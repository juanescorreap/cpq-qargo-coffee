"""V3-8: prevent BOM cycles in recipe_sub_recipes (A->B->A)

A CHECK blocks A->A but not deeper cycles, which would make recursive cost
expansion loop. A recursive trigger rejects any insert/update that would close a
cycle.

Revision ID: 0019_bom_cycle_trigger
Revises: 0018_srp_price_unit
Create Date: 2026-06-04
"""

from alembic import op

revision = "0019_bom_cycle_trigger"
down_revision = "0018_srp_price_unit"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION public.fn_recipe_no_cycle()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (
    WITH RECURSIVE chain(pid) AS (
      SELECT NEW.sub_recipe_id
      UNION
      SELECT rsr.sub_recipe_id
      FROM public.recipe_sub_recipes rsr
      JOIN chain ON rsr.parent_product_id = chain.pid
    )
    SELECT 1 FROM chain WHERE pid = NEW.parent_product_id
  ) THEN
    RAISE EXCEPTION 'recipe_sub_recipes: cycle detected (% -> %)',
      NEW.parent_product_id, NEW.sub_recipe_id;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_recipe_sub_recipes_no_cycle
  BEFORE INSERT OR UPDATE ON public.recipe_sub_recipes
  FOR EACH ROW EXECUTE FUNCTION public.fn_recipe_no_cycle();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_recipe_sub_recipes_no_cycle ON public.recipe_sub_recipes")
    op.execute("DROP FUNCTION IF EXISTS public.fn_recipe_no_cycle()")
