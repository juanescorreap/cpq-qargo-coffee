"""V3-5: normalize supply_route_prices price unit (price_per_unit -> FK)

price_per_unit is free text ('por kg'), so the costing engine cannot validate the
price's unit against the ingredient's conversion basis. Add a nullable FK to
recipe_units as the structured unit; price_per_unit is kept for now and deprecated.
A follow-up migration backfills and makes it NOT NULL.

Revision ID: 0018_srp_price_unit
Revises: 0017_fx_rates
Create Date: 2026-06-04
"""

from alembic import op

revision = "0018_srp_price_unit"
down_revision = "0017_fx_rates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.supply_route_prices "
        "ADD COLUMN price_unit_id bigint "
        "REFERENCES public.recipe_units(id) ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE INDEX idx_srp_price_unit ON public.supply_route_prices (price_unit_id)"
    )
    op.execute(
        "COMMENT ON COLUMN public.supply_route_prices.price_per_unit IS "
        "'DEPRECATED: usar price_unit_id (FK a recipe_units). Texto libre legado.'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_srp_price_unit")
    op.execute("ALTER TABLE public.supply_route_prices DROP COLUMN price_unit_id")
