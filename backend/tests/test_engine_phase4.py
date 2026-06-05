"""Phase 4: cost snapshots + lineage (ENGINE_SUPPLIER_PLAN_V2 §1 Fase 4 / T1/T2/B3).

calculate_all_prices(save_to_db=True) must persist an immutable RecipeCostSnapshot
per (product, size) when a store is given: base_cost (no subs) vs effective_cost
(with subs), has_substitutes, line-by-line lineage in snapshot_detail, plus
batch_run_id / size_id / formula_version. No store -> no snapshot.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    Ingredient,
    Product,
    ProductSize,
    RecipeIngredient,
    Store,
)
from backend.models.supply_chain import (
    IngredientAvailability,
    IngredientSubstitute,
    Region,
    RecipeCostSnapshot,
)
from backend.services.pricing_engine import PricingEngine


def _store(db: Session):
    region = Region(name="P4 Region", code="P4R")
    db.add(region)
    db.commit()
    store = Store(code="P4-STORE", name="P4 Store", region_id=region.id)
    db.add(store)
    db.commit()
    return region, store


def _product_with_size(db: Session, ing: Ingredient, qty="100") -> tuple:
    p = Product(name="P4 Product", is_active=True, is_sub_recipe=False)
    db.add(p)
    db.commit()
    size = ProductSize(
        product_id=p.id, size_name="medium",
        scale_factor=Decimal("1.0"), is_default=True,
    )
    db.add(size)
    db.add(RecipeIngredient(
        product_id=p.id, ingredient_id=ing.id,
        quantity=Decimal(qty), scales_with_size=False,
        process_yield_loss=Decimal("0"),
    ))
    db.commit()
    return p, size


def _snapshots_for(db: Session, product_id: int):
    return (
        db.query(RecipeCostSnapshot)
        .filter(RecipeCostSnapshot.product_id == product_id)
        .all()
    )


def test_snapshot_written_with_substitute_lineage(test_db: Session):
    region, store = _store(test_db)
    orig = Ingredient(name="P4 Orig", purchase_price=Decimal("20000"),
                      usage_unit="g", conversion_factor=Decimal("1000"),
                      yield_percentage=Decimal("1.00"))
    sub = Ingredient(name="P4 Sub", purchase_price=Decimal("10000"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    test_db.add_all([orig, sub])
    test_db.commit()
    test_db.add(IngredientAvailability(
        ingredient_id=orig.id, region_id=region.id, status="shortage"))
    test_db.add(IngredientSubstitute(
        original_ingredient_id=orig.id, substitute_ingredient_id=sub.id,
        approved_by="corp", approval_date=date.today(),
        activation_condition="shortage", quantity_ratio=Decimal("1.0")))
    test_db.commit()
    product, size = _product_with_size(test_db, orig)

    res = PricingEngine(test_db).calculate_all_prices(
        store_id=store.id, save_to_db=True)
    assert res["snapshots_written"] >= 1

    snaps = _snapshots_for(test_db, product.id)
    assert len(snaps) == 1
    s = snaps[0]
    assert s.has_substitutes is True
    assert s.size_id == size.id
    assert s.batch_run_id is not None
    assert s.formula_version == "v1"
    # effective (substitute ~1000) below base (original ~2000)
    assert abs(Decimal(str(s.effective_cost)) - Decimal("1000")) < Decimal("1")
    assert abs(Decimal(str(s.base_cost)) - Decimal("2000")) < Decimal("1")
    line = s.snapshot_detail["ingredients"][0]
    assert line["is_substitute"] is True
    assert line["original_ingredient_id"] == orig.id
    assert line["ingredient_id"] == sub.id


def test_snapshot_flags_unavailable_no_substitute(test_db: Session):
    region, store = _store(test_db)
    orig = Ingredient(name="P4 Orphan", purchase_price=Decimal("20000"),
                      usage_unit="g", conversion_factor=Decimal("1000"),
                      yield_percentage=Decimal("1.00"))
    test_db.add(orig)
    test_db.commit()
    test_db.add(IngredientAvailability(
        ingredient_id=orig.id, region_id=region.id, status="shortage"))
    test_db.commit()
    product, size = _product_with_size(test_db, orig)

    PricingEngine(test_db).calculate_all_prices(store_id=store.id, save_to_db=True)
    s = _snapshots_for(test_db, product.id)[0]
    assert s.has_substitutes is False
    # no substitution -> effective == base
    assert Decimal(str(s.effective_cost)) == Decimal(str(s.base_cost))
    line = s.snapshot_detail["ingredients"][0]
    assert line["flags"]["unavailable_no_substitute"] is True


def test_no_snapshot_without_store(test_db: Session):
    ing = Ingredient(name="P4 Catalogue", purchase_price=Decimal("10000"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    test_db.add(ing)
    test_db.commit()
    product, size = _product_with_size(test_db, ing)

    res = PricingEngine(test_db).calculate_all_prices(store_id=None, save_to_db=True)
    assert res["snapshots_written"] == 0
    assert _snapshots_for(test_db, product.id) == []
