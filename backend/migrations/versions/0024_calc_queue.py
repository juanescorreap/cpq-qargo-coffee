"""T3/B5: calc_jobs queue (outbox) + price-change triggers + guarded ingestion

Closes the event-transport and ingestion gaps using Postgres natively (no broker):

  - calc_jobs: one table = work queue + transactional outbox + checkpoint +
    dead-letter. Workers claim with FOR UPDATE SKIP LOCKED.
  - Outbox triggers on ingredient_price_history / supply_route_prices /
    supply_route_assignments enqueue a recompute job IN THE SAME TRANSACTION as
    the price/route write -> a committed price change can never lose its recompute.
  - fn_ingest_route_price: temporal close+insert guarded by a per-route advisory
    xact lock (fine-grained; avoids SERIALIZABLE global retries). The new-price
    INSERT itself fires the outbox trigger, so the function does NOT enqueue
    manually (single enqueue point).
  - pg_cron: nightly full-recompute seeding, a reaper (requeue stale 'running'
    with exponential backoff, dead-letter past max_attempts) and snapshot
    retention. Tolerant if pg_cron is unavailable (same pattern as 0020).

Heavy Decimal compute runs in Python workers polling the claim; pg_cron only
seeds and maintains the queue.

Revision ID: 0024_calc_queue
Revises: 0023_fn_active_substitute
Create Date: 2026-06-05
"""

from alembic import op

revision = "0024_calc_queue"
down_revision = "0023_fn_active_substitute"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
-- gen_random_uuid(): built-in PG13+, but keep pgcrypto tolerant for older setups.
DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS pgcrypto;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pgcrypto not available (%); relying on built-in gen_random_uuid.', SQLERRM;
END;
$$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'calc_job_status') THEN
    CREATE TYPE public.calc_job_status AS ENUM
      ('pending', 'running', 'done', 'failed', 'dead');
  END IF;
END;
$$;

CREATE TABLE public.calc_jobs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       uuid        NOT NULL DEFAULT gen_random_uuid(),
    job_type     varchar(40) NOT NULL,        -- batch_chunk|price_change|route_change|sourcing_sync
    store_id     bigint,
    product_ids  bigint[]    NOT NULL DEFAULT '{}',
    payload      jsonb       NOT NULL DEFAULT '{}',
    status       public.calc_job_status NOT NULL DEFAULT 'pending',
    priority     smallint    NOT NULL DEFAULT 100,   -- lower = sooner
    attempts     smallint    NOT NULL DEFAULT 0,
    max_attempts smallint    NOT NULL DEFAULT 5,
    locked_at    timestamptz,
    locked_by    text,
    not_before   timestamptz NOT NULL DEFAULT now(), -- backoff gate
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    CONSTRAINT fk_calc_jobs_store FOREIGN KEY (store_id)
        REFERENCES public.stores(id) ON DELETE CASCADE
);

-- Claim index: only pending rows that are due.
CREATE INDEX idx_calc_jobs_claim
    ON public.calc_jobs (priority, not_before)
    WHERE status = 'pending';

-- Reaper index: stale running jobs.
CREATE INDEX idx_calc_jobs_running
    ON public.calc_jobs (locked_at)
    WHERE status = 'running';

COMMENT ON TABLE public.calc_jobs IS
    'Queue + transactional outbox for the cost engine. Claim with FOR UPDATE '
    'SKIP LOCKED. A chunk marks itself done in the same tx as its writes -> '
    'idempotent (crash-before=clean retry, crash-after=no re-claim).';

-- ── Outbox: ingredient catalogue price change ────────────────────────────────
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

CREATE TRIGGER trg_outbox_ingredient_price
    AFTER INSERT ON public.ingredient_price_history
    FOR EACH ROW EXECUTE FUNCTION public.fn_enqueue_price_change();

-- ── Outbox: supply route price change (resolve ingredient via supply_routes) ──
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

CREATE TRIGGER trg_outbox_route_price
    AFTER INSERT ON public.supply_route_prices
    FOR EACH ROW EXECUTE FUNCTION public.fn_enqueue_route_price_change();

-- ── Outbox: supply route assignment change (which route a store/region uses) ──
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

CREATE TRIGGER trg_outbox_route_assignment
    AFTER INSERT ON public.supply_route_assignments
    FOR EACH ROW EXECUTE FUNCTION public.fn_enqueue_route_assignment_change();

-- ── Guarded ingestion of supplier prices (B5) ────────────────────────────────
-- p_price_unit_id: canonical FK to recipe_units (added by 0018). p_per_unit: the
-- legacy free-text price_per_unit, still NOT NULL/deprecated, so kept as a param.
CREATE OR REPLACE FUNCTION public.fn_ingest_route_price(
    p_route_id      bigint,
    p_list          price_amount,
    p_qargo         price_amount,
    p_ccy           char(3),
    p_price_unit_id bigint,
    p_per_unit      varchar,
    p_source        varchar,
    p_by            varchar,
    p_valid_from    date DEFAULT CURRENT_DATE
)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE v_id bigint;
BEGIN
    -- Serialise ONLY this route for the duration of the transaction.
    PERFORM pg_advisory_xact_lock(hashtextextended('srp:' || p_route_id, 0));

    IF p_qargo > p_list THEN
        RAISE EXCEPTION 'qargo_price (%) > list_price (%)', p_qargo, p_list;
    END IF;

    -- Close the currently-open price (close+insert pattern; matches 0012).
    UPDATE public.supply_route_prices
       SET valid_until = p_valid_from
     WHERE supply_route_id = p_route_id
       AND valid_until IS NULL;

    -- Insert the new open price. This INSERT fires trg_outbox_route_price,
    -- which enqueues the recompute job -> no manual enqueue here.
    INSERT INTO public.supply_route_prices
        (supply_route_id, list_price, qargo_price, currency_code,
         price_unit_id, price_per_unit, valid_from, source, created_by)
    VALUES (p_route_id, p_list, p_qargo, p_ccy,
            p_price_unit_id, p_per_unit, p_valid_from, p_source, p_by)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- ── pg_cron: seed + reaper + retention (tolerant) ────────────────────────────
DO $$
BEGIN
  BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_cron;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_cron not available (%); configure calc_jobs maintenance manually.', SQLERRM;
    RETURN;
  END;

  -- (a) Nightly full recompute: seed one job per active store + a global (base) run.
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

  -- (b) Reaper: requeue stale 'running' with exponential backoff; dead-letter past max.
  PERFORM cron.schedule('calc_jobs_reaper', '*/5 * * * *', $job$
    UPDATE public.calc_jobs
       SET status     = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
           not_before = now() + (interval '1 minute' * power(2, attempts)),
           locked_at  = NULL,
           locked_by  = NULL
     WHERE status = 'running'
       AND locked_at < now() - interval '15 minutes';
  $job$);

  -- (c) Snapshot retention: append-only table grows; keep 90 days.
  PERFORM cron.schedule('snapshot_retention', '30 3 * * *', $job$
    DELETE FROM public.recipe_cost_snapshots
     WHERE calculated_at < now() - interval '90 days';
  $job$);

  -- (d) Finished-job cleanup: keep done/dead rows 14 days for observability.
  PERFORM cron.schedule('calc_jobs_cleanup', '45 3 * * *', $job$
    DELETE FROM public.calc_jobs
     WHERE status IN ('done', 'dead')
       AND finished_at < now() - interval '14 days';
  $job$);
END;
$$;
"""

DOWNGRADE_SQL = r"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('nightly_full_recompute');
    PERFORM cron.unschedule('calc_jobs_reaper');
    PERFORM cron.unschedule('snapshot_retention');
    PERFORM cron.unschedule('calc_jobs_cleanup');
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron unschedule skipped: %', SQLERRM;
END;
$$;

DROP FUNCTION IF EXISTS public.fn_ingest_route_price(
    bigint, price_amount, price_amount, char, bigint, varchar, varchar, varchar, date);

DROP TRIGGER IF EXISTS trg_outbox_route_assignment ON public.supply_route_assignments;
DROP TRIGGER IF EXISTS trg_outbox_route_price      ON public.supply_route_prices;
DROP TRIGGER IF EXISTS trg_outbox_ingredient_price ON public.ingredient_price_history;

DROP FUNCTION IF EXISTS public.fn_enqueue_route_assignment_change();
DROP FUNCTION IF EXISTS public.fn_enqueue_route_price_change();
DROP FUNCTION IF EXISTS public.fn_enqueue_price_change();

DROP TABLE IF EXISTS public.calc_jobs;
DROP TYPE  IF EXISTS public.calc_job_status;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
