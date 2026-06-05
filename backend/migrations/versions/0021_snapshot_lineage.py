"""B3/T2: snapshot lineage — size_id, formula_version, batch_run_id, FX trail

recipe_cost_snapshots could not represent per-size cost (no size_id), nor the
formula version, nor the batch run it belonged to, nor the FX rate used to
normalise a foreign-currency supplier price. Without these the snapshot is not
reconstructible (violates the lineage goal T2).

Adds (all on the partitioned parent → propagate to every partition, PG11+):
  - size_id          FK product_sizes  (nullable: old rows predate it)
  - formula_version  NOT NULL DEFAULT 'v1' (fast default, no rewrite)
  - batch_run_id     uuid (groups a run; for retention/observability, NOT dedup)
  - fx_rate          numeric(18,8) + fx_rate_date date (aggregate conversion;
                     per-line FX always lives in snapshot_detail JSONB)

Revision ID: 0021_snapshot_lineage
Revises: 0020_pg_cron_jobs
Create Date: 2026-06-05
"""

from alembic import op

revision = "0021_snapshot_lineage"
down_revision = "0020_pg_cron_jobs"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
ALTER TABLE public.recipe_cost_snapshots
  ADD COLUMN size_id         bigint,
  ADD COLUMN formula_version varchar(40) NOT NULL DEFAULT 'v1',
  ADD COLUMN batch_run_id    uuid,
  ADD COLUMN fx_rate         numeric(18, 8),
  ADD COLUMN fx_rate_date    date;

-- size_id nullable so existing snapshots remain valid; FK validates new rows.
ALTER TABLE public.recipe_cost_snapshots
  ADD CONSTRAINT fk_rcs_size FOREIGN KEY (size_id)
      REFERENCES public.product_sizes(id) ON DELETE RESTRICT;

-- Group all snapshots of one batch run (retention / run-vs-run comparison).
CREATE INDEX idx_rcs_run
  ON public.recipe_cost_snapshots (batch_run_id);

-- Latest cost per (product, store, size). calculated_at is the partition key.
CREATE INDEX idx_rcs_psz
  ON public.recipe_cost_snapshots (product_id, store_id, size_id, calculated_at DESC);
"""

DOWNGRADE_SQL = r"""
DROP INDEX IF EXISTS public.idx_rcs_psz;
DROP INDEX IF EXISTS public.idx_rcs_run;
ALTER TABLE public.recipe_cost_snapshots
  DROP CONSTRAINT IF EXISTS fk_rcs_size,
  DROP COLUMN IF EXISTS fx_rate_date,
  DROP COLUMN IF EXISTS fx_rate,
  DROP COLUMN IF EXISTS batch_run_id,
  DROP COLUMN IF EXISTS formula_version,
  DROP COLUMN IF EXISTS size_id;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
