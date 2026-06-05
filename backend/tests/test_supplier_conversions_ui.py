"""SUPPLIER_FRONTEND_DESIGN slice #1 — unit-conversions UI.

Covers the new conversions tab: render, create (with HX-Trigger for cost
invalidation), server-side validation (no 0/negative), duplicate handling, delete.
"""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend.models import Ingredient, RecipeUnit
from backend.models.supply_chain import (
    IngredientSupplierRef,
    SupplierUnitConversion,
    SupplyRoute,
)


@pytest.fixture
def route_with_ref(test_db: Session):
    ing = Ingredient(name="Conv UI Milk", purchase_price=Decimal("1000"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    unit = RecipeUnit(name="conv_ui_gram", category="weight")
    test_db.add_all([ing, unit])
    test_db.commit()
    route = SupplyRoute(ingredient_id=ing.id, is_direct=True)
    test_db.add(route)
    test_db.commit()
    ref = IngredientSupplierRef(ingredient_id=ing.id, supply_route_id=route.id,
                               external_name="Milk Box 10kg", purchase_unit="box")
    test_db.add(ref)
    test_db.commit()
    return {"route": route, "ref": ref, "unit": unit}


def _count(db, ref_id):
    return db.query(SupplierUnitConversion).filter(
        SupplierUnitConversion.ingredient_ref_id == ref_id).count()


def test_detail_renders_conversions_tab(test_client, route_with_ref):
    rid = route_with_ref["route"].id
    html = test_client.get(f"/supply-chain/routes/{rid}").text
    assert "route-conversions" in html
    assert "Add conversion" in html


def test_create_conversion_ok_and_invalidates(test_client, test_db, route_with_ref):
    rid = route_with_ref["route"].id
    r = test_client.post(f"/supply-chain/routes/{rid}/conversions/htmx", data={
        "ingredient_ref_id": route_with_ref["ref"].id,
        "recipe_unit_id": route_with_ref["unit"].id,
        "purchase_qty": "1", "recipe_qty": "10000",
    })
    assert r.status_code == 200
    assert r.headers.get("HX-Trigger") == "prices-changed"   # cost invalidation
    assert _count(test_db, route_with_ref["ref"].id) == 1
    assert "10000" in r.text


def test_create_conversion_rejects_non_positive(test_client, test_db, route_with_ref):
    rid = route_with_ref["route"].id
    r = test_client.post(f"/supply-chain/routes/{rid}/conversions/htmx", data={
        "ingredient_ref_id": route_with_ref["ref"].id,
        "recipe_unit_id": route_with_ref["unit"].id,
        "purchase_qty": "1", "recipe_qty": "0",
    })
    assert r.status_code == 200
    assert "greater than zero" in r.text
    assert _count(test_db, route_with_ref["ref"].id) == 0   # nothing saved


def test_duplicate_conversion_is_reported(test_client, test_db, route_with_ref):
    rid = route_with_ref["route"].id
    payload = {
        "ingredient_ref_id": route_with_ref["ref"].id,
        "recipe_unit_id": route_with_ref["unit"].id,
        "purchase_qty": "1", "recipe_qty": "10000",
    }
    test_client.post(f"/supply-chain/routes/{rid}/conversions/htmx", data=payload)
    r = test_client.post(f"/supply-chain/routes/{rid}/conversions/htmx", data=payload)
    assert r.status_code == 200
    assert "already exists" in r.text
    assert _count(test_db, route_with_ref["ref"].id) == 1   # not duplicated


def test_delete_conversion(test_client, test_db, route_with_ref):
    rid = route_with_ref["route"].id
    test_client.post(f"/supply-chain/routes/{rid}/conversions/htmx", data={
        "ingredient_ref_id": route_with_ref["ref"].id,
        "recipe_unit_id": route_with_ref["unit"].id,
        "purchase_qty": "1", "recipe_qty": "10000",
    })
    conv = test_db.query(SupplierUnitConversion).filter(
        SupplierUnitConversion.ingredient_ref_id == route_with_ref["ref"].id).one()
    r = test_client.post(f"/supply-chain/routes/{rid}/conversions/htmx/{conv.id}/delete")
    assert r.status_code == 200
    assert _count(test_db, route_with_ref["ref"].id) == 0
