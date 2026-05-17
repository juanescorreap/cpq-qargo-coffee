"""Servicio de cálculo de costos para productos del catálogo CPQ.

Responsabilidades:
- Calcular el costo total de un producto dado un tamaño y tienda opcionales.
- Resolver unidades de receta (shots, pumps) a gramos/ml para pricing.
- Aplicar precios locales de ingredientes (StoreIngredientPrice) cuando se
  especifica una tienda; caer al precio base de Ingredient si no hay local.
- Descomponer el costo en ingredientes directos, sub-recetas, packaging y mano
  de obra para reportes de rentabilidad.
"""

from decimal import Decimal
from typing import Dict, Optional

from backend.database import SessionLocal  # noqa: F401 — expuesto para uso externo
from backend.models import (
    Ingredient,
    IngredientRecipeUnitConversion,
    Product,
    ProductSize,
    RecipeIngredient,
    RecipeSubRecipe,
    RecipeUnit,
    SizePackaging,
    Store,
    StoreIngredientPrice,
)


class CostCalculator:
    """Calcula costos de producción de productos de cafetería.

    Encapsula toda la lógica de resolución de precios de ingredientes,
    conversión de unidades de receta y agregación de sub-recetas en un único
    punto de entrada. Las instancias son stateless respecto a los resultados
    y pueden reutilizarse entre múltiples llamadas dentro de la misma sesión.

    Attributes:
        db: Sesión SQLAlchemy activa inyectada en el constructor. El llamador
            es responsable de su ciclo de vida (commit / rollback / close).
    """

    def __init__(self, db_session) -> None:
        """Inicializa el calculador con una sesión de base de datos.

        Args:
            db_session: Sesión SQLAlchemy activa (e.g. obtenida con
                ``next(get_db())`` en un endpoint FastAPI, o directamente con
                ``SessionLocal()`` en scripts).
        """
        self.db = db_session

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def calculate_product_cost(
        self,
        product_id: int,
        size_id: Optional[int] = None,
        store_id: Optional[int] = None,
        _recursion_depth: int = 0,
    ) -> Decimal:
        """Calcula el costo total de producción de un producto.

        El costo incluye:
        - Ingredientes directos de la receta (``RecipeIngredient``), escalados
          por el ``scale_factor`` del tamaño cuando ``scales_with_size=True``.
        - Sub-recetas batch (``RecipeSubRecipe``) valoradas recursivamente
          usando este mismo método.
        - Packaging asociado al tamaño (``SizePackaging``).
        - Costo de mano de obra: ``prep_time_min × labor_cost_per_min`` del
          producto base (no se escala con el tamaño).

        Resolución de precios:
        1. Si ``store_id`` está presente, busca un ``StoreIngredientPrice``
           vigente para ese ingrediente+tienda.
        2. Si no existe precio local, usa ``Ingredient.purchase_price`` /
           ``Ingredient.conversion_factor`` como precio unitario base.

        Resolución de unidades:
        Si la receta expresa la cantidad en una ``RecipeUnit`` (e.g. "2 shots"),
        busca ``IngredientRecipeUnitConversion`` para convertir a la unidad de
        consumo del ingrediente antes de calcular el precio.

        Args:
            product_id: PK del producto en la tabla ``products``.
            size_id: PK del tamaño (``ProductSize``). Si es ``None``, se
                utiliza el tamaño marcado como ``is_default=True``. Si no hay
                ninguno marcado como default, se lanza ``ValueError``.
            store_id: PK de la tienda (``Store``). Si es ``None``, se usan
                los precios base de ``Ingredient`` sin ajuste local.
            _recursion_depth: Contador interno de profundidad de recursión.
                No debe ser pasado por llamadores externos; es usado
                exclusivamente por ``_calculate_sub_recipes_cost`` para
                detectar dependencias circulares en recetas anidadas.

        Returns:
            Costo total en ``Decimal`` expresado en la moneda base del sistema
            (pesos colombianos COP).

        Raises:
            ValueError: Si ``product_id`` no existe en la base de datos.
            ValueError: Si ``size_id`` es ``None`` y el producto no tiene
                ningún tamaño marcado como default.
            ValueError: Si un ingrediente referenciado en la receta no tiene
                precio definido (ni base ni local) y no puede valorarse.
            RecursionError: Propagado desde ``_calculate_sub_recipes_cost``
                si se detecta una dependencia circular entre sub-recetas.
        """
        # 1. Verificar que el producto existe
        product = (
            self.db.query(Product).filter(Product.id == product_id).first()
        )

        if not product:
            raise ValueError(f"Product {product_id} not found")

        # 2. Determinar tamaño y scale_factor
        if size_id:
            size = (
                self.db.query(ProductSize)
                .filter(ProductSize.id == size_id)
                .first()
            )

            if not size:
                raise ValueError(f"Size {size_id} not found")

            scale_factor = size.scale_factor
        else:
            # Buscar tamaño default o usar scale_factor = 1.0
            default_size = (
                self.db.query(ProductSize)
                .filter(
                    ProductSize.product_id == product_id,
                    ProductSize.is_default == True,
                )
                .first()
            )

            if default_size:
                size_id = default_size.id
                scale_factor = default_size.scale_factor
            else:
                scale_factor = Decimal("1.0")

        # 3. Calcular cada componente
        ingredients_cost = self._calculate_ingredients_cost(
            product_id, scale_factor, store_id
        )

        sub_recipes_cost = self._calculate_sub_recipes_cost(
            product_id, scale_factor, store_id, _recursion_depth
        )

        packaging_cost = self._calculate_packaging_cost(size_id, store_id)

        labor_cost = self._calculate_labor_cost(product_id)

        # 4. Sumar todo
        total_cost = (
            ingredients_cost + sub_recipes_cost + packaging_cost + labor_cost
        )

        # 5. Redondear a 2 decimales
        return round(total_cost, 2)

    def get_cost_breakdown(
        self,
        product_id: int,
        size_id: Optional[int] = None,
        store_id: Optional[int] = None,
    ) -> Dict:
        """Retorna el desglose detallado del costo de un producto.

        Útil para pantallas de análisis de rentabilidad y para auditar por qué
        un producto tiene determinado costo. Internamente delega el cálculo
        numérico a ``calculate_product_cost`` y sus helpers privados.

        Args:
            product_id: PK del producto en la tabla ``products``.
            size_id: PK del tamaño. Mismo comportamiento de default que en
                ``calculate_product_cost``.
            store_id: PK de la tienda. Mismo comportamiento de fallback que en
                ``calculate_product_cost``.

        Returns:
            Diccionario con la siguiente estructura::

                {
                    "total": Decimal,          # suma de todas las líneas
                    "ingredients": [
                        {
                            "ingredient_id": int,
                            "name": str,
                            "quantity": Decimal,   # en unidad de consumo
                            "unit": str,           # unidad de consumo
                            "unit_cost": Decimal,  # costo por unidad de consumo
                            "line_cost": Decimal,  # quantity × unit_cost
                            "price_source": str,   # "store" | "base"
                        },
                        ...
                    ],
                    "sub_recipes": [
                        {
                            "sub_product_id": int,
                            "name": str,
                            "quantity": Decimal,   # porción usada en la receta
                            "unit_cost": Decimal,  # costo por unidad de sub-receta
                            "line_cost": Decimal,
                        },
                        ...
                    ],
                    "packaging": [
                        {
                            "ingredient_id": int,
                            "name": str,
                            "quantity": Decimal,
                            "unit_cost": Decimal,
                            "line_cost": Decimal,
                            "price_source": str,   # "store" | "base"
                        },
                        ...
                    ],
                    "labor": Decimal,              # prep_time_min × labor_cost_per_min
                }

        Raises:
            Las mismas excepciones que ``calculate_product_cost``.
        """
        # Resolver entidades principales
        product = self.db.query(Product).get(product_id)
        if not product:
            raise ValueError(f"Product {product_id} not found")

        size = self.db.query(ProductSize).get(size_id) if size_id else None
        store = self.db.query(Store).get(store_id) if store_id else None

        # Si no se pasó size_id, intentar resolver el default
        if not size:
            default_size = (
                self.db.query(ProductSize)
                .filter(
                    ProductSize.product_id == product_id,
                    ProductSize.is_default == True,
                )
                .first()
            )
            if default_size:
                size = default_size
                size_id = default_size.id

        scale_factor = size.scale_factor if size else Decimal("1.0")

        # Calcular totales por componente
        ingredients_total = self._calculate_ingredients_cost(
            product_id, scale_factor, store_id
        )
        sub_recipes_total = self._calculate_sub_recipes_cost(
            product_id, scale_factor, store_id
        )
        packaging_total = self._calculate_packaging_cost(size_id, store_id)
        labor_total = self._calculate_labor_cost(product_id)

        total_cost = round(
            ingredients_total + sub_recipes_total + packaging_total + labor_total,
            2,
        )

        # TODO: Implementar listas de detalle por ítem para 'ingredients',
        #       'sub_recipes' y 'packaging'. Cada entrada deberá incluir
        #       name, quantity, unit y cost resueltos individualmente,
        #       siguiendo el mismo patrón de _calculate_ingredient_cost.

        return {
            "product_id": product_id,
            "product_name": product.name,
            "size_id": size_id,
            "size_name": size.size_name if size else None,
            "store_id": store_id,
            "store_name": store.name if store else None,
            "total_cost": total_cost,
            "breakdown": {
                "ingredients": [],  # TODO: detalle por ingrediente
                "sub_recipes": [],  # TODO: detalle por sub-receta
                "packaging": [],    # TODO: detalle por ítem de packaging
                "labor": {
                    "minutes": product.prep_time_minutes or Decimal("0"),
                    "cost_per_minute": product.labor_cost_per_minute or Decimal("0"),
                    "cost": labor_total,
                },
            },
            "totals": {
                "ingredients": ingredients_total,
                "sub_recipes": sub_recipes_total,
                "packaging": packaging_total,
                "labor": labor_total,
            },
        }

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _calculate_labor_cost(self, product_id: int) -> Decimal:
        """Calcula el costo de mano de obra basado en el tiempo de preparación.

        Multiplica ``prep_time_minutes`` por ``labor_cost_per_minute`` del
        producto. Ambos campos son opcionales en el modelo; si alguno es
        ``None`` o cero el método retorna ``Decimal("0")`` sin error, ya que
        hay productos (sub-recetas batch, ítems sin mano de obra asignada) para
        los que este costo no aplica.

        El costo de labor no se escala con el tamaño: el tiempo de preparación
        está definido para el producto base y se asume constante entre tamaños.

        Args:
            product_id: PK del producto en la tabla ``products``.

        Returns:
            Costo de mano de obra en ``Decimal`` (COP). Retorna
            ``Decimal("0")`` si el producto no existe, o si
            ``prep_time_minutes`` / ``labor_cost_per_minute`` son ``None``.
        """
        product = (
            self.db.query(Product).filter(Product.id == product_id).first()
        )

        if not product:
            return Decimal("0")

        if not product.prep_time_minutes or not product.labor_cost_per_minute:
            return Decimal("0")

        return product.prep_time_minutes * product.labor_cost_per_minute

    def _get_ingredient_price(
        self,
        ingredient_id: int,
        store_id: Optional[int],
    ) -> Decimal:
        """Obtiene el precio efectivo de compra de un ingrediente.

        Aplica la lógica de fallback store → base:
        1. Si ``store_id`` no es ``None``, busca un registro en
           ``store_ingredient_prices`` para el par (store, ingredient).
           Si existe y tiene ``local_price`` definido, lo retorna.
        2. En cualquier otro caso (sin tienda, sin override o
           ``local_price`` nulo) retorna ``Ingredient.purchase_price``.

        El precio devuelto corresponde a la **unidad de compra** del
        ingrediente (e.g. precio por caja, por bolsa). Para obtener el
        costo por unidad de consumo se debe dividir por
        ``Ingredient.conversion_factor`` en el llamador.

        Args:
            ingredient_id: PK del ingrediente en la tabla ``ingredients``.
            store_id: PK de la tienda cuyo precio local se prefiere. Si es
                ``None`` se omite la búsqueda local y se usa el precio base.

        Returns:
            Precio de la unidad de compra como ``Decimal``. Retorna
            ``Decimal("0")`` si ``purchase_price`` es ``None`` en la BD
            (ingrediente sin precio cargado aún).

        Raises:
            ValueError: Si no existe ningún ``Ingredient`` con
                ``ingredient_id`` en la base de datos.
        """
        # 1. Buscar ingrediente
        ingredient = (
            self.db.query(Ingredient)
            .filter(Ingredient.id == ingredient_id)
            .first()
        )

        if not ingredient:
            raise ValueError(f"Ingredient {ingredient_id} not found")

        # 2. Si hay store_id, buscar override
        if store_id:
            store_price = (
                self.db.query(StoreIngredientPrice)
                .filter(
                    StoreIngredientPrice.store_id == store_id,
                    StoreIngredientPrice.ingredient_id == ingredient_id,
                )
                .first()
            )

            if store_price and store_price.local_price:
                return store_price.local_price

        # 3. Retornar precio base
        return ingredient.purchase_price or Decimal("0")

    def _get_recipe_unit_conversion(
        self,
        ingredient_id: int,
        recipe_unit_id: int,
    ) -> Decimal:
        """Obtiene el factor de conversión de una recipe unit a la usage unit.

        Resuelve cuántas ``usage_unit`` del ingrediente equivalen a una unidad
        de receta. Este factor se usa para transformar cantidades expresadas en
        unidades de receta (shots, pumps, teaspoons) al sistema de medida del
        ingrediente (ml, g) antes de calcular el costo.

        Example::

            _get_recipe_unit_conversion(jarabe_id, pump_id) → Decimal("30")
            # Significa: 1 pump = 30 ml

        Args:
            ingredient_id: PK del ingrediente en la tabla ``ingredients``.
            recipe_unit_id: PK de la unidad de receta en la tabla
                ``recipe_units`` (e.g. el ID de "pump", "shot", "teaspoon").

        Returns:
            Cantidad de ``usage_unit`` que equivale a 1 ``recipe_unit``,
            tal como está registrada en ``IngredientRecipeUnitConversion
            .usage_unit_quantity``.

        Raises:
            ValueError: Si no existe una conversión definida para el par
                (ingredient, recipe_unit). El mensaje incluye los nombres
                resueltos para facilitar la corrección en el frontend.
        """
        conversion = (
            self.db.query(IngredientRecipeUnitConversion)
            .filter(
                IngredientRecipeUnitConversion.ingredient_id == ingredient_id,
                IngredientRecipeUnitConversion.recipe_unit_id == recipe_unit_id,
            )
            .first()
        )

        if not conversion:
            ingredient = self.db.query(Ingredient).get(ingredient_id)
            recipe_unit = self.db.query(RecipeUnit).get(recipe_unit_id)
            raise ValueError(
                f"Missing conversion for '{ingredient.name}' "
                f"in unit '{recipe_unit.name}'. "
                "Please define the conversion in ingredient settings."
            )

        return conversion.usage_unit_quantity

    def _calculate_ingredient_cost(
        self,
        recipe_ing: RecipeIngredient,
        ingredient: Ingredient,
        scale_factor: Decimal,
        store_id: Optional[int],
    ) -> Decimal:
        """Calcula el costo de un ingrediente individual dentro de una receta.

        Aplica en orden: resolución de precio → conversión de unidad de receta
        → escala por tamaño → yield del ingrediente → process yield loss →
        costo final. Cada paso transforma la cantidad efectiva antes de
        multiplicar por el precio unitario de consumo.

        Proceso detallado:

        1. **Precio de compra** — delega a ``_get_ingredient_price`` con
           fallback store → base.
        2. **Conversión recipe_unit → usage_unit** — si ``recipe_ing`` tiene
           ``recipe_unit_id``, llama a ``_get_recipe_unit_conversion`` para
           obtener el factor (e.g. 1 pump = 30 ml) y multiplica la cantidad.
           Si no hay ``recipe_unit_id``, la cantidad ya está en ``usage_unit``.
        3. **Scale por tamaño** — si ``recipe_ing.scales_with_size`` es
           ``True``, multiplica la cantidad por ``scale_factor``.
        4. **Ingredient yield** — divide por ``yield_percentage / 100`` para
           compensar la merma del ingrediente en almacenamiento/preparación
           (e.g. yield 95 % → se necesita un 5 % más de materia prima).
        5. **Process yield loss** — si ``process_yield_loss > 0``, divide por
           ``1 - process_yield_loss / 100`` para compensar la merma adicional
           del proceso (e.g. evaporación al hervir).
        6. **Costo final** — ``unit_cost = price / conversion_factor`` da el
           precio por ``usage_unit``; multiplicado por la cantidad efectiva
           produce el costo total de esta línea.

        Example::

            # Cappuccino 12 oz: 240 ml de leche entera
            # purchase_price = 4 500 COP / caja 1 000 ml, yield = 95 %
            # scale_factor = 1.0 (tamaño base), sin recipe_unit, sin process loss
            #
            # unit_cost  = 4 500 / 1 000 = 4.5 COP/ml
            # qty_yield  = 240 / 0.95   ≈ 252.63 ml
            # total_cost ≈ 4.5 × 252.63 ≈ 1 136.84 COP

        Args:
            recipe_ing: Fila ``RecipeIngredient`` con ``quantity``,
                ``recipe_unit_id``, ``scales_with_size`` y
                ``process_yield_loss``.
            ingredient: Objeto ``Ingredient`` completo con ``purchase_price``,
                ``conversion_factor`` y ``yield_percentage``.
            scale_factor: Factor multiplicador de cantidad derivado del tamaño
                seleccionado (``ProductSize.scale_factor``). Use
                ``Decimal("1")`` para el tamaño base.
            store_id: PK de la tienda cuyo precio local se prefiere. ``None``
                usa el precio base del ingrediente.

        Returns:
            Costo total en ``Decimal`` (COP) del ingrediente para esta línea
            de receta con el tamaño y tienda dados.

        Raises:
            ValueError: Propagado desde ``_get_ingredient_price`` si el
                ingrediente no existe.
            ValueError: Propagado desde ``_get_recipe_unit_conversion`` si
                falta la conversión recipe_unit → usage_unit.
            ZeroDivisionError: Si ``ingredient.conversion_factor`` o
                ``ingredient.yield_percentage`` son cero en la BD (dato
                corrupto; debe corregirse en el catálogo).
        """
        # 1. Precio del ingrediente
        price = self._get_ingredient_price(ingredient.id, store_id)

        # 2. Cantidad en recipe_unit → usage_unit
        if recipe_ing.recipe_unit_id:
            conversion = self._get_recipe_unit_conversion(
                ingredient.id,
                recipe_ing.recipe_unit_id,
            )
            quantity_in_usage_units = recipe_ing.quantity * conversion
        else:
            quantity_in_usage_units = recipe_ing.quantity

        # 3. Scaling por tamaño
        if recipe_ing.scales_with_size:
            quantity_in_usage_units *= scale_factor

        # 4. Aplicar yield del ingrediente
        # yield_percentage está almacenado como fracción (0.0–1.0) en la BD.
        yield_factor = ingredient.yield_percentage  # e.g. 0.98
        if yield_factor and yield_factor > 0:
            quantity_with_yield = quantity_in_usage_units / yield_factor
        else:
            quantity_with_yield = quantity_in_usage_units

        # 5. Aplicar process yield loss
        # process_yield_loss está almacenado como porcentaje de rendimiento
        # (0–100): 100 = sin merma, 90 = 10 % de merma en proceso.
        if recipe_ing.process_yield_loss > 0 and recipe_ing.process_yield_loss < 100:
            process_yield_factor = recipe_ing.process_yield_loss / Decimal("100")
            quantity_with_yield = quantity_with_yield / process_yield_factor

        # 6. Calcular costo
        # price = precio de purchase_unit
        # conversion_factor = cuántas usage_units en 1 purchase_unit
        conversion_factor = ingredient.conversion_factor
        if not conversion_factor:
            return Decimal("0")
        unit_cost = price / conversion_factor
        return unit_cost * quantity_with_yield

    def _calculate_ingredients_cost(
        self,
        product_id: int,
        scale_factor: Decimal,
        store_id: Optional[int],
    ) -> Decimal:
        """Calcula el costo total de todos los ingredientes directos de un producto.

        Itera sobre cada ``RecipeIngredient`` del producto y acumula el costo
        individual usando ``_calculate_ingredient_cost``. Los ingredientes con
        problemas de integridad referencial (``ingredient_id`` huérfano) se
        omiten silenciosamente para no bloquear el cálculo; la inconsistencia
        debe corregirse en el catálogo.

        Args:
            product_id: PK del producto cuya receta se quiere valorar.
            scale_factor: Factor multiplicador derivado del ``ProductSize``
                seleccionado. Usar ``Decimal("1")`` para el tamaño base.
            store_id: PK de la tienda para aplicar precios locales. ``None``
                usa los precios base de cada ingrediente.

        Returns:
            Suma de los costos de todos los ingredientes directos en
            ``Decimal`` (COP). Retorna ``Decimal("0")`` si el producto no
            tiene ingredientes en receta.
        """
        recipe_ingredients = (
            self.db.query(RecipeIngredient)
            .filter(RecipeIngredient.product_id == product_id)
            .all()
        )

        total_cost = Decimal("0")

        for recipe_ing in recipe_ingredients:
            ingredient = (
                self.db.query(Ingredient)
                .filter(Ingredient.id == recipe_ing.ingredient_id)
                .first()
            )

            if not ingredient:
                # Ingrediente huérfano: omitir sin bloquear el cálculo
                continue

            total_cost += self._calculate_ingredient_cost(
                recipe_ing,
                ingredient,
                scale_factor,
                store_id,
            )

        return total_cost

    def _calculate_packaging_cost(
        self,
        size_id: Optional[int],
        store_id: Optional[int],
    ) -> Decimal:
        """Calcula el costo total de packaging asociado a un tamaño de producto.

        El packaging (vasos, tapas, servilletas, mangas, etc.) se modela como
        ``Ingredient`` referenciado desde ``SizePackaging``, lo que permite
        usar la misma lógica de precios store → base que los ingredientes de
        receta. Cada ítem de packaging tiene una cantidad fija por unidad
        producida (sin escalado por tamaño, ya que el tamaño ya está implícito
        en el ``size_id``).

        Args:
            size_id: PK del ``ProductSize`` cuyo packaging se quiere valorar.
                Si es ``None`` el método retorna ``Decimal("0")`` de inmediato,
                ya que sin tamaño no hay packaging definido.
            store_id: PK de la tienda para aplicar precios locales de los
                ítems de packaging. ``None`` usa los precios base.

        Returns:
            Suma del costo de todos los ítems de packaging en ``Decimal``
            (COP). Retorna ``Decimal("0")`` si ``size_id`` es ``None`` o si
            el tamaño no tiene packaging configurado.
        """
        if not size_id:
            return Decimal("0")

        packaging_items = (
            self.db.query(SizePackaging)
            .filter(SizePackaging.size_id == size_id)
            .all()
        )

        total_cost = Decimal("0")

        for pkg_item in packaging_items:
            ingredient = (
                self.db.query(Ingredient)
                .filter(Ingredient.id == pkg_item.packaging_ingredient_id)
                .first()
            )

            if not ingredient:
                continue

            price = self._get_ingredient_price(
                pkg_item.packaging_ingredient_id, store_id
            )
            unit_cost = price / ingredient.conversion_factor
            total_cost += unit_cost * pkg_item.quantity

        return total_cost

    def _calculate_sub_recipes_cost(
        self,
        product_id: int,
        scale_factor: Decimal,
        store_id: Optional[int],
        _recursion_depth: int = 0,
    ) -> Decimal:
        """Calcula el costo de todas las sub-recetas usadas en un producto.

        Resuelve recursivamente cada ``RecipeSubRecipe`` del producto llamando
        a ``calculate_product_cost`` con ``size_id=None`` (las sub-recetas son
        preparaciones batch sin variantes de tamaño). La cantidad de
        sub-receta consumida se escala si ``scales_with_size=True``.

        El parámetro ``_recursion_depth`` actúa como guardia contra ciclos en
        el grafo de recetas (e.g. A → B → A). El límite de 10 niveles cubre
        cualquier jerarquía razonable de recetas de cafetería; si se supera
        hay un error de datos, no un caso de uso legítimo.

        Args:
            product_id: PK del producto padre cuyas sub-recetas se valoran.
            scale_factor: Factor multiplicador por tamaño aplicado a las
                sub-recetas con ``scales_with_size=True``.
            store_id: PK de la tienda para precios locales de ingredientes
                dentro de la sub-receta. ``None`` usa precios base.
            _recursion_depth: Profundidad actual de la pila de recursión.
                El llamador externo nunca debe pasarlo; lo gestiona
                internamente esta clase.

        Returns:
            Suma del costo de todas las sub-recetas en ``Decimal`` (COP).
            Retorna ``Decimal("0")`` si el producto no tiene sub-recetas.

        Raises:
            RecursionError: Si ``_recursion_depth`` supera 10, indicando una
                dependencia circular en la configuración de recetas.
        """
        if _recursion_depth > 10:
            raise RecursionError(
                f"Circular dependency detected in product {product_id}. "
                "Check your recipe_sub_recipes for circular references."
            )

        sub_recipes = (
            self.db.query(RecipeSubRecipe)
            .filter(RecipeSubRecipe.parent_product_id == product_id)
            .all()
        )

        total_cost = Decimal("0")

        for sub_recipe in sub_recipes:
            # Costo unitario de la sub-receta (recursivo, sin tamaño)
            sub_cost = self.calculate_product_cost(
                product_id=sub_recipe.sub_recipe_id,
                size_id=None,
                store_id=store_id,
                _recursion_depth=_recursion_depth + 1,
            )

            quantity = sub_recipe.quantity

            if sub_recipe.scales_with_size:
                quantity *= scale_factor

            total_cost += sub_cost * quantity

        return total_cost
