"""Phase 6: per-currency quantize (ENGINE_SUPPLIER_PLAN_V2 §1 Fase 6 / E6).

The final cost is quantized to the currency's minor_unit instead of a fixed 2
decimals. COP has minor_unit=0 -> integer pesos. Changing the minor_unit changes
the rounding, proving it is data-driven (not hard-coded).
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models import Ingredient, Product, RecipeIngredient
from backend.services.cost_calculator import CostCalculator


def _milk_product(db: Session) -> Product:
    # 4500/1000 = 4.5 COP/ml ; 240 ml / 0.95 yield = 252.6315... ml
    # raw = 4.5 * 252.6315... = 1136.8421... COP
    milk = Ingredient(name="P6 Milk", purchase_price=Decimal("4500"),
                      usage_unit="ml", conversion_factor=Decimal("1000"),
                      yield_percentage=Decimal("0.95"))
    product = Product(name="P6 Product", is_sub_recipe=False)
    db.add_all([milk, product])
    db.commit()
    db.add(RecipeIngredient(product_id=product.id, ingredient_id=milk.id,
                            quantity=Decimal("240"), scales_with_size=False,
                            process_yield_loss=Decimal("0")))
    db.commit()
    return product


def test_cop_quantizes_to_integer(test_db: Session):
    product = _milk_product(test_db)
    cost = CostCalculator(test_db).calculate_product_cost(product.id)
    # COP minor_unit = 0 -> integer pesos (1136.84... -> 1137)
    assert cost == Decimal("1137")
    assert cost == cost.to_integral_value()  # no fractional part


def test_quantize_follows_minor_unit(test_db: Session):
    product = _milk_product(test_db)
    # Make COP behave like a 2-decimal currency (rolled back after the test).
    test_db.execute(text("UPDATE currencies SET minor_unit = 2 WHERE code = 'COP'"))
    test_db.flush()
    cost = CostCalculator(test_db).calculate_product_cost(product.id)
    assert cost == Decimal("1136.84")
