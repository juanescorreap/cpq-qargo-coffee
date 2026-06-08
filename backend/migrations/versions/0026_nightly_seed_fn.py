"""N6: app-side-safe nightly full-recompute seed (single source of truth)

The nightly full recompute lived ONLY in pg_cron (0024/0025). On Supabase pg_cron
is optional and frequently unavailable, so on those hosts the periodic full sweep
never runs — only event-driven (outbox) recomputes do, and drift (new products,
manual data fixes, formula changes) is never reconciled. G1 already established
"don't rely on pg_cron for liveness" and gave the REAPER an app-side mirror, but
not the SEED. This closes that asymmetry.

Design (mirrors CLAUDE.md P6 — one source of truth):
  - calc_seed_runs: a marker table keyed by (seed_kind, seed_date). The first
    caller of the day wins the INSERT; everyone else no-ops. Exactly-once per
    BUSINESS day (America/Bogota) regardless of who fires.
  - fn_seed_nightly_recompute(p_by): claims the marker, and only if it won, seeds
    BOUNDED batch_chunk jobs (CHUNK_SIZE=200, same as N1) — one set per active
    store + a global base set. Returns the number of jobs seeded (0 = already
    seeded today). The claim + the job inserts share one transaction, so the seed
    is all-or-nothing.
  - BOTH the pg_cron job AND the Python worker call this one function. pg_cron
    becomes a thin `SELECT fn_seed_nightly_recompute('pg_cron')`; the worker calls
    it with 'worker'. Whoever runs first per business date wins; the other no-ops.

Revision ID: 0026_nightly_seed_fn
Revises: 0025_calc_coalesce_chunk
Create Date: 2026-06-08
"""

from alembic import op

revision = "0026_nightly_seed_fn"
down_revision = "0025_calc_coalesce_chunk"
branch_labels = None
depends_on = None

# Keep in sync with backend/services/calc_worker.py CHUNK_SIZE and migration 0025.
CHUNK_SIZE = 200


UPGRADE_SQL = rf"""
-- ── Marker table: exactly-once-per-business-day claim ────────────────────────
CREATE TABLE public.calc_seed_runs (
    seed_kind  text NOT NULL DEFAULT 'nightly_full',
    seed_date  date NOT NULL,
    claimed_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (seed_kind, seed_date)
);

COMMENT ON TABLE public.calc_seed_runs IS
    'One row per business day the nightly full recompute was seeded. The PK makes '
    'the claim atomic, so pg_cron and the app-side worker can both call '
    'fn_seed_nightly_recompute and the seed still runs exactly once per day.';

-- ── Single source of truth: seed the nightly full recompute (chunked) ─────────
CREATE OR REPLACE FUNCTION public.fn_seed_nightly_recompute(p_by text DEFAULT 'unknown')
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
    v_date  date := (now() AT TIME ZONE 'America/Bogota')::date;
    v_total integer := 0;
    v_n     integer;
BEGIN
    -- Atomic claim: the first caller of the business day wins; others no-op.
    INSERT INTO public.calc_seed_runs (seed_kind, seed_date, claimed_by)
    VALUES ('nightly_full', v_date, p_by)
    ON CONFLICT (seed_kind, seed_date) DO NOTHING;

    IF NOT FOUND THEN
        RETURN 0;  -- already seeded today
    END IF;

    -- Per active store: bounded chunks of CHUNK_SIZE active products.
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
    GET DIAGNOSTICS v_n = ROW_COUNT;
    v_total := v_total + v_n;

    -- Global base run (store_id NULL): bounded chunks.
    WITH numbered AS (
        SELECT p.id, ((row_number() OVER (ORDER BY p.id)) - 1) / {CHUNK_SIZE} AS grp
          FROM public.products p WHERE p.is_active
    ), chunks AS (
        SELECT grp, array_agg(id ORDER BY id) AS arr FROM numbered GROUP BY grp
    )
    INSERT INTO public.calc_jobs (job_type, store_id, product_ids, priority)
    SELECT 'batch_chunk', NULL, c.arr, 60 FROM chunks c;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    v_total := v_total + v_n;

    RETURN v_total;
END;
$$;

COMMENT ON FUNCTION public.fn_seed_nightly_recompute(text) IS
    'Seeds the nightly full recompute as BOUNDED batch_chunk jobs, exactly once '
    'per business day (claim via calc_seed_runs). Called by both pg_cron and the '
    'app-side worker. Returns jobs seeded (0 = already done today).';

-- ── pg_cron: thin wrapper over the function (tolerant of pg_cron absence) ─────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    RAISE NOTICE 'pg_cron not available; the app-side worker seeds via fn_seed_nightly_recompute.';
    RETURN;
  END IF;

  -- Replaces the inline 0025 body. Same 3am slot; the function self-guards.
  PERFORM cron.schedule('nightly_full_recompute', '0 3 * * *',
    'SELECT public.fn_seed_nightly_recompute(''pg_cron'');');

  -- Retain seed markers 180 days (tiny table; for observability of past runs).
  PERFORM cron.schedule('calc_seed_runs_cleanup', '50 3 * * *', $job$
    DELETE FROM public.calc_seed_runs WHERE seed_date < current_date - 180;
  $job$);
END;
$$;
"""

DOWNGRADE_SQL = rf"""
-- Restore the 0025 inline chunked pg_cron seed body.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('calc_seed_runs_cleanup');
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
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron reschedule skipped: %', SQLERRM;
END;
$$;

DROP FUNCTION IF EXISTS public.fn_seed_nightly_recompute(text);
DROP TABLE IF EXISTS public.calc_seed_runs;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
