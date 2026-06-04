"""V3-2 + V3-4: scheduled refresh of matview + yearly partition creation (pg_cron)

Automates the two derived/maintenance gaps:
  - REFRESH MATERIALIZED VIEW CONCURRENTLY mv_product_modifier_cost (every 15 min)
  - create next-year partitions ahead of the rollover

pg_cron may not be available/permitted on every connection (e.g. Supabase enables
it per-project). The migration is tolerant: if the extension or cron schema is not
available, it logs a NOTICE and continues instead of failing the upgrade.

Revision ID: 0020_pg_cron_jobs
Revises: 0019_bom_cycle_trigger
Create Date: 2026-06-04
"""

from alembic import op

revision = "0020_pg_cron_jobs"
down_revision = "0019_bom_cycle_trigger"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
DO $$
BEGIN
  BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_cron;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_cron not available (%); skipping scheduled jobs. Configure them manually.', SQLERRM;
    RETURN;
  END;

  -- Refresh the modifier-cost matview periodically (CONCURRENTLY uses uq_mv_pmc_modifier).
  PERFORM cron.schedule(
    'refresh_mv_modifier_cost', '*/15 * * * *',
    'REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_product_modifier_cost'
  );

  -- Create next year's partitions every December, ahead of the rollover.
  PERFORM cron.schedule(
    'ensure_partitions_next_year', '0 0 1 12 *',
    $job$
      SELECT public.ensure_yearly_partition('public.product_price_history',         (extract(year from now())::int + 1));
      SELECT public.ensure_yearly_partition('public.ingredient_price_history',      (extract(year from now())::int + 1));
      SELECT public.ensure_yearly_partition('public.recipe_cost_snapshots',         (extract(year from now())::int + 1));
      SELECT public.ensure_yearly_partition('public.competitor_price_observations', (extract(year from now())::int + 1));
    $job$
  );
END;
$$;
"""

DOWNGRADE_SQL = r"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('refresh_mv_modifier_cost');
    PERFORM cron.unschedule('ensure_partitions_next_year');
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron unschedule skipped: %', SQLERRM;
END;
$$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
