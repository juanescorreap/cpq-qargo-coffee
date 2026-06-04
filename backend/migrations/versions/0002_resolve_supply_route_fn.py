"""restore fn_resolve_supply_route (single source of truth for route resolution)

The validated target DDL (files/schema_refactorizado.sql) did not carry the
``fn_resolve_supply_route`` function, but the application (routers/supply_chain.py,
routers/stores_ui.py, schemas) still depends on it — it is Principle 6 of the
supply-chain expansion: one place that answers "which route does this store use
for this ingredient today?". This migration re-creates it, adapted to the new
bigint identity types.

Revision ID: 0002_resolve_supply_route_fn
Revises: 0001_initial_schema
Create Date: 2026-06-04
"""

from alembic import op

revision = "0002_resolve_supply_route_fn"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


CREATE_FN = r"""
CREATE OR REPLACE FUNCTION public.fn_resolve_supply_route(
    p_ingredient_id  bigint,
    p_store_id       bigint,
    p_date           date DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    assignment_id    bigint,
    supply_route_id  bigint,
    scope            varchar,
    priority         integer,
    manufacturer_id  bigint,
    distributor_id   bigint,
    is_direct        boolean
)
LANGUAGE sql STABLE AS $$
    SELECT *
    FROM (
        -- Candidate 1: explicit store override
        SELECT
            sra.id                     AS assignment_id,
            sra.supply_route_id,
            'store_override'::varchar  AS scope,
            sra.priority,
            sr.manufacturer_id,
            sr.distributor_id,
            sr.is_direct
        FROM public.supply_route_assignments sra
        JOIN public.supply_routes            sr  ON sr.id = sra.supply_route_id
        WHERE sra.store_id     = p_store_id
          AND sr.ingredient_id = p_ingredient_id
          AND sr.is_active     = true
          AND sra.valid_from  <= p_date
          AND (sra.valid_until IS NULL OR sra.valid_until > p_date)

        UNION ALL

        -- Candidate 2: regional assignment for the store's region
        SELECT
            sra.id                      AS assignment_id,
            sra.supply_route_id,
            'region_default'::varchar   AS scope,
            sra.priority,
            sr.manufacturer_id,
            sr.distributor_id,
            sr.is_direct
        FROM public.supply_route_assignments sra
        JOIN public.supply_routes            sr  ON sr.id = sra.supply_route_id
        JOIN public.stores                   s   ON s.region_id = sra.region_id
        WHERE s.id             = p_store_id
          AND sra.store_id     IS NULL
          AND sr.ingredient_id = p_ingredient_id
          AND sr.is_active     = true
          AND sra.valid_from  <= p_date
          AND (sra.valid_until IS NULL OR sra.valid_until > p_date)
    ) candidates
    ORDER BY
        CASE candidates.scope WHEN 'store_override' THEN 0 ELSE 1 END,
        candidates.priority ASC
    LIMIT 1;
$$;
"""


def upgrade() -> None:
    op.execute(CREATE_FN)


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS public.fn_resolve_supply_route(bigint, bigint, date)"
    )
