"""V3-1: current_price trigger must not regress on out-of-order inserts

sync_ingredient_current_price set current_price = NEW.price unconditionally, so a
backdated/corrective insert (older changed_at) overwrote the current price with a
stale value. Only advance when NEW is the most recent observation.

Revision ID: 0015_fix_current_price_trg
Revises: 0014_partition_automation
Create Date: 2026-06-04
"""

from alembic import op

revision = "0015_fix_current_price_trg"
down_revision = "0014_partition_automation"
branch_labels = None
depends_on = None


FIXED_FN = r"""
CREATE OR REPLACE FUNCTION public.sync_ingredient_current_price()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE public.ingredients i
  SET current_price = NEW.price, updated_at = now()
  WHERE i.id = NEW.ingredient_id
    AND NEW.changed_at >= COALESCE((
      SELECT max(h.changed_at)
      FROM public.ingredient_price_history h
      WHERE h.ingredient_id = NEW.ingredient_id
        AND h.changed_at <> NEW.changed_at
    ), NEW.changed_at);
  RETURN NEW;
END;
$$;
"""

ORIGINAL_FN = r"""
CREATE OR REPLACE FUNCTION public.sync_ingredient_current_price()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE public.ingredients
  SET current_price = NEW.price, updated_at = now()
  WHERE id = NEW.ingredient_id;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.execute(FIXED_FN)


def downgrade() -> None:
    op.execute(ORIGINAL_FN)
