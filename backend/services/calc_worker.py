"""calc_jobs queue worker (ENGINE_SUPPLIER_PLAN_V2 §3.2 / §4 / T3).

Drains the ``calc_jobs`` table (queue + transactional outbox seeded by the 0024
triggers / pg_cron). Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` so many
workers never block each other. Each chunk writes its pricing + snapshots AND
marks the job ``done`` in the SAME transaction, so:

  - crash before commit  -> rollback, clean re-claim (no orphan rows);
  - crash after commit   -> job already done, never re-claimed (no duplicates).

Job types:
  - ``batch_chunk``  : recompute prices+snapshots for product_ids (or all) at store.
  - ``price_change`` / ``route_change`` : expand affected products via the reverse
    BOM closure and enqueue a ``batch_chunk`` (incremental recompute).
  - ``sourcing_sync``: run sync_store_supplier_history for the store.

Heavy compute lives here (Python), not in pg_cron — the scheduler only seeds and
reaps the queue.
"""

from __future__ import annotations

import logging
import socket
from typing import Optional

from sqlalchemy import text

from backend.services.pricing_engine import PricingEngine
from backend.services.sourcing_sync import sync_store_supplier_history

logger = logging.getLogger("calc_worker")

_CLAIM_SQL = text(
    """
    WITH j AS (
        SELECT id FROM public.calc_jobs
         WHERE status = 'pending' AND not_before <= now()
         ORDER BY priority, not_before
         FOR UPDATE SKIP LOCKED
         LIMIT 1
    )
    UPDATE public.calc_jobs c
       SET status = 'running', locked_at = now(), locked_by = :worker,
           attempts = attempts + 1
      FROM j WHERE c.id = j.id
    RETURNING c.id, c.job_type, c.store_id, c.product_ids, c.payload,
              c.attempts, c.max_attempts
    """
)


def reverse_bom_closure(db, ingredient_id: int) -> set:
    """Products that use an ingredient directly or through any sub-recipe level."""
    rows = db.execute(
        text(
            """
            WITH RECURSIVE affected AS (
                SELECT product_id FROM public.recipe_ingredients
                 WHERE ingredient_id = :ing
                UNION
                SELECT rsr.parent_product_id
                  FROM public.recipe_sub_recipes rsr
                  JOIN affected a ON rsr.sub_recipe_id = a.product_id
            )
            SELECT DISTINCT product_id FROM affected
            """
        ),
        {"ing": ingredient_id},
    ).all()
    return {r.product_id for r in rows}


def claim_job(db, worker_id: str):
    """Claim one due job atomically. Returns a row mapping or ``None``."""
    return db.execute(_CLAIM_SQL, {"worker": worker_id}).mappings().first()


def _mark_done(db, job_id: int) -> None:
    db.execute(
        text(
            "UPDATE public.calc_jobs SET status='done', finished_at=now() "
            "WHERE id = :id"
        ),
        {"id": job_id},
    )


def _requeue(db, job, error: str) -> None:
    """Failure: dead-letter past max_attempts, else re-pending with exp backoff.

    Assumes the job's WORK was already rolled back (savepoint) so the claimed job
    row is still present in the transaction.
    """
    db.execute(
        text(
            "UPDATE public.calc_jobs "
            "SET status = (CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END)::calc_job_status, "
            "    not_before = now() + (interval '1 minute' * power(2, attempts)), "
            "    locked_at = NULL, locked_by = NULL, last_error = :err "
            "WHERE id = :id"
        ),
        {"err": error[:2000], "id": job["id"]},
    )
    db.commit()


def _enqueue_batch_chunk(db, store_id: Optional[int], product_ids: set) -> None:
    if not product_ids:
        return
    db.execute(
        text(
            "INSERT INTO public.calc_jobs (job_type, store_id, product_ids, priority) "
            "VALUES ('batch_chunk', :s, CAST(:pids AS bigint[]), 80)"
        ),
        {"s": store_id, "pids": list(product_ids)},
    )


def process_job(db, job, worker_id: str = "worker") -> None:
    """Dispatch + persist + mark done atomically. On failure the job's work is
    rolled back to a savepoint (the claimed job row survives) and the job is
    requeued / dead-lettered.

    The savepoint makes this correct under both real separate-transaction usage
    and a single wrapping test transaction.
    """
    sp = db.begin_nested()
    try:
        jtype = job["job_type"]
        if jtype == "batch_chunk":
            pids = set(job["product_ids"] or []) or None
            PricingEngine(db).calculate_all_prices(
                store_id=job["store_id"], save_to_db=True,
                triggered_by="batch_chunk", product_ids=pids, commit=False,
            )
        elif jtype in ("price_change", "route_change"):
            payload = job["payload"] or {}
            ing = payload.get("ingredient_id")
            affected = reverse_bom_closure(db, ing) if ing is not None else set()
            store = job["store_id"] or payload.get("store_id")
            _enqueue_batch_chunk(db, store, affected)
        elif jtype == "sourcing_sync":
            if job["store_id"] is not None:
                sync_store_supplier_history(db, job["store_id"], commit=False)
        else:
            raise ValueError(f"unknown job_type {jtype!r}")

        _mark_done(db, job["id"])
        sp.commit()       # release savepoint
        db.commit()       # persist work + job-done together
    except Exception as exc:  # noqa: BLE001 — any failure requeues the job
        logger.warning("job %s failed: %s", job["id"], exc)
        sp.rollback()     # undo only this job's work; claimed row stays
        _requeue(db, job, str(exc))
        db.commit()


def run_worker(db, worker_id: Optional[str] = None, max_jobs: Optional[int] = None) -> int:
    """Process pending jobs until the queue is empty (or max_jobs reached).

    Returns the number of jobs processed. Intended to be invoked by a long-lived
    process or a pg_cron-triggered tick.
    """
    worker_id = worker_id or f"{socket.gethostname()}:{id(object())}"
    processed = 0
    while max_jobs is None or processed < max_jobs:
        job = claim_job(db, worker_id)
        if job is None:
            break
        process_job(db, job, worker_id)
        processed += 1
    return processed
