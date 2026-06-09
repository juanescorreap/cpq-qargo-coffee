"""#4 / G6: recipe_cost_snapshots monthly partitions + app-side maintenance

The nightly full recompute (N6) writes one snapshot per (product, store, size)
per business day, so recipe_cost_snapshots is the highest-volume dated table.
Two latent problems, both rooted in pg_cron (absent on Supabase):

  1. Future partitions were created ONLY by the pg_cron job ``ensure_partitions_
     next_year`` (0020). Without pg_cron, no partition past 2027 is ever created,
     so post-2027 rows fall into ``_default`` -- which then BLOCKS creating those
     months/years later (Postgres rejects a partition overlapping default rows).
  2. Yearly granularity makes retention all-or-nothing: you can only drop a whole
     year, never trim to a rolling window.

Fix (mirrors N6 / CLAUDE.md P6 -- one source of truth, app-side-safe):
  - Repartition recipe_cost_snapshots from YEARLY to MONTHLY. The parent stays
    ``PARTITION BY RANGE (calculated_at)``; only the child ranges change, so no
    parent/strategy swap and the parent indexes (idx_rcs_run, idx_rcs_psz) and
    FKs propagate to the new children automatically.
  - ``ensure_month_partition(parent, month)``: idempotent monthly-partition maker
    (sibling of 0014's ensure_yearly_partition).
  - ``maintenance_runs``: exactly-once-per-business-day claim table (sibling of
    0026's calc_seed_runs).
  - ``fn_run_partition_maintenance(by, ahead, retention)``: the single source of
    truth. Claims the day, then (a) creates snapshot monthly partitions ahead of
    now, (b) creates next-year partitions for the OTHER dated tables (replacing
    the dead pg_cron job), and (c) drops snapshot monthly partitions older than
    the retention window. Returns (created, dropped).
  - pg_cron becomes a thin ``SELECT fn_run_partition_maintenance('pg_cron')`` and
    the Python worker calls it with 'worker'; whoever runs first per day wins.

recipe_cost_snapshots is empty in production (greenfield), so the repartition is
instantaneous there; the migration still preserves any rows in dev/test DBs.

Revision ID: 0027_snapshot_monthly_partitions
Revises: 0026_nightly_seed_fn
Create Date: 2026-06-09
"""

from alembic import op

revision = "0027_snapshot_monthly_partitions"
down_revision = "0026_nightly_seed_fn"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
-- ── Helper: idempotent monthly partition maker (sibling of ensure_yearly) ─────
CREATE OR REPLACE FUNCTION public.ensure_month_partition(
    p_parent regclass, p_month date  -- any date in the target month
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    m  date := date_trunc('month', p_month)::date;
    nm text := format('%s_%s', p_parent::text, to_char(m, 'YYYY_MM'));
BEGIN
    IF to_regclass(nm) IS NULL THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',
            nm, p_parent::text, m, (m + interval '1 month')::date
        );
    END IF;
END;
$$;

COMMENT ON FUNCTION public.ensure_month_partition(regclass, date) IS
    'Create the monthly RANGE partition covering p_month if it does not exist. '
    'Idempotent. Used by fn_run_partition_maintenance to roll partitions forward '
    'app-side (no pg_cron dependency).';

-- ── Repartition recipe_cost_snapshots: YEARLY -> MONTHLY ──────────────────────
-- Park existing rows (no-op in prod: 0 rows), drop the yearly+default children
-- (they overlap the monthly ranges we create), create a monthly window covering
-- [oldest data or now()-1mo .. now()+3mo], recreate default, restore rows.
CREATE TEMP TABLE _rcs_tmp AS TABLE public.recipe_cost_snapshots;

DROP TABLE IF EXISTS public.recipe_cost_snapshots_2025;
DROP TABLE IF EXISTS public.recipe_cost_snapshots_2026;
DROP TABLE IF EXISTS public.recipe_cost_snapshots_2027;
DROP TABLE IF EXISTS public.recipe_cost_snapshots_default;

DO $$
DECLARE
    lo date := date_trunc('month', LEAST(
                 COALESCE((SELECT min(calculated_at) FROM _rcs_tmp), now()),
                 now() - interval '1 month'))::date;
    hi date := date_trunc('month', now() + interval '3 months')::date;
    m  date;
BEGIN
    m := lo;
    WHILE m <= hi LOOP
        PERFORM public.ensure_month_partition('public.recipe_cost_snapshots', m);
        m := (m + interval '1 month')::date;
    END LOOP;
END $$;

-- Safety net: catches rows outside the rolling window. Stays empty in normal
-- operation because the worker creates months ahead of any insert.
CREATE TABLE public.recipe_cost_snapshots_default
    PARTITION OF public.recipe_cost_snapshots DEFAULT;

-- id is GENERATED ALWAYS -> OVERRIDING SYSTEM VALUE to keep original ids.
INSERT INTO public.recipe_cost_snapshots OVERRIDING SYSTEM VALUE
    SELECT * FROM _rcs_tmp;
DROP TABLE _rcs_tmp;

-- ── Marker table: exactly-once-per-business-day claim (sibling of calc_seed_runs)
CREATE TABLE public.maintenance_runs (
    run_kind   text NOT NULL DEFAULT 'partitions',
    run_date   date NOT NULL,
    claimed_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_kind, run_date)
);

COMMENT ON TABLE public.maintenance_runs IS
    'One row per business day partition maintenance ran. The PK makes the claim '
    'atomic so pg_cron and the app-side worker can both call '
    'fn_run_partition_maintenance and it still runs exactly once per day.';

-- ── Single source of truth: roll partitions forward + retention ───────────────
CREATE OR REPLACE FUNCTION public.fn_run_partition_maintenance(
    p_by               text DEFAULT 'unknown',
    p_ahead_months     int  DEFAULT 3,
    p_retention_months int  DEFAULT 18
) RETURNS TABLE(created int, dropped int) LANGUAGE plpgsql AS $$
DECLARE
    v_date    date := (now() AT TIME ZONE 'America/Bogota')::date;
    v_created int := 0;
    v_dropped int := 0;
    m         date;
    cutoff    date;
    r         record;
BEGIN
    -- Atomic claim: first caller of the business day wins; others no-op.
    INSERT INTO public.maintenance_runs (run_kind, run_date, claimed_by)
    VALUES ('partitions', v_date, p_by)
    ON CONFLICT (run_kind, run_date) DO NOTHING;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 0, 0;
        RETURN;
    END IF;

    -- (a) Snapshot monthly partitions: current month .. +p_ahead_months ahead.
    m := date_trunc('month', now())::date;
    WHILE m <= date_trunc('month', now() + make_interval(months => p_ahead_months))::date LOOP
        IF to_regclass(format('public.recipe_cost_snapshots_%s', to_char(m, 'YYYY_MM'))) IS NULL THEN
            PERFORM public.ensure_month_partition('public.recipe_cost_snapshots', m);
            v_created := v_created + 1;
        END IF;
        m := (m + interval '1 month')::date;
    END LOOP;

    -- (b) Next-year partitions for the other dated tables (replaces the dead
    --     pg_cron ensure_partitions_next_year job; ensure_yearly is idempotent).
    PERFORM public.ensure_yearly_partition('public.product_price_history',         extract(year from now())::int + 1);
    PERFORM public.ensure_yearly_partition('public.ingredient_price_history',      extract(year from now())::int + 1);
    PERFORM public.ensure_yearly_partition('public.competitor_price_observations', extract(year from now())::int + 1);

    -- (c) Retention: drop snapshot MONTHLY partitions older than the window.
    --     Matches only recipe_cost_snapshots_YYYY_MM -> never touches _default
    --     nor any non-monthly child. Snapshots are regenerable (nightly recompute).
    cutoff := date_trunc('month', now() - make_interval(months => p_retention_months))::date;
    FOR r IN
        SELECT c.relname AS relname
          FROM pg_inherits i
          JOIN pg_class c ON c.oid = i.inhrelid
          JOIN pg_class p ON p.oid = i.inhparent
         WHERE p.relname = 'recipe_cost_snapshots'
           AND c.relname ~ '^recipe_cost_snapshots_[0-9]{4}_[0-9]{2}$'
           AND to_date(substring(c.relname FROM '([0-9]{4}_[0-9]{2})$'), 'YYYY_MM') < cutoff
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I', r.relname);
        v_dropped := v_dropped + 1;
    END LOOP;

    RETURN QUERY SELECT v_created, v_dropped;
END;
$$;

COMMENT ON FUNCTION public.fn_run_partition_maintenance(text, int, int) IS
    'Rolls partitions forward (snapshots monthly, other dated tables yearly) and '
    'drops snapshot months older than the retention window, exactly once per '
    'business day (claim via maintenance_runs). Called by both pg_cron and the '
    'app-side worker. Returns (created, dropped) partition counts.';

-- ── pg_cron: thin wrapper, replacing the 0020 yearly-partition job ────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    RAISE NOTICE 'pg_cron not available; the app-side worker maintains partitions via fn_run_partition_maintenance.';
    RETURN;
  END IF;

  -- Retire the 0020 job; superseded by fn_run_partition_maintenance.
  BEGIN
    PERFORM cron.unschedule('ensure_partitions_next_year');
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'ensure_partitions_next_year not scheduled; nothing to unschedule.';
  END;

  -- Daily, just after the nightly seed slot (0026 uses 03:00 / 03:50).
  PERFORM cron.schedule('partition_maintenance', '20 3 * * *',
    'SELECT public.fn_run_partition_maintenance(''pg_cron'');');

  -- Keep maintenance markers 180 days for observability (tiny table).
  PERFORM cron.schedule('maintenance_runs_cleanup', '55 3 * * *', $job$
    DELETE FROM public.maintenance_runs WHERE run_date < current_date - 180;
  $job$);
END;
$$;
"""

DOWNGRADE_SQL = r"""
-- ── pg_cron: restore the 0020 yearly-partition job ───────────────────────────
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    BEGIN PERFORM cron.unschedule('partition_maintenance');      EXCEPTION WHEN OTHERS THEN NULL; END;
    BEGIN PERFORM cron.unschedule('maintenance_runs_cleanup');   EXCEPTION WHEN OTHERS THEN NULL; END;
    PERFORM cron.schedule('ensure_partitions_next_year', '0 0 1 12 *', $job$
      SELECT public.ensure_yearly_partition('public.product_price_history',         (extract(year from now())::int + 1));
      SELECT public.ensure_yearly_partition('public.ingredient_price_history',      (extract(year from now())::int + 1));
      SELECT public.ensure_yearly_partition('public.recipe_cost_snapshots',         (extract(year from now())::int + 1));
      SELECT public.ensure_yearly_partition('public.competitor_price_observations', (extract(year from now())::int + 1));
    $job$);
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron reschedule skipped: %', SQLERRM;
END;
$$;

DROP FUNCTION IF EXISTS public.fn_run_partition_maintenance(text, int, int);
DROP TABLE IF EXISTS public.maintenance_runs;

-- ── Revert recipe_cost_snapshots: MONTHLY -> YEARLY (post-0014 state) ─────────
CREATE TEMP TABLE _rcs_tmp AS TABLE public.recipe_cost_snapshots;

DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT c.relname AS relname
          FROM pg_inherits i
          JOIN pg_class c ON c.oid = i.inhrelid
          JOIN pg_class p ON p.oid = i.inhparent
         WHERE p.relname = 'recipe_cost_snapshots'
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I', r.relname);
    END LOOP;
END $$;

CREATE TABLE public.recipe_cost_snapshots_2025 PARTITION OF public.recipe_cost_snapshots
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE public.recipe_cost_snapshots_2026 PARTITION OF public.recipe_cost_snapshots
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE public.recipe_cost_snapshots_2027 PARTITION OF public.recipe_cost_snapshots
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE public.recipe_cost_snapshots_default PARTITION OF public.recipe_cost_snapshots DEFAULT;

INSERT INTO public.recipe_cost_snapshots OVERRIDING SYSTEM VALUE
    SELECT * FROM _rcs_tmp;
DROP TABLE _rcs_tmp;

DROP FUNCTION IF EXISTS public.ensure_month_partition(regclass, date);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
