"""Generador de reportes de análisis para el CPQ de Qargo Coffee.

Responsabilidades:
- Costos por producto con desglose por componente (ingredientes, sub-recetas,
  packaging, mano de obra) para cada tamaño activo.
- Análisis de márgenes: clasifica pricings en negativo, bajo, sano y alto.
- Benchmark de precios propios versus competidores con match establecido.
- Simulación del impacto en costos de un cambio porcentual en un ingrediente.
"""

import logging
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models import (
    Competitor,
    CompetitorProduct,
    Ingredient,
    Product,
    ProductCompetitorMatch,
    ProductPricing,
    ProductSize,
    RecipeIngredient,
)
from backend.services.cost_calculator import CostCalculator
from backend.services.pricing_engine import PricingEngine

logger = logging.getLogger("report_generator")


class ReportGenerator:
    """Generador de reportes de análisis de costos, márgenes y competencia.

    Encapsula la lógica de cuatro tipos de reporte:

    1. **Costos por producto**: costo total + desglose por componente para
       cada tamaño activo, con soporte de precios locales por tienda.
    2. **Análisis de márgenes**: clasifica todos los pricings vigentes en
       cuatro rangos (negativo, bajo < 30 %, sano 30–80 %, alto > 80 %).
    3. **Benchmark competidores**: diferencia de precio para cada match
       establecido en ``ProductCompetitorMatch``.
    4. **Simulación de impacto**: proyecta cómo un cambio porcentual en el
       precio de un ingrediente afecta el costo de los productos que lo usan,
       sin escribir datos en la BD.

    Todos los métodos capturan errores por ítem individual para que un dato
    incompleto no interrumpa el resto del informe.

    Attributes:
        db: Sesión SQLAlchemy activa. El llamador es responsable de su
            ciclo de vida (commit / rollback / close).
        cost_calculator: Instancia de :class:`~backend.services.cost_calculator.CostCalculator`
            construida con la misma sesión.
        pricing_engine: Instancia de :class:`~backend.services.pricing_engine.PricingEngine`
            construida con la misma sesión.
    """

    def __init__(self, db: Session) -> None:
        """Inicializa el generador con una sesión de base de datos.

        Args:
            db: Sesión SQLAlchemy activa (e.g. ``next(get_db())`` en FastAPI).
        """
        self.db = db
        self.cost_calculator = CostCalculator(db)
        self.pricing_engine = PricingEngine(db)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def product_costs_report(
        self,
        store_id: Optional[int] = None,
    ) -> List[Dict]:
        """Reporte de costos por producto con desglose por componente.

        Itera sobre todos los productos activos y calcula el costo de
        producción para cada uno de sus tamaños. Los productos o tamaños
        que generen error (ingrediente sin precio, receta vacía, etc.) son
        omitidos y registrados en el log como WARNING sin interrumpir el lote.

        Args:
            store_id: PK de la tienda para usar precios locales de
                ingredientes (``StoreIngredientPrice``). ``None`` usa los
                precios base globales de cada ingrediente.

        Returns:
            Lista ordenada por nombre de producto::

                [
                    {
                        'product_id':   int,
                        'product_name': str,
                        'category':     str | None,
                        'sizes': [
                            {
                                'size_name': str,
                                'cost':      Decimal,
                                'cost_breakdown': {
                                    'ingredients': Decimal,
                                    'sub_recipes': Decimal,
                                    'packaging':   Decimal,
                                    'labor':       Decimal,
                                }
                            },
                            ...
                        ]
                    },
                    ...
                ]
        """
        products = (
            self.db.query(Product)
            .filter(Product.is_active == True)
            .order_by(Product.name)
            .all()
        )

        report: List[Dict] = []

        for product in products:
            sizes = (
                self.db.query(ProductSize)
                .filter(ProductSize.product_id == product.id)
                .order_by(ProductSize.scale_factor)
                .all()
            )

            sizes_data: List[Dict] = []
            for size in sizes:
                try:
                    cost = self.cost_calculator.calculate_product_cost(
                        product.id, size.id, store_id
                    )
                    breakdown = self.cost_calculator.get_cost_breakdown(
                        product.id, size.id, store_id
                    )
                    sizes_data.append({
                        "size_name":      size.size_name,
                        "cost":           cost,
                        "cost_breakdown": breakdown.get("totals", {}),
                    })
                except Exception as exc:
                    logger.warning(
                        "product_costs_report: skipping %s (%s) — %s",
                        product.name, size.size_name, exc,
                    )

            report.append({
                "product_id":   product.id,
                "product_name": product.name,
                "category":     product.category,
                "sizes":        sizes_data,
            })

        return report

    def margin_analysis_report(self) -> Dict:
        """Reporte de análisis de márgenes sobre todos los pricings vigentes.

        Clasifica cada registro de ``ProductPricing`` según el margen bruto
        calculado como ``(precio − costo) / precio × 100``:

        - **negative_margin**: margen < 0 % (precio por debajo del costo).
        - **low_margin**: 0 % ≤ margen < 30 %.
        - **healthy_margin**: 30 % ≤ margen ≤ 80 %.
        - **high_margin**: margen > 80 %.

        Los registros con ``final_price`` o ``calculated_cost`` nulos, cero
        o negativos son omitidos. Los errores al resolver producto/tamaño
        en la BD son capturados y registrados como WARNING.

        Returns:
            Diccionario con cuatro listas. Cada ítem tiene la estructura::

                {
                    'product_name': str,
                    'size_name':    str,
                    'cost':         float,
                    'price':        float,
                    'margin_pct':   float,
                }

            ``negative_margin`` y ``low_margin`` están ordenadas de menor a
            mayor margen; ``high_margin`` de mayor a menor.
        """
        all_pricing = self.db.query(ProductPricing).all()

        negative: List[Dict] = []
        low:      List[Dict] = []
        healthy:  List[Dict] = []
        high:     List[Dict] = []

        for pricing in all_pricing:
            if not pricing.final_price or not pricing.calculated_cost:
                continue
            if float(pricing.final_price) <= 0 or float(pricing.calculated_cost) <= 0:
                continue

            try:
                price = float(pricing.final_price)
                cost  = float(pricing.calculated_cost)
                margin_pct = (price - cost) / price * 100

                product = self.db.get(Product, pricing.product_id)
                size    = self.db.get(ProductSize, pricing.size_id)

                if not product or not size:
                    logger.warning(
                        "margin_analysis_report: missing product/size for pricing %d",
                        pricing.id,
                    )
                    continue

                item = {
                    "product_name": product.name,
                    "size_name":    size.size_name,
                    "cost":         cost,
                    "price":        price,
                    "margin_pct":   round(margin_pct, 2),
                }

                if margin_pct < 0:
                    negative.append(item)
                elif margin_pct < 30:
                    low.append(item)
                elif margin_pct <= 80:
                    healthy.append(item)
                else:
                    high.append(item)

            except Exception as exc:
                logger.warning(
                    "margin_analysis_report: error on pricing %d — %s",
                    pricing.id, exc,
                )

        return {
            "negative_margin": sorted(negative, key=lambda x: x["margin_pct"]),
            "low_margin":      sorted(low,      key=lambda x: x["margin_pct"]),
            "healthy_margin":  healthy,
            "high_margin":     sorted(high, key=lambda x: x["margin_pct"], reverse=True),
        }

    def competitor_benchmark_report(self) -> List[Dict]:
        """Reporte de comparación de precios propios contra competencia.

        Solo incluye los productos para los que existe un match explícito en
        ``ProductCompetitorMatch`` *y* un precio vigente en ``ProductPricing``
        con ``store_id IS NULL`` (precio global). Si el match no tiene
        pricing asociado se omite silenciosamente.

        Los errores al resolver entidades relacionadas (producto, tamaño,
        competidor) son capturados y registrados como WARNING.

        Returns:
            Lista ordenada por diferencia porcentual de precio descendente
            (los más caros respecto a la competencia aparecen primero)::

                [
                    {
                        'our_product':          str,
                        'our_size':             str,
                        'our_price':            float,
                        'competitor':           str,
                        'competitor_product':   str,
                        'competitor_price':     float,
                        'price_difference':     float,   # nuestro − competidor (COP)
                        'price_difference_pct': float,   # diferencia / competidor × 100
                    },
                    ...
                ]
        """
        matches = self.db.query(ProductCompetitorMatch).all()
        report: List[Dict] = []

        for match in matches:
            try:
                our_product  = self.db.get(Product, match.our_product_id)
                our_size     = self.db.get(ProductSize, match.our_size_id)
                comp_product = self.db.get(CompetitorProduct, match.competitor_product_id)

                if not our_product or not our_size or not comp_product:
                    logger.warning(
                        "competitor_benchmark_report: missing entity for match %d",
                        match.id,
                    )
                    continue

                competitor = self.db.get(Competitor, comp_product.competitor_id)
                if not competitor:
                    logger.warning(
                        "competitor_benchmark_report: competitor %d not found (match %d)",
                        comp_product.competitor_id, match.id,
                    )
                    continue

                our_pricing = (
                    self.db.query(ProductPricing)
                    .filter(
                        ProductPricing.product_id == match.our_product_id,
                        ProductPricing.size_id    == match.our_size_id,
                        ProductPricing.store_id.is_(None),
                    )
                    .order_by(ProductPricing.effective_date.desc())
                    .first()
                )

                if not our_pricing:
                    continue

                if not comp_product.price or float(comp_product.price) <= 0:
                    continue

                our_price  = float(our_pricing.final_price)
                comp_price = float(comp_product.price)
                price_diff     = our_price - comp_price
                price_diff_pct = price_diff / comp_price * 100

                report.append({
                    "our_product":          our_product.name,
                    "our_size":             our_size.size_name,
                    "our_price":            our_price,
                    "competitor":           competitor.name,
                    "competitor_product":   comp_product.product_name,
                    "competitor_price":     comp_price,
                    "price_difference":     round(price_diff, 2),
                    "price_difference_pct": round(price_diff_pct, 2),
                })

            except Exception as exc:
                logger.warning(
                    "competitor_benchmark_report: error on match %d — %s",
                    match.id, exc,
                )

        return sorted(report, key=lambda x: x["price_difference_pct"], reverse=True)

    def price_impact_simulation(
        self,
        ingredient_id: int,
        percent_change: Decimal,
    ) -> Dict:
        """Simula el impacto en costos de un cambio porcentual en el precio de un ingrediente.

        Calcula el costo actual y proyectado de cada combinación
        producto × tamaño que usa el ingrediente, **sin escribir ningún dato
        en la BD**. La modificación temporal del precio del ingrediente se
        realiza dentro de un bloque ``no_autoflush`` para garantizar que
        SQLAlchemy no propague el valor temporal al servidor de BD.

        Args:
            ingredient_id: PK del ingrediente cuyo precio se simula.
            percent_change: Porcentaje de variación. Positivo = incremento,
                negativo = reducción. Ejemplo: ``Decimal("10")`` → +10 %,
                ``Decimal("-5")`` → −5 %.

        Returns:
            En caso de éxito, diccionario con los resultados::

                {
                    'ingredient':      str,
                    'current_price':   Decimal,
                    'new_price':       Decimal,
                    'percent_change':  Decimal,
                    'affected_products': [
                        {
                            'product':           str,
                            'size':              str,
                            'current_cost':      Decimal,
                            'new_cost':          Decimal,
                            'cost_increase':     Decimal,   # puede ser negativo
                            'cost_increase_pct': Decimal,
                        },
                        ...
                    ]
                }

            La lista ``affected_products`` está ordenada de mayor a menor
            impacto (``cost_increase_pct`` descendente). Los productos o
            tamaños que generen error se omiten y se registran como WARNING.

            En caso de error, retorna ``{'error': str}``.

        Raises:
            No lanza excepciones hacia el llamador; los errores se convierten
            en la clave ``'error'`` del dict o en entradas omitidas con WARNING.
        """
        ingredient = self.db.get(Ingredient, ingredient_id)
        if not ingredient:
            return {"error": f"Ingredient {ingredient_id} not found"}

        if not ingredient.purchase_price or float(ingredient.purchase_price) <= 0:
            return {
                "error": (
                    f"Ingredient '{ingredient.name}' has no valid purchase price "
                    "and cannot be used in a simulation"
                )
            }

        current_price = Decimal(str(ingredient.purchase_price))
        new_price = current_price * (Decimal("1") + percent_change / Decimal("100"))

        recipe_ings = (
            self.db.query(RecipeIngredient)
            .filter(RecipeIngredient.ingredient_id == ingredient_id)
            .all()
        )

        if not recipe_ings:
            return {
                "ingredient":        ingredient.name,
                "current_price":     current_price,
                "new_price":         new_price,
                "percent_change":    percent_change,
                "affected_products": [],
            }

        product_ids = {r.product_id for r in recipe_ings}
        affected: List[Dict] = []

        for product_id in product_ids:
            product = self.db.get(Product, product_id)
            if not product:
                logger.warning(
                    "price_impact_simulation: product %d not found, skipping",
                    product_id,
                )
                continue

            sizes = (
                self.db.query(ProductSize)
                .filter(ProductSize.product_id == product_id)
                .order_by(ProductSize.scale_factor)
                .all()
            )

            for size in sizes:
                try:
                    current_cost = self.cost_calculator.calculate_product_cost(
                        product.id, size.id, None
                    )

                    with self.db.no_autoflush:
                        original_price = ingredient.purchase_price
                        ingredient.purchase_price = new_price
                        new_cost = self.cost_calculator.calculate_product_cost(
                            product.id, size.id, None
                        )
                        ingredient.purchase_price = original_price

                    cost_increase = new_cost - current_cost
                    cost_increase_pct = (
                        cost_increase / current_cost * Decimal("100")
                        if current_cost > 0
                        else Decimal("0")
                    )

                    affected.append({
                        "product":           product.name,
                        "size":              size.size_name,
                        "current_cost":      current_cost,
                        "new_cost":          new_cost,
                        "cost_increase":     cost_increase,
                        "cost_increase_pct": round(cost_increase_pct, 4),
                    })

                except Exception as exc:
                    logger.warning(
                        "price_impact_simulation: error on %s (%s) — %s",
                        product.name, size.size_name, exc,
                    )

        return {
            "ingredient":        ingredient.name,
            "current_price":     current_price,
            "new_price":         new_price,
            "percent_change":    percent_change,
            "affected_products": sorted(
                affected,
                key=lambda x: x["cost_increase_pct"],
                reverse=True,
            ),
        }
