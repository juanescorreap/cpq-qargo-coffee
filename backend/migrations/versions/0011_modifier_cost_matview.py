"""C4: drop product_modifier_costs table -> mv_product_modifier_cost matview

product_modifier_costs persisted a derived value (effects x ingredient prices)
with no invalidation -> silently stale. Replace with a materialized view over
ingredients.current_price (refresh on price/effect changes).

Revision ID: 0011_modifier_cost_matview
Revises: 0010_ingredient_current_price
Create Date: 2026-06-04
"""

from alembic import op

revision = "0011_modifier_cost_matview"
down_revision = "0010_ingredient_current_price"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
DROP TABLE IF EXISTS public.product_modifier_costs;

CREATE MATERIALIZED VIEW public.mv_product_modifier_cost AS
SELECT mie.modifier_id,
       SUM(mie.quantity_change * i.current_price) AS cost_impact
FROM public.modifier_ingredient_effects mie
JOIN public.ingredients i ON i.id = mie.ingredient_id
WHERE i.current_price IS NOT NULL
GROUP BY mie.modifier_id;

-- Unique index required for REFRESH MATERIALIZED VIEW CONCURRENTLY.
CREATE UNIQUE INDEX uq_mv_pmc_modifier ON public.mv_product_modifier_cost (modifier_id);
"""

DOWNGRADE_SQL = r"""
DROP MATERIALIZED VIEW IF EXISTS public.mv_product_modifier_cost;

CREATE TABLE public.product_modifier_costs (
  id            bigint GENERATED ALWAYS AS IDENTITY,
  product_id    bigint NOT NULL,
  modifier_id   bigint NOT NULL,
  cost_impact   numeric(14, 4) NOT NULL,
  calculated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_product_modifier_costs PRIMARY KEY (id),
  CONSTRAINT uq_product_modifier_costs UNIQUE (product_id, modifier_id),
  CONSTRAINT fk_pmc_product  FOREIGN KEY (product_id)  REFERENCES public.products(id)  ON DELETE CASCADE,
  CONSTRAINT fk_pmc_modifier FOREIGN KEY (modifier_id) REFERENCES public.modifiers(id) ON DELETE CASCADE
);
CREATE INDEX idx_pmc_modifier ON public.product_modifier_costs (modifier_id);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
