"""A3: partition automation helper + explicit 2027 partitions

Adds ensure_yearly_partition(parent, year) so future partitions can be created
ahead of time (schedule via pg_cron / pg_partman) instead of relying on the
DEFAULT catch-all, and pre-creates 2027 partitions for the dated history tables.

Revision ID: 0014_partition_automation
Revises: 0013_competitor_split
Create Date: 2026-06-04
"""

from alembic import op

revision = "0014_partition_automation"
down_revision = "0013_competitor_split"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION public.ensure_yearly_partition(
    p_parent regclass, p_year int
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    part_name text := format('%s_%s', p_parent::text, p_year);
BEGIN
    IF to_regclass(part_name) IS NULL THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',
            part_name, p_parent::text,
            format('%s-01-01', p_year), format('%s-01-01', p_year + 1)
        );
    END IF;
END;
$$;

SELECT public.ensure_yearly_partition('public.product_price_history', 2027);
SELECT public.ensure_yearly_partition('public.ingredient_price_history', 2027);
SELECT public.ensure_yearly_partition('public.recipe_cost_snapshots', 2027);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.product_price_history_2027")
    op.execute("DROP TABLE IF EXISTS public.ingredient_price_history_2027")
    op.execute("DROP TABLE IF EXISTS public.recipe_cost_snapshots_2027")
    op.execute("DROP FUNCTION IF EXISTS public.ensure_yearly_partition(regclass, int)")
