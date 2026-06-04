"""V3-7: partial indexes for current-state (valid_until IS NULL) lookups

Temporal tables are queried for the currently-valid row constantly; partial
indexes on valid_until IS NULL keep those reads cheap at scale.

Revision ID: 0016_current_state_idx
Revises: 0015_fix_current_price_trg
Create Date: 2026-06-04
"""

from alembic import op

revision = "0016_current_state_idx"
down_revision = "0015_fix_current_price_trg"
branch_labels = None
depends_on = None


INDEXES = [
    ("idx_srp_route_current",
     "CREATE INDEX idx_srp_route_current ON public.supply_route_prices (supply_route_id) WHERE valid_until IS NULL"),
    ("idx_sra_region_current",
     "CREATE INDEX idx_sra_region_current ON public.supply_route_assignments (region_id, priority) WHERE valid_until IS NULL AND region_id IS NOT NULL"),
    ("idx_sra_store_current",
     "CREATE INDEX idx_sra_store_current ON public.supply_route_assignments (store_id, priority) WHERE valid_until IS NULL AND store_id IS NOT NULL"),
    ("idx_sip_current",
     "CREATE INDEX idx_sip_current ON public.store_ingredient_prices (store_id, ingredient_id) WHERE valid_until IS NULL"),
    ("idx_ssh_current",
     "CREATE INDEX idx_ssh_current ON public.store_supplier_history (store_id, ingredient_id) WHERE valid_until IS NULL"),
]


def upgrade() -> None:
    for _, ddl in INDEXES:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS public.{name}")
