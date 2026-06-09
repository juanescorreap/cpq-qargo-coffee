"""E2E audit follow-up: covering index for calc_jobs.store_id + drop stray
product_modifier_costs table.

Two reconciliations surfaced by the end-to-end schema review:

1. calc_jobs.store_id is a foreign key with no covering index, so a store
   delete (and any join over the key) seq-scans calc_jobs. Add the index. The
   other unindexed FKs point at the tiny currencies lookup, where the planner
   seq-scans regardless, so indexing them would only add write overhead.

2. product_modifier_costs was dropped in 0011 (its derived value moved to the
   mv_product_modifier_cost materialized view). It nevertheless lingered on
   environments whose schema had ever been provisioned by Base.metadata.
   create_all() instead of the migrations, leaving an empty, unused "ghost"
   table that diverged from the migration-defined schema. Drop it so the schema
   matches the intended post-0011 state everywhere. Idempotent (IF EXISTS), and
   a no-op where the table was already correctly absent.

Revision ID: 0028_fk_covering_indexes
Revises: 0027_snapshot_monthly_partitions
Create Date: 2026-06-09
"""

from alembic import op

revision = "0028_fk_covering_indexes"
down_revision = "0027_snapshot_monthly_partitions"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE INDEX IF NOT EXISTS idx_calc_jobs_store
    ON public.calc_jobs (store_id);

-- Remove the stray table superseded by mv_product_modifier_cost in 0011.
-- Empty and unreferenced (verified: no inbound FKs / view dependencies).
DROP TABLE IF EXISTS public.product_modifier_costs;
"""

DOWNGRADE_SQL = r"""
DROP INDEX IF EXISTS public.idx_calc_jobs_store;
-- The product_modifier_costs table is intentionally NOT recreated: it is not
-- part of the intended schema at or after revision 0011, so resurrecting it
-- would re-introduce the very divergence this migration removes.
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
