"""C2: denormalized ingredients.current_price + trigger; O(1) current-price view

v_current_ingredient_price did DISTINCT ON over the partitioned history table on
every read — a full scan at scale. Maintain ingredients.current_price via a
trigger on ingredient_price_history inserts and turn the view into a trivial
projection.

Revision ID: 0010_ingredient_current_price
Revises: 0009_avail_scope_cascade
Create Date: 2026-06-04
"""

from alembic import op

revision = "0010_ingredient_current_price"
down_revision = "0009_avail_scope_cascade"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
ALTER TABLE public.ingredients ADD COLUMN current_price price_amount;

-- Backfill from the latest historical price per ingredient.
UPDATE public.ingredients i SET current_price = lp.price
FROM (
  SELECT DISTINCT ON (ingredient_id) ingredient_id, price
  FROM public.ingredient_price_history
  ORDER BY ingredient_id, changed_at DESC
) lp
WHERE lp.ingredient_id = i.id;

CREATE OR REPLACE FUNCTION public.sync_ingredient_current_price()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE public.ingredients
  SET current_price = NEW.price, updated_at = now()
  WHERE id = NEW.ingredient_id;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_iph_sync_current_price
  AFTER INSERT ON public.ingredient_price_history
  FOR EACH ROW EXECUTE FUNCTION public.sync_ingredient_current_price();

-- v_product_modifier_cost depends on v_current_ingredient_price; drop it first
-- (recreated as a materialized view in 0011).
DROP VIEW IF EXISTS public.v_product_modifier_cost;
DROP VIEW IF EXISTS public.v_current_ingredient_price;

CREATE VIEW public.v_current_ingredient_price AS
SELECT id AS ingredient_id, current_price AS price
FROM public.ingredients
WHERE current_price IS NOT NULL;
"""

DOWNGRADE_SQL = r"""
DROP VIEW IF EXISTS public.v_current_ingredient_price;
CREATE VIEW public.v_current_ingredient_price AS
SELECT DISTINCT ON (iph.ingredient_id)
       iph.ingredient_id, iph.price, iph.source, iph.changed_at
FROM public.ingredient_price_history iph
ORDER BY iph.ingredient_id, iph.changed_at DESC;

CREATE VIEW public.v_product_modifier_cost AS
SELECT mie.modifier_id, cip.ingredient_id,
       SUM(mie.quantity_change * cip.price) AS cost_impact
FROM public.modifier_ingredient_effects mie
JOIN public.v_current_ingredient_price cip ON cip.ingredient_id = mie.ingredient_id
GROUP BY mie.modifier_id, cip.ingredient_id;

DROP TRIGGER IF EXISTS trg_iph_sync_current_price ON public.ingredient_price_history;
DROP FUNCTION IF EXISTS public.sync_ingredient_current_price();
ALTER TABLE public.ingredients DROP COLUMN current_price;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
