"""Tests unitarios para CostCalculator.

Cada test crea sus propios datos dentro de la sesión aislada provista por
`test_db` (rollback automático al finalizar). Los fixtures de conftest.py
se usan como punto de partida cuando aplica; para escenarios específicos
se crean datos adicionales inline.

Convenciones de nomenclatura de variables de costo esperado:
    Los valores esperados se calculan a mano en los docstrings para que
    sirvan de especificación ejecutable, no solo de verificación.
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
# 1. Costo con un ingrediente en usage_unit directo (sin recipe_unit)
# ---------------------------------------------------------------------------

def test_calculate_simple_ingredient_cost(
    test_db: Session,
    sample_product: Product,
    sample_ingredient: Ingredient,
    sample_recipe: RecipeIngredient,
    sample_size: ProductSize,
):
    """El costo de un ingrediente expresado en usage_unit se calcula correctamente.

    Escenario:
        - Leche: 4 500 COP / 1 000 ml, yield 95 %
        - Receta: 240 ml, scales_with_size=True, process_yield_loss=0
        - Tamaño: scale_factor=1.0 (mediano base)

    Cálculo esperado:
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
    # Rango tolerante a redondeo de BD (Numeric vs Decimal puro)
    assert Decimal("1700") < cost < Decimal("1800")


# ---------------------------------------------------------------------------
# 2. Costo con recipe_unit (conversión pump → ml)
# ---------------------------------------------------------------------------

def test_calculate_with_recipe_unit(test_db: Session):
    """Las cantidades expresadas en recipe_unit se convierten a usage_unit antes de costear.

    Escenario:
        - Jarabe de vainilla: 28 000 COP / botella 750 ml, yield 98 %
        - RecipeUnit: "pump"
        - Conversión: 1 pump = 30 ml
        - Receta: 2 pumps, scales_with_size=False, process_yield_loss=0

    Cálculo esperado:
        unit_cost       = 28 000 / 750          ≈ 37.3333 COP/ml
        qty_usage_units = 2 pumps × 30 ml/pump  = 60 ml
        qty_yield       = 60 / 0.98             ≈ 61.2245 ml
        line_cost       = 37.3333 × 61.2245     ≈ 2 285.71 COP
        labor_cost      = 0 (sin prep_time)
        total           ≈ 2 285.71 COP
    """
    # Datos del test
    syrup = Ingredient(
        name="Test Vanilla Syrup",
        category="syrups",
        purchase_unit="Bottle 750ml",
        purchase_price=Decimal("28000"),
        usage_unit="ml",
        conversion_factor=Decimal("750"),
        yield_percentage=Decimal("98"),
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
# 3. Scaling por tamaño
# ---------------------------------------------------------------------------

def test_scaling_with_size(test_db: Session):
    """El scale_factor del tamaño afecta solo ingredientes con scales_with_size=True.

    Escenario:
        - Leche (scales=True):  4 500 COP / 1 000 ml, yield 100 %
        - Espresso (scales=False): 25 000 COP / 500 g, yield 100 %
        - Tamaños: pequeño (0.67×), mediano (1.0×), grande (1.33×)
        - Receta: 240 ml leche (escalable) + 60 g espresso (fijo)

    Invariantes verificados:
        1. costo_pequeño < costo_mediano < costo_grande
        2. La diferencia entre tamaños solo proviene de la leche.
        3. El componente de espresso es igual en los 3 tamaños.
    """
    milk = Ingredient(
        name="Test Milk Scaling",
        purchase_price=Decimal("4500"),
        usage_unit="ml",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("100"),
    )
    espresso = Ingredient(
        name="Test Espresso Scaling",
        purchase_price=Decimal("25000"),
        usage_unit="g",
        conversion_factor=Decimal("500"),
        yield_percentage=Decimal("100"),
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

    # 1. Los costos escalan con el tamaño
    assert cost_small < cost_medium < cost_large

    # 2. El componente fijo de espresso es el mismo en los 3 tamaños
    #    espresso_cost = 25 000 / 500 × 60 = 3 000 COP
    espresso_cost = Decimal("25000") / Decimal("500") * Decimal("60")

    # 3. La diferencia entre tamaños coincide con el factor de escala de la leche
    #    milk_unit_cost = 4 500 / 1 000 = 4.5 COP/ml
    milk_unit = Decimal("4500") / Decimal("1000")
    milk_medium = milk_unit * Decimal("240")
    milk_small  = milk_unit * Decimal("240") * Decimal("0.67")
    milk_large  = milk_unit * Decimal("240") * Decimal("1.33")

    assert abs(cost_medium - (espresso_cost + milk_medium)) < Decimal("1")
    assert abs(cost_small  - (espresso_cost + milk_small))  < Decimal("1")
    assert abs(cost_large  - (espresso_cost + milk_large))  < Decimal("1")


# ---------------------------------------------------------------------------
# 4. Ajuste por yield del ingrediente
# ---------------------------------------------------------------------------

def test_yield_loss(test_db: Session):
    """El yield_percentage incrementa la cantidad efectiva y por tanto el costo.

    Escenario:
        - Fruta: 10 000 COP / kg (1 000 g), yield 80 %
        - Receta: 100 g, sin scaling, sin process_yield_loss

    Cálculo esperado:
        unit_cost  = 10 000 / 1 000  = 10 COP/g
        qty_yield  = 100 / 0.80      = 125 g
        line_cost  = 10 × 125        = 1 250 COP

    Comparación con yield 100 %:
        qty_yield_100 = 100 g
        line_cost_100 = 10 × 100 = 1 000 COP
        ratio ≈ 1 250 / 1 000 = 1.25  (25 % más caro)
    """
    fruit = Ingredient(
        name="Test Fruit 80pct Yield",
        purchase_price=Decimal("10000"),
        usage_unit="g",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("80"),
    )
    fruit_full_yield = Ingredient(
        name="Test Fruit 100pct Yield",
        purchase_price=Decimal("10000"),
        usage_unit="g",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("100"),
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
# 5. Override de precio por tienda
# ---------------------------------------------------------------------------

def test_store_price_override(test_db: Session):
    """Cuando existe StoreIngredientPrice para la tienda, se usa local_price.

    Escenario:
        - Ingrediente: precio base 1 000 COP / unidad
        - Tienda A:    precio local 1 200 COP / unidad  (override)
        - Receta:      1 unidad, sin conversión ni yield extra

    Verificaciones:
        1. Sin store_id    → costo usa precio base  (1 000 COP)
        2. Con store_id    → costo usa precio local (1 200 COP)
        3. local > base    → costo_store > costo_base
    """
    ingredient = Ingredient(
        name="Test Override Ingredient",
        purchase_price=Decimal("1000"),
        usage_unit="unit",
        conversion_factor=Decimal("1"),
        yield_percentage=Decimal("100"),
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
