"""Phase 3: availability-driven substitution (ENGINE_SUPPLIER_PLAN_V2 §1 Fase 3 / B2).

When an ingredient is unavailable (or a substitute is 'always' eligible) the
engine costs the approved SUBSTITUTE with its quantity_ratio, 1 level deep, and
flags has_substitutes. No store -> no substitution (availability is regional).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    Ingredient,
    Product,
    RecipeIngredient,
    Store,
)
from backend.models.supply_chain import (
    IngredientAvailability,
    IngredientSubstitute,
    Region,
)
from backend.services.cost_calculator import CostCalculator


def _pair(db: Session, orig_price: str, sub_price: str):
    """Original (expensive) + substitute (cheap), both 1000 g/purchase unit."""
    orig = Ingredient(
        name="P3 Original", purchase_price=Decimal(orig_price),
        usage_unit="g", conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("1.00"),
    )
    sub = Ingredient(
        name="P3 Substitute", purchase_price=Decimal(sub_price),
        usage_unit="g", conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("1.00"),
    )
    region = Region(name="P3 Region", code="P3R")
    db.add_all([orig, sub, region])
    db.commit()
    store = Store(code="P3-STORE", name="P3 Store", region_id=region.id)
    db.add(store)
    db.commit()
    return orig, sub, region, store


def _product_using(db: Session, ing: Ingredient, qty: str = "100") -> Product:
    p = Product(name="P3 Product", is_sub_recipe=False)
    db.add(p)
    db.commit()
    db.add(RecipeIngredient(
        product_id=p.id, ingredient_id=ing.id,
        quantity=Decimal(qty), scales_with_size=False,
        process_yield_loss=Decimal("0"),
    ))
    db.commit()
    return p


def test_shortage_activates_substitute(test_db: Session):
    """Original in shortage + approved substitute -> cost the substitute.

    Original 20000/1000*100 = 2000 ; Substitute 10000/1000*100 = 1000.
    """
    orig, sub, region, store = _pair(test_db, "20000", "10000")
    test_db.add(IngredientAvailability(
        ingredient_id=orig.id, region_id=region.id, status="shortage",
    ))
    test_db.add(IngredientSubstitute(
        original_ingredient_id=orig.id, substitute_ingredient_id=sub.id,
        approved_by="corp", approval_date=date.today(),
        activation_condition="shortage", quantity_ratio=Decimal("1.0"),
    ))
    test_db.commit()
    product = _product_using(test_db, orig)

    calc = CostCalculator(test_db)
    cost_store = calc.calculate_product_cost(product.id, store_id=store.id)
    assert abs(cost_store - Decimal("1000")) < Decimal("1"), cost_store  # substitute used

    bd = calc.get_cost_breakdown(product.id, store_id=store.id)
    assert bd["has_substitutes"] is True
    line = bd["breakdown"]["ingredients"][0]
    assert line["is_substitute"] is True
    assert line["ingredient_id"] == sub.id
    assert line["original_ingredient_id"] == orig.id


def test_quantity_ratio_applied(test_db: Session):
    """quantity_ratio scales the substitute quantity. ratio=2 -> 2x cost."""
    orig, sub, region, store = _pair(test_db, "20000", "10000")
    test_db.add(IngredientAvailability(
        ingredient_id=orig.id, region_id=region.id, status="shortage",
    ))
    test_db.add(IngredientSubstitute(
        original_ingredient_id=orig.id, substitute_ingredient_id=sub.id,
        approved_by="corp", approval_date=date.today(),
        activation_condition="shortage", quantity_ratio=Decimal("2.0"),
    ))
    test_db.commit()
    product = _product_using(test_db, orig)

    cost = CostCalculator(test_db).calculate_product_cost(product.id, store_id=store.id)
    # substitute base 1000 * ratio 2 = 2000
    assert abs(cost - Decimal("2000")) < Decimal("1"), cost


def test_always_activation_without_shortage(test_db: Session):
    """activation_condition='always' substitutes even with no availability row."""
    orig, sub, region, store = _pair(test_db, "20000", "10000")
    test_db.add(IngredientSubstitute(
        original_ingredient_id=orig.id, substitute_ingredient_id=sub.id,
        approved_by="corp", approval_date=date.today(),
        activation_condition="always", quantity_ratio=Decimal("1.0"),
    ))
    test_db.commit()
    product = _product_using(test_db, orig)

    cost = CostCalculator(test_db).calculate_product_cost(product.id, store_id=store.id)
    assert abs(cost - Decimal("1000")) < Decimal("1"), cost  # substitute


def test_no_store_no_substitution(test_db: Session):
    """Without a store there is no region context -> original is costed."""
    orig, sub, region, store = _pair(test_db, "20000", "10000")
    test_db.add(IngredientAvailability(
        ingredient_id=orig.id, region_id=region.id, status="shortage",
    ))
    test_db.add(IngredientSubstitute(
        original_ingredient_id=orig.id, substitute_ingredient_id=sub.id,
        approved_by="corp", approval_date=date.today(),
        activation_condition="shortage", quantity_ratio=Decimal("1.0"),
    ))
    test_db.commit()
    product = _product_using(test_db, orig)

    cost = CostCalculator(test_db).calculate_product_cost(product.id, store_id=None)
    assert abs(cost - Decimal("2000")) < Decimal("1"), cost  # original, no substitution
