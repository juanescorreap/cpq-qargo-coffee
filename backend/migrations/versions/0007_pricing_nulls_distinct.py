"""M1: product_pricing uniqueness via NULLS NOT DISTINCT (drop COALESCE hack)

Replace uq_product_pricing_current's COALESCE(store_id, 0) magic value with
PG15+ NULLS NOT DISTINCT, treating the global (store NULL) row as a single key.

Revision ID: 0007_pricing_nulls_distinct
Revises: 0006_subst_excl_temporal
Create Date: 2026-06-04
"""

from alembic import op

revision = "0007_pricing_nulls_distinct"
down_revision = "0006_subst_excl_temporal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_product_pricing_current")
    op.execute(
        "CREATE UNIQUE INDEX uq_product_pricing_current "
        "ON public.product_pricing (product_id, size_id, store_id, currency_code) "
        "NULLS NOT DISTINCT"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_product_pricing_current")
    op.execute(
        "CREATE UNIQUE INDEX uq_product_pricing_current "
        "ON public.product_pricing (product_id, size_id, COALESCE(store_id, 0), currency_code)"
    )
