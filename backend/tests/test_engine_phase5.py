"""Phase 5: queue worker + ingestion + FX in snapshot
(ENGINE_SUPPLIER_PLAN_V2 §1 Fase 5 / T3 / B5).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models import (
    Ingredient,
    IngredientRecipeUnitConversion,
    Product,
    ProductPricing,
    ProductSize,
    RecipeIngredient,
    RecipeUnit,
    Store,
)
from backend.models.supply_chain import (
    IngredientSupplierRef,
    Region,
    RecipeCostSnapshot,
    SupplierUnitConversion,
    SupplyRoute,
    SupplyRouteAssignment,
    SupplyRoutePrice,
)
from backend.services import calc_worker
from backend.services.calc_worker import reverse_bom_closure, run_worker
from backend.services.price_ingest import ingest_route_prices


def _simple_priceable(db: Session):
    ing = Ingredient(name="P5 Ing", purchase_price=Decimal("10000"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    region = Region(name="P5 Region", code="P5R")
    db.add_all([ing, region])
    db.commit()
    store = Store(code="P5-STORE", name="P5 Store", region_id=region.id)
    product = Product(name="P5 Product", is_active=True, is_sub_recipe=False)
    db.add_all([store, product])
    db.commit()
    db.add(ProductSize(product_id=product.id, size_name="medium",
                       scale_factor=Decimal("1.0"), is_default=True))
    db.add(RecipeIngredient(product_id=product.id, ingredient_id=ing.id,
                            quantity=Decimal("100"), scales_with_size=False,
                            process_yield_loss=Decimal("0")))
    db.commit()
    return ing, region, store, product


def _enqueue(db, job_type, store_id=None, product_ids=None, payload=None, max_attempts=5):
    return db.execute(
        text(
            "INSERT INTO calc_jobs (job_type, store_id, product_ids, payload, max_attempts) "
            "VALUES (:t, :s, CAST(:p AS bigint[]), CAST(:pl AS jsonb), :ma) RETURNING id"
        ),
        {"t": job_type, "s": store_id, "p": list(product_ids or []),
         "pl": __import__("json").dumps(payload or {}), "ma": max_attempts},
    ).scalar()


def test_worker_processes_batch_chunk(test_db: Session):
    ing, region, store, product = _simple_priceable(test_db)
    job_id = _enqueue(test_db, "batch_chunk", store_id=store.id, product_ids={product.id})
    test_db.commit()

    n = run_worker(test_db, worker_id="t", max_jobs=5)
    assert n >= 1
    status = test_db.execute(
        text("SELECT status FROM calc_jobs WHERE id=:i"), {"i": job_id}).scalar()
    assert status == "done"
    # pricing + snapshot written
    assert test_db.query(ProductPricing).filter(
        ProductPricing.product_id == product.id).count() >= 1
    assert test_db.query(RecipeCostSnapshot).filter(
        RecipeCostSnapshot.product_id == product.id).count() == 1


def test_reverse_bom_closure(test_db: Session):
    ing, region, store, product = _simple_priceable(test_db)
    affected = reverse_bom_closure(test_db, ing.id)
    assert product.id in affected


def test_price_change_enqueues_batch_chunk(test_db: Session):
    ing, region, store, product = _simple_priceable(test_db)
    job_id = _enqueue(test_db, "price_change", payload={"ingredient_id": ing.id})
    test_db.commit()

    # process ONLY the price_change job
    run_worker(test_db, worker_id="t", max_jobs=1)
    assert test_db.execute(
        text("SELECT status FROM calc_jobs WHERE id=:i"), {"i": job_id}).scalar() == "done"
    # a batch_chunk covering the affected product was enqueued
    pending = test_db.execute(text(
        "SELECT product_ids FROM calc_jobs "
        "WHERE job_type='batch_chunk' AND status='pending'")).all()
    assert any(product.id in row.product_ids for row in pending)


def test_failed_job_dead_letters(test_db: Session):
    job_id = _enqueue(test_db, "nope_unknown", max_attempts=1)
    test_db.commit()
    run_worker(test_db, worker_id="t", max_jobs=1)
    row = test_db.execute(text(
        "SELECT status, last_error FROM calc_jobs WHERE id=:i"), {"i": job_id}).mappings().first()
    assert row["status"] == "dead"
    assert "unknown job_type" in (row["last_error"] or "")


def test_ingest_route_prices_and_outbox(test_db: Session):
    ing = Ingredient(name="P5 Ingest Ing", purchase_price=Decimal("1"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    unit = RecipeUnit(name="p5_unit", category="volume")
    test_db.add_all([ing, unit])
    test_db.commit()
    route = SupplyRoute(ingredient_id=ing.id, is_direct=True)
    test_db.add(route)
    test_db.commit()

    ids = ingest_route_prices(test_db, [{
        "route_id": route.id, "list_price": Decimal("100"),
        "qargo_price": Decimal("90"), "currency": "COP",
        "price_unit_id": unit.id, "price_per_unit": "por litro",
        "created_by": "tester", "source": "csv",
    }])
    assert len(ids) == 1
    row = test_db.query(SupplyRoutePrice).filter(
        SupplyRoutePrice.supply_route_id == route.id).one()
    assert row.valid_until is None and row.price_unit_id == unit.id
    # outbox trigger enqueued a route_change job
    n = test_db.execute(text(
        "SELECT count(*) FROM calc_jobs WHERE job_type='route_change' "
        "AND (payload->>'supply_route_id')::bigint = :r"), {"r": route.id}).scalar()
    assert n == 1


def test_ingest_rejects_qargo_over_list(test_db: Session):
    ing = Ingredient(name="P5 Bad Ing", purchase_price=Decimal("1"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    test_db.add(ing)
    test_db.commit()
    route = SupplyRoute(ingredient_id=ing.id, is_direct=True)
    test_db.add(route)
    test_db.commit()
    with pytest.raises(Exception):
        ingest_route_prices(test_db, [{
            "route_id": route.id, "list_price": Decimal("100"),
            "qargo_price": Decimal("150"), "currency": "COP",
            "price_unit_id": None, "price_per_unit": "x", "created_by": "t",
        }])


def test_fx_captured_in_snapshot(test_db: Session):
    """USD route price normalised to COP; snapshot records the fx_rate used."""
    ing = Ingredient(name="P5 FX Ing", purchase_price=Decimal("999999"),
                     usage_unit="g", conversion_factor=Decimal("1000"),
                     yield_percentage=Decimal("1.00"))
    unit = RecipeUnit(name="p5_fx_unit", category="volume")
    region = Region(name="P5 FX Region", code="P5FX")
    test_db.add_all([ing, unit, region])
    test_db.commit()
    store = Store(code="P5-FX-STORE", name="P5 FX Store", region_id=region.id)
    route = SupplyRoute(ingredient_id=ing.id, is_direct=True)
    test_db.add_all([store, route])
    test_db.commit()
    test_db.add(IngredientRecipeUnitConversion(
        ingredient_id=ing.id, recipe_unit_id=unit.id, usage_unit_quantity=Decimal("1")))
    test_db.add(SupplyRouteAssignment(
        supply_route_id=route.id, region_id=region.id, priority=1, assigned_by="t"))
    test_db.add(SupplyRoutePrice(
        supply_route_id=route.id, list_price=Decimal("1"), qargo_price=Decimal("1"),
        currency_code="USD", price_per_unit="por litro", price_unit_id=unit.id,
        created_by="t"))
    ref = IngredientSupplierRef(ingredient_id=ing.id, supply_route_id=route.id,
                                external_name="X", purchase_unit="litro")
    test_db.add(ref)
    test_db.commit()
    test_db.add(SupplierUnitConversion(
        ingredient_ref_id=ref.id, recipe_unit_id=unit.id,
        purchase_qty=Decimal("1"), recipe_qty=Decimal("1000")))
    # FX rate USD -> COP = 4000
    test_db.execute(text(
        "INSERT INTO fx_rates (base_code, quote_code, rate, valid_from) "
        "VALUES ('USD','COP',4000,CURRENT_DATE)"))
    product = Product(name="P5 FX Product", is_active=True, is_sub_recipe=False)
    test_db.add(product)
    test_db.commit()
    test_db.add(ProductSize(product_id=product.id, size_name="medium",
                            scale_factor=Decimal("1.0"), is_default=True))
    test_db.add(RecipeIngredient(product_id=product.id, ingredient_id=ing.id,
                                 quantity=Decimal("100"), recipe_unit_id=unit.id,
                                 scales_with_size=False, process_yield_loss=Decimal("0")))
    test_db.commit()

    from backend.services.pricing_engine import PricingEngine
    PricingEngine(test_db).calculate_all_prices(store_id=store.id, save_to_db=True)
    snap = test_db.query(RecipeCostSnapshot).filter(
        RecipeCostSnapshot.product_id == product.id).one()
    # 1 USD * 4000 / (1000/1) = 4 COP/unit ; 100 units -> 400 COP
    assert abs(Decimal(str(snap.effective_cost)) - Decimal("400")) < Decimal("1")
    assert snap.fx_rate is not None and abs(Decimal(str(snap.fx_rate)) - Decimal("4000")) < Decimal("1")
