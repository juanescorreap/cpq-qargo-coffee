"""C3: ingredient_availability scope CHECK + FKs ON DELETE CASCADE

Without a scope CHECK and with SET NULL FKs, deleting a route/region leaves
availability rows with both scopes NULL (orphan observations). Require a scope
and cascade deletes from the referenced route/region.

Revision ID: 0009_avail_scope_cascade
Revises: 0008_recipe_unit_restrict
Create Date: 2026-06-04
"""

from alembic import op

revision = "0009_avail_scope_cascade"
down_revision = "0008_recipe_unit_restrict"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.ingredient_availability DROP CONSTRAINT fk_ia_route")
    op.execute("ALTER TABLE public.ingredient_availability DROP CONSTRAINT fk_ia_region")
    op.execute(
        "ALTER TABLE public.ingredient_availability ADD CONSTRAINT fk_ia_route "
        "FOREIGN KEY (supply_route_id) REFERENCES public.supply_routes(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE public.ingredient_availability ADD CONSTRAINT fk_ia_region "
        "FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE public.ingredient_availability ADD CONSTRAINT ck_ia_scope "
        "CHECK (supply_route_id IS NOT NULL OR region_id IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.ingredient_availability DROP CONSTRAINT ck_ia_scope")
    op.execute("ALTER TABLE public.ingredient_availability DROP CONSTRAINT fk_ia_route")
    op.execute("ALTER TABLE public.ingredient_availability DROP CONSTRAINT fk_ia_region")
    op.execute(
        "ALTER TABLE public.ingredient_availability ADD CONSTRAINT fk_ia_route "
        "FOREIGN KEY (supply_route_id) REFERENCES public.supply_routes(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE public.ingredient_availability ADD CONSTRAINT fk_ia_region "
        "FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE SET NULL"
    )
