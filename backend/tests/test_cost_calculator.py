"""Unit tests for CostCalculator.

Each test creates its own data within the isolated session provided by
`test_db` (automatic rollback on completion).  Fixtures from conftest.py
are used as a starting point where applicable; for specific scenarios,
additional data is created inline.

Expected cost variable naming conventions:
    Expected values are calculated by hand in the docstrings so that they
    serve as executable specifications, not merely as verifications.
"""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    Ingredient,
    IngredientRecipeUnitConversion,
    Product,
    ProductSize,
    RecipeIngredient,
    RecipeUnit,
    Store,
    StoreIngredientPrice,
)
from backend.services.cost_calculator import CostCalculator


# ---------------------------------------------------------------------------
# 1. Cost with a direct usage_unit ingredient (no recipe_unit)
# ---------------------------------------------------------------------------

def test_calculate_simple_ingredient_cost(
    test_db: Session,
    sample_product: Product,
    sample_ingredient: Ingredient,
    sample_recipe: RecipeIngredient,
    sample_size: ProductSize,
):
    """The cost of an ingredient expressed in usage_unit is calculated correctly.

    Scenario:
        - Milk: 4 500 COP / 1 000 ml, yield 95 %
        - Recipe: 240 ml, scales_with_size=True, process_yield_loss=0
        - Size: scale_factor=1.0 (base medium)

    Expected calculation:
        unit_cost  = 4 500 / 1 000        = 4.5 COP/ml
        qty_yield  = 240 / 0.95           ≈ 252.6316 ml
        line_cost  = 4.5 × 252.6316       ≈ 1 136.84 COP
        labor_cost = 3 min × 200 COP/min  = 600 COP
        total      ≈ 1 736.84 COP
    """
    calc = CostCalculator(test_db)
    cost = calc.calculate_product_cost(
        product_id=sample_product.id,
        size_id=sample_size.id,
        store_id=None,
    )

    assert cost > Decimal("0")
    # Tolerant range for DB rounding (Numeric vs pure Decimal)
    assert Decimal("1700") < cost < Decimal("1800")


# ---------------------------------------------------------------------------
# 2. Cost with recipe_unit (pump → ml conversion)
# ---------------------------------------------------------------------------

def test_calculate_with_recipe_unit(test_db: Session):
    """Quantities expressed in recipe_unit are converted to usage_unit before costing.

    Scenario:
        - Vanilla syrup: 28 000 COP / 750 ml bottle, yield 98 %
        - RecipeUnit: "pump"
        - Conversion: 1 pump = 30 ml
        - Recipe: 2 pumps, scales_with_size=False, process_yield_loss=0

    Expected calculation:
        unit_cost       = 28 000 / 750          ≈ 37.3333 COP/ml
        qty_usage_units = 2 pumps × 30 ml/pump  = 60 ml
        qty_yield       = 60 / 0.98             ≈ 61.2245 ml
        line_cost       = 37.3333 × 61.2245     ≈ 2 285.71 COP
        labor_cost      = 0 (no prep_time)
        total           ≈ 2 285.71 COP
    """
    # Test data
    syrup = Ingredient(
        name="Test Vanilla Syrup",
        category="syrups",
        purchase_unit="Bottle 750ml",
        purchase_price=Decimal("28000"),
        usage_unit="ml",
        conversion_factor=Decimal("750"),
        yield_percentage=Decimal("0.98"),
    )
    pump_unit = RecipeUnit(name="test_pump", category="volume")
    product = Product(
        name="Test Vanilla Latte",
        category="hot_beverages",
        is_sub_recipe=False,
    )
    test_db.add_all([syrup, pump_unit, product])
    test_db.commit()

    conversion = IngredientRecipeUnitConversion(
        ingredient_id=syrup.id,
        recipe_unit_id=pump_unit.id,
        usage_unit_quantity=Decimal("30"),
    )
    recipe_line = RecipeIngredient(
        product_id=product.id,
        ingredient_id=syrup.id,
        quantity=Decimal("2"),
        recipe_unit_id=pump_unit.id,
        scales_with_size=False,
        process_yield_loss=Decimal("0"),
    )
    test_db.add_all([conversion, recipe_line])
    test_db.commit()

    calc = CostCalculator(test_db)
    cost = calc.calculate_product_cost(product_id=product.id)

    assert Decimal("2200") < cost < Decimal("2400")


# ---------------------------------------------------------------------------
# 3. Size scaling
# ---------------------------------------------------------------------------

def test_scaling_with_size(test_db: Session):
    """The size's scale_factor only affects ingredients with scales_with_size=True.

    Scenario:
        - Milk (scales=True):  4 500 COP / 1 000 ml, yield 100 %
        - Espresso (scales=False): 25 000 COP / 500 g, yield 100 %
        - Sizes: small (0.67×), medium (1.0×), large (1.33×)
        - Recipe: 240 ml milk (scalable) + 60 g espresso (fixed)

    Invariants verified:
        1. cost_small < cost_medium < cost_large
        2. The difference between sizes comes only from the milk.
        3. The espresso component is the same across all 3 sizes.
    """
    milk = Ingredient(
        name="Test Milk Scaling",
        purchase_price=Decimal("4500"),
        usage_unit="ml",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("1.00"),
    )
    espresso = Ingredient(
        name="Test Espresso Scaling",
        purchase_price=Decimal("25000"),
        usage_unit="g",
        conversion_factor=Decimal("500"),
        yield_percentage=Decimal("1.00"),
    )
    product = Product(name="Test Scaling Product", is_sub_recipe=False)
    test_db.add_all([milk, espresso, product])
    test_db.commit()

    small = ProductSize(
        product_id=product.id, size_name="small",
        scale_factor=Decimal("0.67"), is_default=False,
    )
    medium = ProductSize(
        product_id=product.id, size_name="medium",
        scale_factor=Decimal("1.0"), is_default=True,
    )
    large = ProductSize(
        product_id=product.id, size_name="large",
        scale_factor=Decimal("1.33"), is_default=False,
    )
    test_db.add_all([small, medium, large])
    test_db.commit()

    milk_line = RecipeIngredient(
        product_id=product.id, ingredient_id=milk.id,
        quantity=Decimal("240"), scales_with_size=True,
        process_yield_loss=Decimal("0"),
    )
    espresso_line = RecipeIngredient(
        product_id=product.id, ingredient_id=espresso.id,
        quantity=Decimal("60"), scales_with_size=False,
        process_yield_loss=Decimal("0"),
    )
    test_db.add_all([milk_line, espresso_line])
    test_db.commit()

    calc = CostCalculator(test_db)
    cost_small  = calc.calculate_product_cost(product.id, size_id=small.id)
    cost_medium = calc.calculate_product_cost(product.id, size_id=medium.id)
    cost_large  = calc.calculate_product_cost(product.id, size_id=large.id)

    # 1. Costs scale with size
    assert cost_small < cost_medium < cost_large

    # 2. The fixed espresso component is the same across all 3 sizes
    #    espresso_cost = 25 000 / 500 × 60 = 3 000 COP
    espresso_cost = Decimal("25000") / Decimal("500") * Decimal("60")

    # 3. The difference between sizes matches the milk scale factor
    #    milk_unit_cost = 4 500 / 1 000 = 4.5 COP/ml
    milk_unit = Decimal("4500") / Decimal("1000")
    milk_medium = milk_unit * Decimal("240")
    milk_small  = milk_unit * Decimal("240") * Decimal("0.67")
    milk_large  = milk_unit * Decimal("240") * Decimal("1.33")

    assert abs(cost_medium - (espresso_cost + milk_medium)) < Decimal("1")
    assert abs(cost_small  - (espresso_cost + milk_small))  < Decimal("1")
    assert abs(cost_large  - (espresso_cost + milk_large))  < Decimal("1")


# ---------------------------------------------------------------------------
# 4. Ingredient yield adjustment
# ---------------------------------------------------------------------------

def test_yield_loss(test_db: Session):
    """yield_percentage increases the effective quantity and therefore the cost.

    Scenario:
        - Fruit: 10 000 COP / kg (1 000 g), yield 80 %
        - Recipe: 100 g, no scaling, no process_yield_loss

    Expected calculation:
        unit_cost  = 10 000 / 1 000  = 10 COP/g
        qty_yield  = 100 / 0.80      = 125 g
        line_cost  = 10 × 125        = 1 250 COP

    Comparison with 100 % yield:
        qty_yield_100 = 100 g
        line_cost_100 = 10 × 100 = 1 000 COP
        ratio ≈ 1 250 / 1 000 = 1.25  (25 % more expensive)
    """
    fruit = Ingredient(
        name="Test Fruit 80pct Yield",
        purchase_price=Decimal("10000"),
        usage_unit="g",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("0.80"),
    )
    fruit_full_yield = Ingredient(
        name="Test Fruit 100pct Yield",
        purchase_price=Decimal("10000"),
        usage_unit="g",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("1.00"),
    )
    product_80  = Product(name="Test Yield 80",  is_sub_recipe=False)
    product_100 = Product(name="Test Yield 100", is_sub_recipe=False)
    test_db.add_all([fruit, fruit_full_yield, product_80, product_100])
    test_db.commit()

    test_db.add_all([
        RecipeIngredient(
            product_id=product_80.id, ingredient_id=fruit.id,
            quantity=Decimal("100"), scales_with_size=False,
            process_yield_loss=Decimal("0"),
        ),
        RecipeIngredient(
            product_id=product_100.id, ingredient_id=fruit_full_yield.id,
            quantity=Decimal("100"), scales_with_size=False,
            process_yield_loss=Decimal("0"),
        ),
    ])
    test_db.commit()

    calc = CostCalculator(test_db)
    cost_80  = calc.calculate_product_cost(product_80.id)
    cost_100 = calc.calculate_product_cost(product_100.id)

    assert cost_80 > cost_100
    assert abs(cost_80  - Decimal("1250")) < Decimal("1")
    assert abs(cost_100 - Decimal("1000")) < Decimal("1")


# ---------------------------------------------------------------------------
# 5. Store price override
# ---------------------------------------------------------------------------

def test_store_price_override(test_db: Session):
    """When a StoreIngredientPrice exists for the store, local_price is used.

    Scenario:
        - Ingredient: base price 1 000 COP / unit
        - Store A:    local price 1 200 COP / unit  (override)
        - Recipe:     1 unit, no conversion or extra yield

    Verifications:
        1. Without store_id → cost uses base price  (1 000 COP)
        2. With store_id    → cost uses local price (1 200 COP)
        3. local > base     → cost_store > cost_base
    """
    ingredient = Ingredient(
        name="Test Override Ingredient",
        purchase_price=Decimal("1000"),
        usage_unit="unit",
        conversion_factor=Decimal("1"),
        yield_percentage=Decimal("1.00"),
    )
    store = Store(code="TEST-STORE-01", name="Test Store Alpha", city="Bogotá")
    product = Product(name="Test Override Product", is_sub_recipe=False)
    test_db.add_all([ingredient, store, product])
    test_db.commit()

    test_db.add_all([
        StoreIngredientPrice(
            store_id=store.id,
            ingredient_id=ingredient.id,
            local_price=Decimal("1200"),
        ),
        RecipeIngredient(
            product_id=product.id,
            ingredient_id=ingredient.id,
            quantity=Decimal("1"),
            scales_with_size=False,
            process_yield_loss=Decimal("0"),
        ),
    ])
    test_db.commit()

    calc = CostCalculator(test_db)
    cost_base  = calc.calculate_product_cost(product.id, store_id=None)
    cost_store = calc.calculate_product_cost(product.id, store_id=store.id)

    assert abs(cost_base  - Decimal("1000")) < Decimal("1")
    assert abs(cost_store - Decimal("1200")) < Decimal("1")
    assert cost_store > cost_base
