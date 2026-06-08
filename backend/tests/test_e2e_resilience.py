"""E2E_ARCHITECTURE_AUDIT — resilience fixes G1/G2/G3.

G1: app-side reaper requeues stale 'running' jobs / dead-letters past max_attempts.
G2: /calc/status surfaces async recompute progress + dead-letters.
G3: the price UI now fires the outbox (enqueues a recompute) + HX-Trigger.
"""

import json
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.ingredient import Ingredient
from backend.models.supply_chain import SupplyRoute
from backend.services import calc_worker
from backend.services.calc_worker import job_queue_status, reap_stale_jobs


def _insert_job(db, *, status, attempts=0, max_attempts=5, locked_min_ago=None,
                payload=None, job_type="price_change"):
    locked = "NULL" if locked_min_ago is None else f"now() - make_interval(mins => {locked_min_ago})"
    jid = db.execute(text(
        f"INSERT INTO calc_jobs (job_type, status, attempts, max_attempts, locked_at, payload) "
        f"VALUES (:t, CAST(:s AS calc_job_status), :a, :ma, {locked}, CAST(:pl AS jsonb)) RETURNING id"
    ), {"t": job_type, "s": status, "a": attempts, "ma": max_attempts,
        "pl": json.dumps(payload or {})}).scalar()
    db.commit()
    return jid


def _status(db, jid):
    return db.execute(text("SELECT status FROM calc_jobs WHERE id=:i"), {"i": jid}).scalar()


# ── G1 ────────────────────────────────────────────────────────────────────────

def test_reaper_requeues_stale_running(test_db: Session):
    jid = _insert_job(test_db, status="running", attempts=1, locked_min_ago=20)
    reaped = reap_stale_jobs(test_db, stale_minutes=15)
    assert reaped >= 1
    assert _status(test_db, jid) == "pending"


def test_reaper_dead_letters_exhausted(test_db: Session):
    jid = _insert_job(test_db, status="running", attempts=5, max_attempts=5, locked_min_ago=20)
    reap_stale_jobs(test_db, stale_minutes=15)
    assert _status(test_db, jid) == "dead"


def test_reaper_ignores_fresh_running(test_db: Session):
    jid = _insert_job(test_db, status="running", attempts=0, locked_min_ago=2)
    reap_stale_jobs(test_db, stale_minutes=15)
    assert _status(test_db, jid) == "running"   # too fresh to reap


# ── G2 ────────────────────────────────────────────────────────────────────────

def test_status_counts_in_flight(test_db: Session):
    _insert_job(test_db, status="pending", payload={"ingredient_id": 4242})
    _insert_job(test_db, status="running", payload={"ingredient_id": 4242})
    st = job_queue_status(test_db, ingredient_id=4242)
    assert st["in_flight"] == 2 and st["done"] is False


def test_status_badge_endpoint(test_client):
    # No jobs for this ingredient -> "fresh".
    html = test_client.get("/calc/status?ingredient_id=999999").text
    assert "Costos al día" in html


def test_status_badge_shows_recalculating(test_client, test_db):
    _insert_job(test_db, status="pending", payload={"ingredient_id": 7777})
    html = test_client.get("/calc/status?ingredient_id=7777").text
    assert "Recalculando" in html


# ── G3 ────────────────────────────────────────────────────────────────────────

# ── N2: outbox job coalescing (E2E_ARCHITECTURE_AUDIT_V2) ───────────────────────

def test_outbox_coalesces_price_burst(test_db: Session, sample_ingredient: Ingredient):
    """A burst of price changes for the SAME ingredient collapses to ONE pending
    recompute job (partial-unique coalesce index) instead of N — no thundering herd."""
    for price in ("100", "110", "120", "130", "140"):
        test_db.execute(text(
            "INSERT INTO ingredient_price_history (ingredient_id, price, source) "
            "VALUES (:i, :p, 'manual')"
        ), {"i": sample_ingredient.id, "p": price})
    test_db.flush()
    n = test_db.execute(text(
        "SELECT count(*) FROM calc_jobs WHERE job_type='price_change' "
        "AND status='pending' AND coalesce_key = :k"
    ), {"k": f"price:{sample_ingredient.id}"}).scalar()
    assert n == 1


def test_outbox_distinct_ingredients_not_coalesced(test_db: Session, sample_ingredient: Ingredient):
    """Different ingredients keep separate jobs (coalescing is per natural key)."""
    other = Ingredient(
        name="coalesce-other", category="otros",
        purchase_unit="g", purchase_price=Decimal("5"), usage_unit="g",
        conversion_factor=Decimal("1"),
    )
    test_db.add(other)
    test_db.flush()
    for ing in (sample_ingredient.id, other.id):
        test_db.execute(text(
            "INSERT INTO ingredient_price_history (ingredient_id, price, source) "
            "VALUES (:i, 100, 'manual')"
        ), {"i": ing})
    test_db.flush()
    n = test_db.execute(text(
        "SELECT count(DISTINCT coalesce_key) FROM calc_jobs "
        "WHERE job_type='price_change' AND status='pending' "
        "AND coalesce_key IN (:a, :b)"
    ), {"a": f"price:{sample_ingredient.id}", "b": f"price:{other.id}"}).scalar()
    assert n == 2


# ── N1: bounded batch chunks (E2E_ARCHITECTURE_AUDIT_V2) ────────────────────────

def test_enqueue_batch_chunk_splits(test_db: Session):
    """_enqueue_batch_chunk emits jobs of at most CHUNK_SIZE products so one job
    can never be the whole catalogue (the OOM/dead-letter footgun)."""
    n = calc_worker.CHUNK_SIZE * 2 + 5
    calc_worker._enqueue_batch_chunk(test_db, None, set(range(1, n + 1)))
    test_db.flush()
    sizes = [
        r.sz for r in test_db.execute(text(
            "SELECT cardinality(product_ids) AS sz FROM calc_jobs "
            "WHERE job_type='batch_chunk' AND store_id IS NULL "
            "ORDER BY id DESC LIMIT 3"
        )).all()
    ]
    assert max(sizes) <= calc_worker.CHUNK_SIZE
    assert sum(sizes) == n


def test_price_ui_fires_outbox_and_trigger(test_client, test_db, sc_supply_route: SupplyRoute):
    r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx", data={
        "list_price": "1000", "qargo_price": "900", "currency_code": "COP",
        "price_per_unit": "per kg", "created_by": "test",
    })
    assert r.status_code == 200
    assert r.headers.get("HX-Trigger") == "prices-changed"
    # outbox trigger on supply_route_prices INSERT enqueued a route_change job
    n = test_db.execute(text(
        "SELECT count(*) FROM calc_jobs WHERE job_type='route_change' "
        "AND (payload->>'supply_route_id')::bigint = :r"), {"r": sc_supply_route.id}).scalar()
    assert n >= 1
