"""FRONTEND_AUDIT #6 — runtime guard for the cost-breakdown contract.

get_cost_breakdown is consumed by both the template (costs/_result.html) and the
JSON adapter (routers/costs.py). The TypedDict CostBreakdown documents the shape
for static checking; this test locks it at runtime so shape drift fails here
instead of silently at render time.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from backend.models import Ingredient, Product, RecipeIngredient
from backend.services.cost_calculator import CostCalculator


def test_breakdown_shape(test_db: Session):
    ing = Ingredient(name="Contract Ing", purchase_price=Decimal("1000"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    product = Product(name="Contract Product", is_sub_recipe=False)
    test_db.add_all([ing, product])
    test_db.commit()
    test_db.add(RecipeIngredient(product_id=product.id, ingredient_id=ing.id,
                                 quantity=Decimal("100"), scales_with_size=False,
                                 process_yield_loss=Decimal("0")))
    test_db.commit()

    bd = CostCalculator(test_db).get_cost_breakdown(product.id)

    # Top-level contract keys.
    for key in ("product_id", "product_name", "size_id", "size_name", "store_id",
                "store_name", "total_cost", "has_substitutes", "breakdown", "totals"):
        assert key in bd, f"missing top-level key {key}"

    for section in ("ingredients", "sub_recipes", "packaging", "labor"):
        assert section in bd["breakdown"], f"missing breakdown.{section}"
    for section in ("ingredients", "sub_recipes", "packaging", "labor"):
        assert section in bd["totals"], f"missing totals.{section}"

    # Ingredient line carries provenance + substitution contract fields.
    line = bd["breakdown"]["ingredients"][0]
    for key in ("ingredient_id", "name", "quantity", "unit", "unit_cost",
                "line_cost", "price_source", "supply_route_id", "manufacturer_id",
                "distributor_id", "is_substitute", "original_ingredient_id"):
        assert key in line, f"missing ingredient line key {key}"

    labor = bd["breakdown"]["labor"]
    for key in ("minutes", "cost_per_minute", "cost"):
        assert key in labor, f"missing labor key {key}"
