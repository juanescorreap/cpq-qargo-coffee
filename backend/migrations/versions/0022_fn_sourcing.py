"""B1: fn_resolve_ingredient_sourcing — full sourcing tuple, not scalar price

fn_ingredient_unit_cost (0012) returns only a scalar. The engine needs the whole
sourcing tuple to (a) use the SUPPLIER's pack→recipe conversion instead of the
catalogue conversion_factor, and (b) record provenance (route/manufacturer/
distributor) for lineage.

Crucially it takes p_recipe_unit_id: supplier_unit_conversions is keyed by
(ingredient_ref_id, recipe_unit_id), so without the line's recipe unit the
conversion row is ambiguous. Precedence mirrors fn_ingredient_unit_cost:
  local store price -> resolved route's qargo_price -> catalogue.
When source='route' but no supplier conversion exists for the unit, purchase_qty/
recipe_qty come back NULL and the engine falls back to ingredient.conversion_factor.

Revision ID: 0022_fn_sourcing
Revises: 0021_snapshot_lineage
Create Date: 2026-06-05
"""

from alembic import op

revision = "0022_fn_sourcing"
down_revision = "0021_snapshot_lineage"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION public.fn_resolve_ingredient_sourcing(
    p_ingredient_id  bigint,
    p_store_id       bigint,
    p_recipe_unit_id bigint,            -- NULL = quantity already in usage unit
    p_date           date DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    supply_route_id  bigint,
    manufacturer_id  bigint,
    distributor_id   bigint,
    is_direct        boolean,
    unit_price       price_amount,      -- price in the PURCHASE unit
    price_currency   char(3),
    purchase_qty     quantity_amount,   -- NULL unless source='route'
    recipe_qty       quantity_amount,   -- NULL unless source='route'
    price_valid_from date,
    source           varchar            -- 'local' | 'route' | 'catalog'
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r       record;
    v_local record;
    v_srp   record;
BEGIN
    -- (1) Local store price: highest precedence (same order as fn_ingredient_unit_cost).
    SELECT sip.local_price, sip.currency_code, sip.valid_from
      INTO v_local
      FROM public.store_ingredient_prices sip
     WHERE sip.store_id = p_store_id
       AND sip.ingredient_id = p_ingredient_id
       AND sip.valid_from <= p_date
       AND (sip.valid_until IS NULL OR sip.valid_until >= p_date)
     ORDER BY sip.valid_from DESC
     LIMIT 1;

    IF FOUND THEN
        RETURN QUERY SELECT
            NULL::bigint, NULL::bigint, NULL::bigint, NULL::boolean,
            v_local.local_price, v_local.currency_code,
            NULL::quantity_amount, NULL::quantity_amount,
            v_local.valid_from, 'local'::varchar;
        RETURN;
    END IF;

    -- (2) Resolved route (store override > regional; priority 1 > 2). One row.
    SELECT *
      INTO r
      FROM public.fn_resolve_supply_route(p_ingredient_id, p_store_id, p_date)
     LIMIT 1;

    IF r.supply_route_id IS NOT NULL THEN
        SELECT srp.qargo_price, srp.currency_code, srp.valid_from
          INTO v_srp
          FROM public.supply_route_prices srp
         WHERE srp.supply_route_id = r.supply_route_id
           AND srp.valid_from <= p_date
           AND (srp.valid_until IS NULL OR srp.valid_until >= p_date)
         ORDER BY srp.valid_from DESC
         LIMIT 1;

        IF FOUND THEN
            RETURN QUERY
            SELECT r.supply_route_id, r.manufacturer_id, r.distributor_id, r.is_direct,
                   v_srp.qargo_price, v_srp.currency_code,
                   suc.purchase_qty, suc.recipe_qty,
                   v_srp.valid_from, 'route'::varchar
              FROM public.ingredient_supplier_refs isr
              LEFT JOIN public.supplier_unit_conversions suc
                     ON suc.ingredient_ref_id = isr.id
                    AND suc.recipe_unit_id IS NOT DISTINCT FROM p_recipe_unit_id
             WHERE isr.ingredient_id   = p_ingredient_id
               AND isr.supply_route_id = r.supply_route_id
               AND isr.is_active       = true
             LIMIT 1;

            IF FOUND THEN
                RETURN;
            END IF;

            -- Route priced but no supplier ref row: still report route + price,
            -- NULL conversion => engine falls back to ingredient.conversion_factor.
            RETURN QUERY SELECT
                r.supply_route_id, r.manufacturer_id, r.distributor_id, r.is_direct,
                v_srp.qargo_price, v_srp.currency_code,
                NULL::quantity_amount, NULL::quantity_amount,
                v_srp.valid_from, 'route'::varchar;
            RETURN;
        END IF;
    END IF;

    -- (3) Catalogue fallback. System base currency = COP.
    RETURN QUERY
    SELECT NULL::bigint, NULL::bigint, NULL::bigint, NULL::boolean,
           COALESCE(i.current_price, i.purchase_price, 0)::price_amount, 'COP'::char(3),
           NULL::quantity_amount, NULL::quantity_amount,
           NULL::date, 'catalog'::varchar
      FROM public.ingredients i
     WHERE i.id = p_ingredient_id;
END;
$$;
"""

DOWNGRADE_SQL = r"""
DROP FUNCTION IF EXISTS public.fn_resolve_ingredient_sourcing(bigint, bigint, bigint, date);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
