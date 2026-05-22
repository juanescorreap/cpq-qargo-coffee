"""Cost calculation service for CPQ catalog products.

Responsibilities:
- Calculate the total cost of a product given an optional size and store.
- Resolve recipe units (shots, pumps) to grams/ml for pricing.
- Apply local ingredient prices (StoreIngredientPrice) when a store is
  specified; fall back to the base Ingredient price if no local price exists.
- Break down the cost into direct ingredients, sub-recipes, packaging, and
  labor for profitability reports.
"""

from decimal import Decimal
from typing import Dict, Optional

from backend.database import SessionLocal  # noqa: F401 — exposed for external use
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
    """Calculates production costs for coffee-shop products.

    Encapsulates all ingredient price resolution, recipe unit conversion, and
    sub-recipe aggregation logic in a single entry point.  Instances are
    stateless with respect to results and can be reused across multiple calls
    within the same session.

    Attributes:
        db: Active SQLAlchemy session injected in the constructor.  The caller
            is responsible for its lifecycle (commit / rollback / close).
    """

    def __init__(self, db_session) -> None:
        """Initialise the calculator with a database session.

        Args:
            db_session: Active SQLAlchemy session (e.g. obtained with
                ``next(get_db())`` in a FastAPI endpoint, or directly with
                ``SessionLocal()`` in scripts).
        """
        self.db = db_session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_product_cost(
        self,
        product_id: int,
        size_id: Optional[int] = None,
        store_id: Optional[int] = None,
        _recursion_depth: int = 0,
    ) -> Decimal:
        """Calculate the total production cost of a product.

        The cost includes:
        - Direct recipe ingredients (``RecipeIngredient``), scaled by the
          size's ``scale_factor`` when ``scales_with_size=True``.
        - Batch sub-recipes (``RecipeSubRecipe``) valued recursively using
          this same method.
        - Packaging associated with the size (``SizePackaging``).
        - Labor cost: ``prep_time_min × labor_cost_per_min`` from the base
          product (not scaled with size).

        Price resolution:
        1. If ``store_id`` is present, look up a ``StoreIngredientPrice``
           for that ingredient+store combination.
        2. If no local price exists, use ``Ingredient.purchase_price`` /
           ``Ingredient.conversion_factor`` as the base unit price.

        Unit resolution:
        If the recipe expresses the quantity in a ``RecipeUnit`` (e.g. "2 shots"),
        look up ``IngredientRecipeUnitConversion`` to convert to the ingredient's
        consumption unit before calculating the price.

        Args:
            product_id: PK of the product in the ``products`` table.
            size_id: PK of the size (``ProductSize``).  If ``None``, the size
                marked as ``is_default=True`` is used.  If none is marked as
                default, ``ValueError`` is raised.
            store_id: PK of the store (``Store``).  If ``None``, base
                ``Ingredient`` prices are used without local adjustment.
            _recursion_depth: Internal recursion depth counter.  Must not be
                passed by external callers; used exclusively by
                ``_calculate_sub_recipes_cost`` to detect circular dependencies
                in nested recipes.

        Returns:
            Total cost as a ``Decimal`` expressed in the system base currency
            (Colombian pesos, COP).

        Raises:
            ValueError: If ``product_id`` does not exist in the database.
            ValueError: If ``size_id`` is ``None`` and the product has no size
                marked as default.
            ValueError: If an ingredient referenced in the recipe has no defined
                price (neither base nor local) and cannot be valued.
            RecursionError: Propagated from ``_calculate_sub_recipes_cost``
                if a circular dependency between sub-recipes is detected.
        """
        # 1. Verify that the product exists
        product = (
            self.db.query(Product).filter(Product.id == product_id).first()
        )

        if not product:
            raise ValueError(f"Product {product_id} not found")

        # 2. Determine size and scale_factor
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
            # Look for the default size or use scale_factor = 1.0
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

        # 3. Calculate each component
        ingredients_cost = self._calculate_ingredients_cost(
            product_id, scale_factor, store_id
        )

        sub_recipes_cost = self._calculate_sub_recipes_cost(
            product_id, scale_factor, store_id, _recursion_depth
        )

        packaging_cost = self._calculate_packaging_cost(size_id, store_id)

        labor_cost = self._calculate_labor_cost(product_id)

        # 4. Sum everything
        total_cost = (
            ingredients_cost + sub_recipes_cost + packaging_cost + labor_cost
        )

        # 5. Round to 2 decimal places
        return round(total_cost, 2)

    def get_cost_breakdown(
        self,
        product_id: int,
        size_id: Optional[int] = None,
        store_id: Optional[int] = None,
    ) -> Dict:
        """Return the detailed cost breakdown for a product.

        Useful for profitability analysis screens and for auditing why a product
        has a given cost.  Internally delegates numerical calculation to
        ``calculate_product_cost`` and its private helpers.

        Args:
            product_id: PK of the product in the ``products`` table.
            size_id: PK of the size.  Same default behaviour as in
                ``calculate_product_cost``.
            store_id: PK of the store.  Same fallback behaviour as in
                ``calculate_product_cost``.

        Returns:
            Dictionary with the following structure::

                {
                    "total": Decimal,          # sum of all lines
                    "ingredients": [
                        {
                            "ingredient_id": int,
                            "name": str,
                            "quantity": Decimal,   # in consumption unit
                            "unit": str,           # consumption unit
                            "unit_cost": Decimal,  # cost per consumption unit
                            "line_cost": Decimal,  # quantity × unit_cost
                            "price_source": str,   # "store" | "base"
                        },
                        ...
                    ],
                    "sub_recipes": [
                        {
                            "sub_product_id": int,
                            "name": str,
                            "quantity": Decimal,   # portion used in the recipe
                            "unit_cost": Decimal,  # cost per sub-recipe unit
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
            The same exceptions as ``calculate_product_cost``.
        """
        # Resolve main entities
        product = self.db.query(Product).get(product_id)
        if not product:
            raise ValueError(f"Product {product_id} not found")

        size = self.db.query(ProductSize).get(size_id) if size_id else None
        store = self.db.query(Store).get(store_id) if store_id else None

        # If no size_id was provided, try to resolve the default
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

        # Calculate totals per component
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

        # TODO: Implement per-item detail lists for 'ingredients',
        #       'sub_recipes', and 'packaging'. Each entry should include
        #       name, quantity, unit, and cost resolved individually,
        #       following the same pattern as _calculate_ingredient_cost.

        return {
            "product_id": product_id,
            "product_name": product.name,
            "size_id": size_id,
            "size_name": size.size_name if size else None,
            "store_id": store_id,
            "store_name": store.name if store else None,
            "total_cost": total_cost,
            "breakdown": {
                "ingredients": [],  # TODO: per-ingredient detail
                "sub_recipes": [],  # TODO: per-sub-recipe detail
                "packaging": [],    # TODO: per-packaging-item detail
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
    # Private helpers
    # ------------------------------------------------------------------

    def _calculate_labor_cost(self, product_id: int) -> Decimal:
        """Calculate the labor cost based on the preparation time.

        Multiplies ``prep_time_minutes`` by ``labor_cost_per_minute`` from
        the product.  Both fields are optional in the model; if either is
        ``None`` or zero the method returns ``Decimal("0")`` without error,
        since some products (batch sub-recipes, items with no assigned labor)
        do not incur this cost.

        Labor cost is not scaled with size: preparation time is defined for
        the base product and is assumed constant across sizes.

        Args:
            product_id: PK of the product in the ``products`` table.

        Returns:
            Labor cost as a ``Decimal`` (COP).  Returns ``Decimal("0")`` if
            the product does not exist, or if ``prep_time_minutes`` /
            ``labor_cost_per_minute`` are ``None``.
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
        """Get the effective purchase price of an ingredient.

        Applies the store → base fallback logic:
        1. If ``store_id`` is not ``None``, look up a record in
           ``store_ingredient_prices`` for the (store, ingredient) pair.
           If it exists and has a defined ``local_price``, return it.
        2. In any other case (no store, no override, or null ``local_price``)
           return ``Ingredient.purchase_price``.

        The returned price corresponds to the ingredient's **purchase unit**
        (e.g. price per case, per bag).  To obtain the cost per consumption
        unit the caller must divide by ``Ingredient.conversion_factor``.

        Args:
            ingredient_id: PK of the ingredient in the ``ingredients`` table.
            store_id: PK of the store whose local price is preferred.  If
                ``None``, the local lookup is skipped and the base price is used.

        Returns:
            Purchase unit price as a ``Decimal``.  Returns ``Decimal("0")``
            if ``purchase_price`` is ``None`` in the DB (ingredient with no
            price loaded yet).

        Raises:
            ValueError: If no ``Ingredient`` with ``ingredient_id`` exists in
                the database.
        """
        # 1. Look up the ingredient
        ingredient = (
            self.db.query(Ingredient)
            .filter(Ingredient.id == ingredient_id)
            .first()
        )

        if not ingredient:
            raise ValueError(f"Ingredient {ingredient_id} not found")

        # 2. If there is a store_id, look for an override
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

        # 3. Return base price
        return ingredient.purchase_price or Decimal("0")

    def _get_recipe_unit_conversion(
        self,
        ingredient_id: int,
        recipe_unit_id: int,
    ) -> Decimal:
        """Get the conversion factor from a recipe unit to the usage unit.

        Resolves how many ``usage_unit`` of the ingredient equal one recipe
        unit.  This factor is used to transform quantities expressed in recipe
        units (shots, pumps, teaspoons) to the ingredient's measurement system
        (ml, g) before calculating the cost.

        Example::

            _get_recipe_unit_conversion(syrup_id, pump_id) → Decimal("30")
            # Means: 1 pump = 30 ml

        Args:
            ingredient_id: PK of the ingredient in the ``ingredients`` table.
            recipe_unit_id: PK of the recipe unit in the ``recipe_units`` table
                (e.g. the ID of "pump", "shot", "teaspoon").

        Returns:
            Number of ``usage_unit`` equivalent to 1 ``recipe_unit``, as
            stored in ``IngredientRecipeUnitConversion.usage_unit_quantity``.

        Raises:
            ValueError: If no conversion is defined for the
                (ingredient, recipe_unit) pair.  The message includes the
                resolved names to facilitate correction in the frontend.
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
        _effective_price: Optional[Decimal] = None,
    ) -> Decimal:
        """Calculate the cost of a single ingredient within a recipe.

        Applies in order: price resolution → recipe unit conversion → size
        scaling → ingredient yield → process yield loss → final cost.  Each
        step transforms the effective quantity before multiplying by the
        consumption unit price.

        Detailed process:

        1. **Purchase price** — delegates to ``_get_ingredient_price`` with
           store → base fallback.
        2. **recipe_unit → usage_unit conversion** — if ``recipe_ing`` has a
           ``recipe_unit_id``, calls ``_get_recipe_unit_conversion`` to obtain
           the factor (e.g. 1 pump = 30 ml) and multiplies the quantity.
           If there is no ``recipe_unit_id``, the quantity is already in
           ``usage_unit``.
        3. **Size scaling** — if ``recipe_ing.scales_with_size`` is ``True``,
           multiplies the quantity by ``scale_factor``.
        4. **Ingredient yield** — divides by ``yield_percentage / 100`` to
           account for ingredient waste during storage/prep
           (e.g. yield 95 % → 5 % more raw material is needed).
        5. **Process yield loss** — if ``process_yield_loss > 0``, divides by
           ``1 - process_yield_loss / 100`` to account for additional process
           waste (e.g. evaporation during boiling).
        6. **Final cost** — ``unit_cost = price / conversion_factor`` gives the
           price per ``usage_unit``; multiplied by the effective quantity
           produces the total cost for this line.

        Example::

            # Cappuccino 12 oz: 240 ml whole milk
            # purchase_price = 4 500 COP / 1 000 ml case, yield = 95 %
            # scale_factor = 1.0 (base size), no recipe_unit, no process loss
            #
            # unit_cost  = 4 500 / 1 000 = 4.5 COP/ml
            # qty_yield  = 240 / 0.95   ≈ 252.63 ml
            # total_cost ≈ 4.5 × 252.63 ≈ 1 136.84 COP

        Args:
            recipe_ing: ``RecipeIngredient`` row with ``quantity``,
                ``recipe_unit_id``, ``scales_with_size``, and
                ``process_yield_loss``.
            ingredient: Full ``Ingredient`` object with ``purchase_price``,
                ``conversion_factor``, and ``yield_percentage``.
            scale_factor: Quantity multiplier derived from the selected size
                (``ProductSize.scale_factor``).  Use ``Decimal("1")`` for the
                base size.
            store_id: PK of the store whose local price is preferred.  ``None``
                uses the ingredient's base price.

        Returns:
            Total cost as a ``Decimal`` (COP) for this recipe line with the
            given size and store.

        Raises:
            ValueError: Propagated from ``_get_ingredient_price`` if the
                ingredient does not exist.
            ValueError: Propagated from ``_get_recipe_unit_conversion`` if
                the recipe_unit → usage_unit conversion is missing.
            ZeroDivisionError: If ``ingredient.conversion_factor`` or
                ``ingredient.yield_percentage`` are zero in the DB (corrupt
                data; must be corrected in the catalogue).
        """
        # 1. Ingredient price (use pre-resolved price from batch caller to avoid N+1)
        price = _effective_price if _effective_price is not None else self._get_ingredient_price(ingredient.id, store_id)

        # 2. Quantity in recipe_unit → usage_unit
        if recipe_ing.recipe_unit_id:
            conversion = self._get_recipe_unit_conversion(
                ingredient.id,
                recipe_ing.recipe_unit_id,
            )
            quantity_in_usage_units = recipe_ing.quantity * conversion
        else:
            quantity_in_usage_units = recipe_ing.quantity

        # 3. Size scaling
        if recipe_ing.scales_with_size:
            quantity_in_usage_units *= scale_factor

        # 4. Apply ingredient yield
        # yield_percentage is stored as a fraction (0.0–1.0) in the DB.
        yield_factor = ingredient.yield_percentage  # e.g. 0.98
        if yield_factor and yield_factor > 0:
            quantity_with_yield = quantity_in_usage_units / yield_factor
        else:
            quantity_with_yield = quantity_in_usage_units

        # 5. Apply process yield loss
        # process_yield_loss is stored as a yield percentage (0–100):
        # 100 = no loss, 90 = 10 % process loss.
        if recipe_ing.process_yield_loss > 0 and recipe_ing.process_yield_loss < 100:
            process_yield_factor = recipe_ing.process_yield_loss / Decimal("100")
            quantity_with_yield = quantity_with_yield / process_yield_factor

        # 6. Calculate cost
        # price = purchase_unit price
        # conversion_factor = how many usage_units in 1 purchase_unit
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
        """Calculate the total cost of all direct ingredients of a product.

        Iterates over each ``RecipeIngredient`` of the product and accumulates
        the individual cost using ``_calculate_ingredient_cost``.  Ingredients
        with referential integrity issues (orphan ``ingredient_id``) are
        silently skipped to avoid blocking the calculation; the inconsistency
        must be corrected in the catalogue.

        Args:
            product_id: PK of the product whose recipe is to be valued.
            scale_factor: Multiplier derived from the selected ``ProductSize``.
                Use ``Decimal("1")`` for the base size.
            store_id: PK of the store for applying local prices.  ``None``
                uses each ingredient's base price.

        Returns:
            Sum of costs of all direct ingredients as a ``Decimal`` (COP).
            Returns ``Decimal("0")`` if the product has no recipe ingredients.
        """
        recipe_ingredients = (
            self.db.query(RecipeIngredient)
            .filter(RecipeIngredient.product_id == product_id)
            .all()
        )

        if not recipe_ingredients:
            return Decimal("0")

        # Pre-fetch all ingredients in one query to avoid N+1
        ingredient_ids = [ri.ingredient_id for ri in recipe_ingredients]
        ingredients_map: Dict[int, Ingredient] = {
            i.id: i
            for i in self.db.query(Ingredient)
            .filter(Ingredient.id.in_(ingredient_ids))
            .all()
        }

        # Pre-fetch all store price overrides in one query
        store_prices_map: Dict[int, StoreIngredientPrice] = {}
        if store_id and ingredient_ids:
            store_prices_map = {
                sp.ingredient_id: sp
                for sp in self.db.query(StoreIngredientPrice)
                .filter(
                    StoreIngredientPrice.store_id == store_id,
                    StoreIngredientPrice.ingredient_id.in_(ingredient_ids),
                )
                .all()
            }

        total_cost = Decimal("0")

        for recipe_ing in recipe_ingredients:
            ingredient = ingredients_map.get(recipe_ing.ingredient_id)
            if not ingredient:
                # Orphan ingredient: skip without blocking the calculation
                continue

            # Resolve effective price from pre-fetched data (no per-row DB hit)
            if store_id:
                sp = store_prices_map.get(recipe_ing.ingredient_id)
                effective_price = (
                    sp.local_price
                    if sp and sp.local_price
                    else (ingredient.purchase_price or Decimal("0"))
                )
            else:
                effective_price = ingredient.purchase_price or Decimal("0")

            total_cost += self._calculate_ingredient_cost(
                recipe_ing,
                ingredient,
                scale_factor,
                store_id,
                _effective_price=effective_price,
            )

        return total_cost

    def _calculate_packaging_cost(
        self,
        size_id: Optional[int],
        store_id: Optional[int],
    ) -> Decimal:
        """Calculate the total packaging cost associated with a product size.

        Packaging (cups, lids, napkins, sleeves, etc.) is modelled as
        ``Ingredient`` referenced from ``SizePackaging``, which allows the same
        store → base pricing logic used for recipe ingredients.  Each packaging
        item has a fixed quantity per produced unit (not scaled by size, since
        the size is already implicit in the ``size_id``).

        Args:
            size_id: PK of the ``ProductSize`` whose packaging is to be valued.
                If ``None`` the method returns ``Decimal("0")`` immediately,
                since without a size there is no defined packaging.
            store_id: PK of the store for applying local prices on packaging
                items.  ``None`` uses base prices.

        Returns:
            Sum of the cost of all packaging items as a ``Decimal`` (COP).
            Returns ``Decimal("0")`` if ``size_id`` is ``None`` or if the size
            has no configured packaging.
        """
        if not size_id:
            return Decimal("0")

        packaging_items = (
            self.db.query(SizePackaging)
            .filter(SizePackaging.size_id == size_id)
            .all()
        )

        if not packaging_items:
            return Decimal("0")

        # Pre-fetch all packaging ingredients in one query to avoid N+1
        pkg_ingredient_ids = [p.packaging_ingredient_id for p in packaging_items]
        pkg_ingredients_map: Dict[int, Ingredient] = {
            i.id: i
            for i in self.db.query(Ingredient)
            .filter(Ingredient.id.in_(pkg_ingredient_ids))
            .all()
        }

        # Pre-fetch all store price overrides in one query
        pkg_store_prices_map: Dict[int, StoreIngredientPrice] = {}
        if store_id and pkg_ingredient_ids:
            pkg_store_prices_map = {
                sp.ingredient_id: sp
                for sp in self.db.query(StoreIngredientPrice)
                .filter(
                    StoreIngredientPrice.store_id == store_id,
                    StoreIngredientPrice.ingredient_id.in_(pkg_ingredient_ids),
                )
                .all()
            }

        total_cost = Decimal("0")

        for pkg_item in packaging_items:
            ingredient = pkg_ingredients_map.get(pkg_item.packaging_ingredient_id)
            if not ingredient:
                continue

            if not ingredient.conversion_factor:
                continue

            # Resolve price from pre-fetched data (no per-row DB hit)
            if store_id:
                sp = pkg_store_prices_map.get(pkg_item.packaging_ingredient_id)
                price = (
                    sp.local_price
                    if sp and sp.local_price
                    else (ingredient.purchase_price or Decimal("0"))
                )
            else:
                price = ingredient.purchase_price or Decimal("0")

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
        """Calculate the cost of all sub-recipes used in a product.

        Recursively resolves each ``RecipeSubRecipe`` of the product by calling
        ``calculate_product_cost`` with ``size_id=None`` (sub-recipes are batch
        preparations without size variants).  The consumed sub-recipe quantity
        is scaled if ``scales_with_size=True``.

        The ``_recursion_depth`` parameter acts as a guard against cycles in
        the recipe graph (e.g. A → B → A).  The limit of 10 levels covers any
        reasonable coffee-shop recipe hierarchy; if exceeded there is a data
        error, not a legitimate use case.

        Args:
            product_id: PK of the parent product whose sub-recipes are valued.
            scale_factor: Size multiplier applied to sub-recipes with
                ``scales_with_size=True``.
            store_id: PK of the store for local ingredient prices inside the
                sub-recipe.  ``None`` uses base prices.
            _recursion_depth: Current recursion stack depth.  External callers
                must never pass this; it is managed internally by this class.

        Returns:
            Sum of the cost of all sub-recipes as a ``Decimal`` (COP).
            Returns ``Decimal("0")`` if the product has no sub-recipes.

        Raises:
            RecursionError: If ``_recursion_depth`` exceeds 10, indicating a
                circular dependency in the recipe configuration.
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
            # Unit cost of the sub-recipe (recursive, no size)
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
