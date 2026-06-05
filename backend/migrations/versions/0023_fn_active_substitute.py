"""B2: fn_active_substitute — availability-driven substitution (1 level)

Returns the approved substitute for an ingredient at a store/date when the
original is not available, else NULL. Activation:
  - activation_condition='always'    -> always eligible
  - 'shortage'/'unavailable'         -> eligible only if ingredient_availability
                                        has a non-available status for the
                                        ingredient in the store's region (or a
                                        global/region-NULL row)
Region scoping: a substitute with NO rows in ingredient_substitute_regions is
global; otherwise it only applies in the store's region. One level only: the
caller does NOT recurse into the substitute (if the substitute is itself short,
the no-substitute policy applies to it).

Revision ID: 0023_fn_active_substitute
Revises: 0022_fn_sourcing
Create Date: 2026-06-05
"""

from alembic import op

revision = "0023_fn_active_substitute"
down_revision = "0022_fn_sourcing"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION public.fn_active_substitute(
    p_ingredient_id bigint,
    p_store_id      bigint,
    p_date          date DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    substitute_ingredient_id bigint,
    quantity_ratio           quantity_amount,
    recipe_unit_id           bigint,
    cost_impact_pct          pct_amount
)
LANGUAGE sql STABLE AS $$
    WITH store_region AS (
        SELECT region_id FROM public.stores WHERE id = p_store_id
    ),
    unavailable AS (
        -- original not available in the store's region (or globally region-NULL)
        SELECT 1
          FROM public.ingredient_availability ia
          LEFT JOIN store_region sr ON true
         WHERE ia.ingredient_id = p_ingredient_id
           AND ia.status IN ('shortage', 'discontinued', 'seasonal')
           AND ia.valid_from <= p_date
           AND (ia.valid_until IS NULL OR ia.valid_until >= p_date)
           AND (ia.region_id IS NULL OR ia.region_id = sr.region_id)
         LIMIT 1
    )
    SELECT isub.substitute_ingredient_id,
           isub.quantity_ratio,
           isub.recipe_unit_id,
           isub.cost_impact_pct
      FROM public.ingredient_substitutes isub
     WHERE isub.original_ingredient_id = p_ingredient_id
       AND isub.valid_from <= p_date
       AND (isub.valid_until IS NULL OR isub.valid_until >= p_date)
       -- activation: 'always' OR original currently unavailable
       AND (isub.activation_condition = 'always' OR EXISTS (SELECT 1 FROM unavailable))
       -- region scoping: global (no region rows) OR matches the store's region
       AND (
            NOT EXISTS (SELECT 1 FROM public.ingredient_substitute_regions r
                         WHERE r.substitute_id = isub.id)
            OR EXISTS (SELECT 1 FROM public.ingredient_substitute_regions r, store_region sr
                        WHERE r.substitute_id = isub.id AND r.region_id = sr.region_id)
       )
     ORDER BY isub.valid_from DESC   -- most recent approval; 1 level
     LIMIT 1;
$$;
"""

DOWNGRADE_SQL = r"""
DROP FUNCTION IF EXISTS public.fn_active_substitute(bigint, bigint, date);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
