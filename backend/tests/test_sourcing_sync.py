"""Phase 4 (B4): decoupled store_supplier_history sync.

sync_store_supplier_history records the resolved route per (store, ingredient)
using close+insert, idempotently, outside the cost batch.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend.models import Ingredient, Product, RecipeIngredient, Store
from backend.models.supply_chain import (
    Region,
    StoreSupplierHistory,
    SupplyRoute,
    SupplyRouteAssignment,
)
from backend.services.sourcing_sync import sync_store_supplier_history


def _scenario(db: Session):
    ing = Ingredient(name="SSH Ing", purchase_price=Decimal("1000"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    region = Region(name="SSH Region", code="SSHR")
    db.add_all([ing, region])
    db.commit()
    store = Store(code="SSH-STORE", name="SSH Store", region_id=region.id)
    route1 = SupplyRoute(ingredient_id=ing.id, is_direct=True)
    route2 = SupplyRoute(ingredient_id=ing.id, is_direct=True)
    db.add_all([store, route1, route2])
    db.commit()
    # region assignment -> route1 (priority 1, open)
    db.add(SupplyRouteAssignment(
        supply_route_id=route1.id, region_id=region.id,
        priority=1, assigned_by="tester"))
    # product using the ingredient so the sync considers it
    product = Product(name="SSH Product", is_active=True, is_sub_recipe=False)
    db.add(product)
    db.commit()
    db.add(RecipeIngredient(
        product_id=product.id, ingredient_id=ing.id,
        quantity=Decimal("100"), scales_with_size=False,
        process_yield_loss=Decimal("0")))
    db.commit()
    return ing, region, store, route1, route2


def _history(db: Session, store_id: int, ing_id: int):
    return (
        db.query(StoreSupplierHistory)
        .filter(StoreSupplierHistory.store_id == store_id,
                StoreSupplierHistory.ingredient_id == ing_id)
        .all()
    )


def test_sync_creates_open_row(test_db: Session):
    ing, region, store, route1, route2 = _scenario(test_db)
    changed = sync_store_supplier_history(test_db, store.id)
    assert changed == 1
    rows = _history(test_db, store.id, ing.id)
    assert len(rows) == 1
    assert rows[0].valid_until is None
    assert rows[0].supply_route_id == route1.id


def test_sync_is_idempotent(test_db: Session):
    ing, region, store, route1, route2 = _scenario(test_db)
    sync_store_supplier_history(test_db, store.id)
    changed2 = sync_store_supplier_history(test_db, store.id)
    assert changed2 == 0
    open_rows = [r for r in _history(test_db, store.id, ing.id) if r.valid_until is None]
    assert len(open_rows) == 1


def test_sync_closes_old_on_route_change(test_db: Session):
    ing, region, store, route1, route2 = _scenario(test_db)
    sync_store_supplier_history(test_db, store.id)

    # store-level override -> route2 wins over the region assignment
    test_db.add(SupplyRouteAssignment(
        supply_route_id=route2.id, store_id=store.id,
        priority=1, assigned_by="tester"))
    test_db.commit()

    changed = sync_store_supplier_history(test_db, store.id)
    assert changed == 1
    rows = _history(test_db, store.id, ing.id)
    open_rows = [r for r in rows if r.valid_until is None]
    closed_rows = [r for r in rows if r.valid_until is not None]
    assert len(open_rows) == 1 and open_rows[0].supply_route_id == route2.id
    assert len(closed_rows) == 1 and closed_rows[0].supply_route_id == route1.id
