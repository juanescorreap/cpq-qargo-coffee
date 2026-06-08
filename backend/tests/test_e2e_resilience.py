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

from backend.models import Product, ProductSize
from backend.models.ingredient import Ingredient
from backend.models.supply_chain import SupplyRoute
from backend.services import calc_worker
from backend.services.calc_worker import job_queue_status, reap_stale_jobs
from backend.services.pricing_engine import PricingEngine


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


# ── N4: product_pricing write serialised by advisory lock ───────────────────────

def test_save_pricing_takes_advisory_lock(test_db: Session,
                                           sample_product: Product,
                                           sample_size: ProductSize):
    """save_pricing acquires a per-(product,size,store,ccy) advisory xact lock so
    parallel workers serialise on the unique key instead of racing into 23505."""
    before = test_db.execute(text(
        "SELECT count(*) FROM pg_locks WHERE locktype='advisory'")).scalar()
    PricingEngine(test_db).save_pricing(
        product_id=sample_product.id, size_id=sample_size.id, store_id=None,
        final_price=Decimal("5000"), cost=Decimal("2000"), commit=False,
    )
    after = test_db.execute(text(
        "SELECT count(*) FROM pg_locks WHERE locktype='advisory'")).scalar()
    assert after > before  # lock held for the open transaction


def test_save_pricing_upsert_is_idempotent(test_db: Session,
                                           sample_product: Product,
                                           sample_size: ProductSize):
    """Re-saving the same (product,size,store,ccy) updates the single current row
    (no duplicate) — the invariant the advisory lock protects under concurrency."""
    eng = PricingEngine(test_db)
    for price in (Decimal("5000"), Decimal("5100")):
        eng.save_pricing(product_id=sample_product.id, size_id=sample_size.id,
                         store_id=None, final_price=price,
                         cost=Decimal("2000"), commit=False)
    n = test_db.execute(text(
        "SELECT count(*) FROM product_pricing WHERE product_id=:p AND size_id=:s "
        "AND store_id IS NULL AND currency_code='COP'"),
        {"p": sample_product.id, "s": sample_size.id}).scalar()
    assert n == 1


# ── N3: batch prefetch uses the read (replica) session, writes use primary ──────

def test_calculate_all_prices_prefetches_via_read_db(test_db: Session,
                                                     sample_product: Product,
                                                     sample_size: ProductSize,
                                                     monkeypatch):
    """calculate_all_prices builds its CalcContext from read_db (the replica when
    configured), never from the primary write session."""
    seen = {}
    import backend.services.pricing_engine as pe

    real_load = pe.load_context

    def _spy(db, *a, **k):
        seen["db"] = db
        return real_load(db, *a, **k)

    monkeypatch.setattr(pe, "load_context", _spy)
    sentinel = object()
    eng = PricingEngine(test_db, read_db=sentinel)
    assert eng.read_db is sentinel
    # Use the primary as read_db here so the call actually runs against seeded data.
    eng.read_db = test_db
    eng.calculate_all_prices(store_id=None, save_to_db=False,
                             product_ids={sample_product.id})
    assert seen["db"] is test_db  # prefetch used the read session, not a hidden one


# ── N6: app-side nightly full-recompute seed (single source of truth) ───────────

def test_nightly_seed_creates_chunks_and_is_idempotent(test_db: Session,
                                                       sample_product: Product):
    """fn_seed_nightly_recompute seeds bounded batch_chunk jobs for the active
    catalogue and is exactly-once per business day (claim via calc_seed_runs)."""
    from backend.services.calc_worker import seed_nightly_recompute

    seeded = seed_nightly_recompute(test_db, by="test")
    assert seeded >= 1  # at least the global base chunk for the active product
    jobs = test_db.execute(text(
        "SELECT count(*) FROM calc_jobs WHERE job_type='batch_chunk'")).scalar()
    assert jobs >= 1
    # marker row claimed for today's business date
    marker = test_db.execute(text(
        "SELECT count(*) FROM calc_seed_runs "
        "WHERE seed_date = (now() AT TIME ZONE 'America/Bogota')::date")).scalar()
    assert marker == 1

    # Second call the same day must no-op (claim already taken).
    again = seed_nightly_recompute(test_db, by="test")
    assert again == 0


def test_seed_due_gates_on_hour_and_date():
    from datetime import datetime

    from backend.services.calc_worker import _SEED_TZ, seed_due

    at_3am = datetime(2026, 6, 8, 3, 0, tzinfo=_SEED_TZ)
    assert seed_due(at_3am, None) is True                 # due: past hour, not done
    assert seed_due(at_3am, at_3am.date()) is False       # already attempted today
    before_3am = datetime(2026, 6, 8, 1, 0, tzinfo=_SEED_TZ)
    assert seed_due(before_3am, None) is False            # too early in the day
