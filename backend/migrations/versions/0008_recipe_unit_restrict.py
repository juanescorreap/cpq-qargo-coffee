"""M3: recipe_ingredients.recipe_unit_id ON DELETE RESTRICT

SET NULL would silently change a recipe's quantity semantics when a unit is
deleted. Units are a dimension (soft-deleted via is_active), so block deletion.

Revision ID: 0008_recipe_unit_restrict
Revises: 0007_pricing_nulls_distinct
Create Date: 2026-06-04
"""

from alembic import op

revision = "0008_recipe_unit_restrict"
down_revision = "0007_pricing_nulls_distinct"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.recipe_ingredients DROP CONSTRAINT fk_recipe_ingredients_unit"
    )
    op.execute(
        "ALTER TABLE public.recipe_ingredients ADD CONSTRAINT fk_recipe_ingredients_unit "
        "FOREIGN KEY (recipe_unit_id) REFERENCES public.recipe_units(id) ON DELETE RESTRICT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.recipe_ingredients DROP CONSTRAINT fk_recipe_ingredients_unit"
    )
    op.execute(
        "ALTER TABLE public.recipe_ingredients ADD CONSTRAINT fk_recipe_ingredients_unit "
        "FOREIGN KEY (recipe_unit_id) REFERENCES public.recipe_units(id) ON DELETE SET NULL"
    )
