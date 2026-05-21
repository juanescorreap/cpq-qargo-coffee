"""Motor de cálculo y persistencia de precios para el CPQ de Qargo Coffee.

Responsabilidades:
- Calcular el precio sugerido a partir del costo de producción + markup.
- Resolver el markup según jerarquía: override explícito → override guardado en
  ``ProductPricing`` → ``CategoryMargin`` de la categoría → default 50 %.
- Persistir los precios en ``ProductPricing`` con auditoría automática en
  ``ProductPriceHistory`` cuando el precio cambia.
- Recalcular masivamente todos los productos activos (operación batch).
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models import (
    CategoryMargin,
    Product,
    ProductPriceHistory,
    ProductPricing,
    ProductSize,
)
from backend.services.cost_calculator import CostCalculator

logger = logging.getLogger("pricing_engine")

_DEFAULT_MARKUP = Decimal("50.0")


class PricingEngine:
    """Motor de cálculo de precios con márgenes para productos de cafetería.

    Encapsula la lógica de:
    - Resolución del markup según jerarquía de prioridad.
    - Cálculo del precio sugerido y redondeado a los 100 COP más cercanos.
    - Persistencia upsert en ``ProductPricing`` con historial automático.
    - Recálculo masivo de todos los productos activos (batch pricing).

    Las instancias son stateless respecto a los resultados calculados y pueden
    reutilizarse entre múltiples llamadas dentro de la misma sesión de BD.

    Attributes:
        db: Sesión SQLAlchemy activa. El llamador es responsable de su
            ciclo de vida (commit / rollback / close).
        cost_calculator: Instancia de :class:`~backend.services.cost_calculator.CostCalculator`
            construida internamente con la misma sesión.
    """

    def __init__(self, db: Session) -> None:
        """Inicializa el motor con una sesión de base de datos.

        Args:
            db: Sesión SQLAlchemy activa (e.g. ``next(get_db())`` en FastAPI,
                o directamente ``SessionLocal()`` en scripts y tareas batch).
        """
        self.db = db
        self.cost_calculator = CostCalculator(db)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def calculate_price(
        self,
        product_id: int,
        size_id: int,
        store_id: Optional[int] = None,
        markup_override: Optional[Decimal] = None,
    ) -> Dict:
        """Calcula el precio sugerido de un producto basado en costo + markup.

        Jerarquía de markup (de mayor a menor prioridad):

        1. ``markup_override`` pasado explícitamente a este método.
        2. ``ProductPricing.markup_override`` del registro vigente más reciente
           en BD para la combinación (product, size, store).
        3. ``CategoryMargin.markup_percentage`` de la categoría del producto.
        4. Default global de 50 %.

        El precio redondeado aproxima el precio sugerido al múltiplo de
        100 COP más cercano, convención habitual de precios en Colombia.

        Args:
            product_id: PK del producto en la tabla ``products``.
            size_id: PK del tamaño (``ProductSize``) a calcular.
            store_id: PK de la tienda. Cuando se especifica, el costo de
                producción usa los precios locales de esa tienda vía
                :class:`CostCalculator`. ``None`` usa los precios base globales.
            markup_override: Porcentaje de markup a aplicar en lugar del
                resuelto automáticamente (e.g. ``Decimal("65.0")`` → 65 %).
                Tiene la prioridad más alta en la jerarquía.

        Returns:
            Diccionario con todos los valores intermedios y finales::

                {
                    'product_id':        int,
                    'size_id':           int,
                    'store_id':          int | None,
                    'cost':              Decimal,   # costo de producción
                    'markup_percentage': Decimal,   # markup aplicado (%)
                    'suggested_price':   Decimal,   # cost × (1 + markup/100)
                    'rounded_price':     Decimal,   # redondeado a 100 COP
                }

        Raises:
            ValueError: Propagado desde :class:`CostCalculator` si el producto
                o tamaño no existen, o si un ingrediente carece de precio.
        """
        cost = self.cost_calculator.calculate_product_cost(
            product_id, size_id, store_id
        )

        markup = self._resolve_markup(product_id, size_id, store_id, markup_override)

        suggested_price = cost * (Decimal("1") + markup / Decimal("100"))
        rounded_price = Decimal(round(suggested_price / Decimal("100")) * 100)

        return {
            "product_id": product_id,
            "size_id": size_id,
            "store_id": store_id,
            "cost": cost,
            "markup_percentage": markup,
            "suggested_price": suggested_price,
            "rounded_price": rounded_price,
        }

    def save_pricing(
        self,
        product_id: int,
        size_id: int,
        store_id: Optional[int],
        final_price: Decimal,
        markup_override: Optional[Decimal] = None,
        is_manual: bool = False,
    ) -> ProductPricing:
        """Guarda o actualiza el precio de un producto en la base de datos.

        Realiza un upsert sobre ``ProductPricing`` usando la combinación
        (product_id, size_id, store_id, effective_date=hoy) como clave
        natural. Si el registro ya existe, lo actualiza; si no, lo crea.

        Cada vez que el precio final cambia respecto al registro anterior,
        inserta automáticamente una fila en ``ProductPriceHistory`` para
        permitir análisis de rentabilidad a lo largo del tiempo. El markup
        registrado en el historial se calcula así:

        - ``markup_override`` si fue provisto.
        - Reverse-engineer ``(final_price / cost − 1) × 100`` en caso
          contrario, útil para registrar precios manuales manteniendo
          la trazabilidad del margen efectivo.

        Args:
            product_id: PK del producto.
            size_id: PK del tamaño.
            store_id: PK de la tienda o ``None`` para precio global que aplica
                a todas las tiendas que no tengan un precio específico.
            final_price: Precio final a persistir (COP).
            markup_override: Porcentaje de markup explícito a guardar en el
                registro. Si ``None``, el campo ``markup_override`` del modelo
                queda en ``NULL`` y el markup del historial se infiere.
            is_manual: ``True`` cuando el precio fue establecido manualmente
                sin respetar la fórmula de markup (e.g. precio promocional).

        Returns:
            Instancia de :class:`~backend.models.ProductPricing` creada o
            actualizada, refrescada desde la base de datos tras el commit.

        Raises:
            ValueError: Propagado desde :class:`CostCalculator` si el producto
                o tamaño no existen.
            ZeroDivisionError: Si el costo calculado es cero y no se provee
                ``markup_override`` (el reverse-engineer del markup requiere
                dividir por el costo).
        """
        cost = self.cost_calculator.calculate_product_cost(
            product_id, size_id, store_id
        )

        if markup_override is not None:
            markup_used = markup_override
        else:
            markup_used = ((final_price / cost) - Decimal("1")) * Decimal("100")

        today = date.today()
        existing = (
            self.db.query(ProductPricing)
            .filter(
                ProductPricing.product_id == product_id,
                ProductPricing.size_id == size_id,
                ProductPricing.store_id == store_id,
                ProductPricing.effective_date == today,
            )
            .first()
        )

        if existing:
            old_price = existing.final_price
            existing.calculated_cost = cost
            existing.markup_override = markup_override
            existing.final_price = final_price
            existing.is_manual_price = is_manual
        else:
            existing = ProductPricing(
                product_id=product_id,
                size_id=size_id,
                store_id=store_id,
                calculated_cost=cost,
                markup_override=markup_override,
                final_price=final_price,
                is_manual_price=is_manual,
                effective_date=today,
            )
            self.db.add(existing)
            old_price = None

        if old_price is None or old_price != final_price:
            history = ProductPriceHistory(
                product_id=product_id,
                size_id=size_id,
                store_id=store_id,
                cost=cost,
                price=final_price,
                markup_used=markup_used,
            )
            self.db.add(history)

        self.db.commit()
        self.db.refresh(existing)
        return existing

    def calculate_all_prices(
        self,
        store_id: Optional[int] = None,
        save_to_db: bool = False,
    ) -> Dict:
        """Calcula (y opcionalmente guarda) los precios de todos los productos activos.

        Itera sobre todos los ``Product`` con ``is_active=True`` y sus
        ``ProductSize`` asociados. Los errores por ítem individual son
        capturados y acumulados en el resultado sin interrumpir el lote,
        de modo que un producto con datos incompletos no bloquea el resto.

        Logging emitido durante la operación:

        - ``INFO`` al inicio con el total de productos encontrados.
        - ``DEBUG`` por cada combinación producto×tamaño calculada con éxito,
          incluyendo costo, markup y precio final.
        - ``WARNING`` por cada combinación que falla con el mensaje de error.
        - ``INFO`` al finalizar con el resumen de éxitos y errores.

        Esta operación puede ser costosa en catálogos grandes; se recomienda
        ejecutarla en un worker de background o tarea programada fuera del
        ciclo de solicitud HTTP.

        Args:
            store_id: PK de la tienda para cálculo de costos con precios
                locales. ``None`` usa los precios base globales de cada
                ingrediente.
            save_to_db: Si ``True``, persiste cada precio calculado en
                ``ProductPricing`` (y genera historial cuando cambia). Si
                ``False``, solo calcula sin escribir — útil para previsualizar
                el impacto de un cambio de costos antes de confirmarlo.

        Returns:
            Resumen de la operación batch::

                {
                    'total_products':    int,         # productos activos encontrados
                    'total_sizes':       int,         # combinaciones producto × tamaño
                    'prices_calculated': int,         # calculadas con éxito
                    'errors':            List[str],   # "<producto> (<tamaño>): <motivo>"
                }
        """
        products = (
            self.db.query(Product).filter(Product.is_active == True).all()
        )

        total_sizes = 0
        prices_calculated = 0
        errors: List[str] = []

        logger.info(
            "Batch pricing started — products=%d store_id=%s save=%s",
            len(products),
            store_id,
            save_to_db,
        )

        for product in products:
            sizes = (
                self.db.query(ProductSize)
                .filter(ProductSize.product_id == product.id)
                .all()
            )

            for size in sizes:
                total_sizes += 1
                label = f"{product.name} ({size.size_name})"

                try:
                    price_data = self.calculate_price(
                        product.id, size.id, store_id
                    )

                    if save_to_db:
                        self.save_pricing(
                            product.id,
                            size.id,
                            store_id,
                            price_data["rounded_price"],
                            is_manual=False,
                        )

                    prices_calculated += 1
                    logger.debug(
                        "  OK %-40s cost=%10s  markup=%5.1f%%  price=%10s",
                        label,
                        price_data["cost"],
                        price_data["markup_percentage"],
                        price_data["rounded_price"],
                    )

                except Exception as exc:
                    error_msg = f"{label}: {exc}"
                    errors.append(error_msg)
                    logger.warning("  FAIL %s", error_msg)

        logger.info(
            "Batch pricing finished — calculated=%d/%d  errors=%d",
            prices_calculated,
            total_sizes,
            len(errors),
        )

        return {
            "total_products": len(products),
            "total_sizes": total_sizes,
            "prices_calculated": prices_calculated,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _resolve_markup(
        self,
        product_id: int,
        size_id: int,
        store_id: Optional[int],
        markup_override: Optional[Decimal],
    ) -> Decimal:
        """Determina el markup a aplicar según jerarquía de prioridad.

        Jerarquía (de mayor a menor):

        1. ``markup_override`` argumento del llamador.
        2. ``ProductPricing.markup_override`` del registro vigente más reciente
           para (product, size, store).
        3. ``CategoryMargin.markup_percentage`` de la categoría del producto.
        4. Default global ``_DEFAULT_MARKUP`` (50 %).

        Extraído como método privado para que tanto :meth:`calculate_price`
        como futuros helpers puedan reutilizarlo sin duplicar la lógica.

        Args:
            product_id: PK del producto.
            size_id: PK del tamaño.
            store_id: PK de la tienda o ``None`` para global.
            markup_override: Override explícito pasado por el llamador externo.

        Returns:
            Markup como porcentaje en ``Decimal`` (e.g. ``Decimal("65.0")``).
        """
        if markup_override is not None:
            return markup_override

        existing_pricing = (
            self.db.query(ProductPricing)
            .filter(
                ProductPricing.product_id == product_id,
                ProductPricing.size_id == size_id,
                ProductPricing.store_id == store_id,
            )
            .order_by(ProductPricing.effective_date.desc())
            .first()
        )

        if existing_pricing and existing_pricing.markup_override is not None:
            return Decimal(str(existing_pricing.markup_override))

        product = (
            self.db.query(Product)
            .filter(Product.id == product_id)
            .first()
        )

        if product and product.category:
            category_margin = (
                self.db.query(CategoryMargin)
                .filter(CategoryMargin.category == product.category)
                .first()
            )
            if category_margin:
                return Decimal(str(category_margin.markup_percentage))

        return _DEFAULT_MARKUP
