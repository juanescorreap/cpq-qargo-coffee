"""allow direct supply routes without a route-level endpoint

CLAUDE.md business rule: a direct purchase buys straight from the manufacturer
and the manufacturer is recorded in ingredient_supplier_refs, so a direct route
may legitimately carry neither manufacturer_id nor distributor_id on the route
row. The validated DDL's ck_supply_routes_endpoint (manufacturer OR distributor)
was stricter than the documented model. Relax it to also accept is_direct.

Revision ID: 0005_sr_direct_endpoint
Revises: 0004_category_slug_underscore
Create Date: 2026-06-04
"""

from alembic import op

revision = "0005_sr_direct_endpoint"
down_revision = "0004_category_slug_underscore"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.supply_routes DROP CONSTRAINT ck_supply_routes_endpoint")
    op.execute(
        "ALTER TABLE public.supply_routes ADD CONSTRAINT ck_supply_routes_endpoint "
        "CHECK (is_direct = true OR manufacturer_id IS NOT NULL OR distributor_id IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.supply_routes DROP CONSTRAINT ck_supply_routes_endpoint")
    op.execute(
        "ALTER TABLE public.supply_routes ADD CONSTRAINT ck_supply_routes_endpoint "
        "CHECK (manufacturer_id IS NOT NULL OR distributor_id IS NOT NULL)"
    )
