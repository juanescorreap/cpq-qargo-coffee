"""N1/N2: bounded batch chunks + outbox job coalescing

E2E_ARCHITECTURE_AUDIT_V2 closes the two critical findings of the async loop —
not its transport (already solid) but the SIZE of the work inside it:

  - N2 (coalescing): a bulk price ingest fired one outbox job PER ROW, so 1000
    price rows -> up to 1000 recompute jobs hammering the same products
    (thundering herd) + colliding on product_pricing's unique index. We add a
    ``coalesce_key`` + a partial unique index over (job_type, store, key) WHERE
    status='pending', and make the outbox triggers ``ON CONFLICT DO NOTHING``.
    A burst of changes to the same ingredient/route now collapses to ONE pending
    job. (store_id folded to -1 so NULL-scoped jobs still dedup.)

  - N1 (chunking): ``batch_chunk`` was a misnomer — the nightly seed enqueued one
    job per store carrying array_agg(ALL active products). One job = whole
    catalogue in a worker's memory + every snapshot pending before a single
    commit -> OOM dead-letters the entire store's recompute. We re-seed the
    nightly cron to emit BOUNDED chunks of CHUNK_SIZE products. The app-side
    expansion (_enqueue_batch_chunk) is chunked in the same change.

Both keep the existing idempotency contract (job 'done' in the same tx as its
writes) intact; they only bound fan-out and per-job memory.

Revision ID: 0025_calc_coalesce_chunk
Revises: 0024_calc_queue
Create Date: 2026-06-08
"""

from alembic import op

revision = "0025_calc_coalesce_chunk"
down_revision = "0024_calc_queue"
branch_labels = None
depends_on = None

# Keep in sync with backend/services/calc_worker.py CHUNK_SIZE.
CHUNK_SIZE = 200


UPGRADE_SQL = rf"""
-- ── N2: coalescing key + partial unique index ────────────────────────────────
ALTER TABLE public.calc_jobs ADD COLUMN coalesce_key text;

COMMENT ON COLUMN public.calc_jobs.coalesce_key IS
    'Natural dedup key for outbox jobs (e.g. price:<ing>, route:<route>). With '
    'the partial unique index, a burst of identical change events collapses to a '
    'single pending job. NULL = not coalesced (e.g. batch_chunk carries arrays).';

-- store_id folded to -1 so NULL-scoped (global) jobs still dedup against each other.
CREATE UNIQUE INDEX uix_calc_jobs_coalesce
    ON public.calc_jobs (job_type, (COALESCE(store_id, -1)), coalesce_key)
    WHERE status = 'pending' AND coalesce_key IS NOT NULL;

-- ── N2: outbox triggers now set the key + ON CONFLICT DO NOTHING ──────────────
CREATE OR REPLACE FUNCTION public.fn_enqueue_price_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.calc_jobs (job_type, payload, coalesce_key)
    VALUES ('price_change',
            jsonb_build_object('ingredient_id', NEW.ingredient_id,
                               'changed_at', now()),
            'price:' || NEW.ingredient_id)
    ON CONFLICT (job_type, (COALESCE(store_id, -1)), coalesce_key)
        WHERE status = 'pending' AND coalesce_key IS NOT NULL
    DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_enqueue_route_price_change()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_ingredient_id bigint;
BEGIN
    SELECT sr.ingredient_id INTO v_ingredient_id
      FROM public.supply_routes sr WHERE sr.id = NEW.supply_route_id;

    INSERT INTO public.calc_jobs (job_type, payload, coalesce_key)
    VALUES ('route_change',
            jsonb_build_object('supply_route_id', NEW.supply_route_id,
                               'ingredient_id', v_ingredient_id,
                               'changed_at', now()),
            'route:' || NEW.supply_route_id)
    ON CONFLICT (job_type, (COALESCE(store_id, -1)), coalesce_key)
        WHERE status = 'pending' AND coalesce_key IS NOT NULL
    DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_enqueue_route_assignment_change()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_ingredient_id bigint;
BEGIN
    SELECT sr.ingredient_id INTO v_ingredient_id
      FROM public.supply_routes sr WHERE sr.id = NEW.supply_route_id;

    INSERT INTO public.calc_jobs (job_type, store_id, payload, coalesce_key)
    VALUES ('route_change', NEW.store_id,
            jsonb_build_object('supply_route_id', NEW.supply_route_id,
                               'ingredient_id', v_ingredient_id,
                               'region_id', NEW.region_id,
                               'changed_at', now()),
            'route:' || NEW.supply_route_id)
    ON CONFLICT (job_type, (COALESCE(store_id, -1)), coalesce_key)
        WHERE status = 'pending' AND coalesce_key IS NOT NULL
    DO NOTHING;
    RETURN NEW;
END;
$$;

-- ── N1: re-seed nightly recompute as BOUNDED chunks (tolerant of no pg_cron) ──
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    RAISE NOTICE 'pg_cron not available; chunked seed applies once it is.';
    RETURN;
  END IF;

  -- cron.schedule upserts by job name -> this replaces the 0024 body.
  PERFORM cron.schedule('nightly_full_recompute', '0 3 * * *', $job$
    WITH numbered AS (
      SELECT p.id, ((row_number() OVER (ORDER BY p.id)) - 1) / {CHUNK_SIZE} AS grp
        FROM public.products p WHERE p.is_active
    ), chunks AS (
      SELECT grp, array_agg(id ORDER BY id) AS arr FROM numbered GROUP BY grp
    )
    INSERT INTO public.calc_jobs (job_type, store_id, product_ids, priority)
    SELECT 'batch_chunk', s.id, c.arr, 50
      FROM public.stores s CROSS JOIN chunks c
     WHERE s.is_active;

    WITH numbered AS (
      SELECT p.id, ((row_number() OVER (ORDER BY p.id)) - 1) / {CHUNK_SIZE} AS grp
        FROM public.products p WHERE p.is_active
    ), chunks AS (
      SELECT grp, array_agg(id ORDER BY id) AS arr FROM numbered GROUP BY grp
    )
    INSERT INTO public.calc_jobs (job_type, store_id, product_ids, priority)
    SELECT 'batch_chunk', NULL, c.arr, 60 FROM chunks c;
  $job$);
END;
$$;
"""

DOWNGRADE_SQL = r"""
-- Restore the unbounded nightly seed (0024 body).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.schedule('nightly_full_recompute', '0 3 * * *', $job$
      INSERT INTO public.calc_jobs (job_type, store_id, product_ids, priority)
      SELECT 'batch_chunk', s.id, c.arr, 50
        FROM public.stores s
        CROSS JOIN LATERAL (
          SELECT array_agg(p.id) AS arr FROM public.products p WHERE p.is_active
        ) c
       WHERE c.arr IS NOT NULL AND s.is_active;
      INSERT INTO public.calc_jobs (job_type, store_id, product_ids, priority)
      SELECT 'batch_chunk', NULL, array_agg(p.id), 60
        FROM public.products p WHERE p.is_active
       HAVING array_agg(p.id) IS NOT NULL;
    $job$);
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron reschedule skipped: %', SQLERRM;
END;
$$;

-- Restore the non-coalescing outbox triggers (0024 bodies).
CREATE OR REPLACE FUNCTION public.fn_enqueue_price_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO public.calc_jobs (job_type, payload)
    VALUES ('price_change',
            jsonb_build_object('ingredient_id', NEW.ingredient_id,
                               'changed_at', now()));
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_enqueue_route_price_change()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_ingredient_id bigint;
BEGIN
    SELECT sr.ingredient_id INTO v_ingredient_id
      FROM public.supply_routes sr WHERE sr.id = NEW.supply_route_id;
    INSERT INTO public.calc_jobs (job_type, payload)
    VALUES ('route_change',
            jsonb_build_object('supply_route_id', NEW.supply_route_id,
                               'ingredient_id', v_ingredient_id,
                               'changed_at', now()));
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.fn_enqueue_route_assignment_change()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_ingredient_id bigint;
BEGIN
    SELECT sr.ingredient_id INTO v_ingredient_id
      FROM public.supply_routes sr WHERE sr.id = NEW.supply_route_id;
    INSERT INTO public.calc_jobs (job_type, store_id, payload)
    VALUES ('route_change', NEW.store_id,
            jsonb_build_object('supply_route_id', NEW.supply_route_id,
                               'ingredient_id', v_ingredient_id,
                               'region_id', NEW.region_id,
                               'changed_at', now()));
    RETURN NEW;
END;
$$;

DROP INDEX IF EXISTS public.uix_calc_jobs_coalesce;
ALTER TABLE public.calc_jobs DROP COLUMN IF EXISTS coalesce_key;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
