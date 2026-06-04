"""A1: temporal store_ingredient_prices + fn_ingredient_unit_cost (single source)

store_ingredient_prices had no temporality (UNIQUE(store,ingredient)) and there
was no defined precedence vs supply_route_prices. Give it valid_from/until +
currency + a no-overlap EXCLUDE, and add fn_ingredient_unit_cost as the single
source of truth for an ingredient's unit cost at a store/date:
  local valid price -> resolved route's qargo_price -> COALESCE(current_price, purchase_price)

Revision ID: 0012_store_price_temporal
Revises: 0011_modifier_cost_matview
Create Date: 2026-06-04
"""

from alembic import op

revision = "0012_store_price_temporal"
down_revision = "0011_modifier_cost_matview"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
ALTER TABLE public.store_ingredient_prices
  ADD COLUMN currency_code char(3) NOT NULL DEFAULT 'COP',
  ADD COLUMN valid_from date NOT NULL DEFAULT CURRENT_DATE,
  ADD COLUMN valid_until date;

ALTER TABLE public.store_ingredient_prices
  ADD CONSTRAINT fk_sip_currency FOREIGN KEY (currency_code)
    REFERENCES public.currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT;

ALTER TABLE public.store_ingredient_prices ALTER COLUMN local_price SET NOT NULL;

ALTER TABLE public.store_ingredient_prices
  DROP CONSTRAINT uq_store_ingredient_prices;

ALTER TABLE public.store_ingredient_prices
  ADD CONSTRAINT ck_sip_validity CHECK (valid_until IS NULL OR valid_until >= valid_from);

ALTER TABLE public.store_ingredient_prices
  ADD CONSTRAINT no_overlap_sip EXCLUDE USING gist (
    store_id WITH =, ingredient_id WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&);

-- Single source of truth for an ingredient's unit cost at a store on a date.
CREATE OR REPLACE FUNCTION public.fn_ingredient_unit_cost(
    p_ingredient_id bigint, p_store_id bigint, p_date date DEFAULT CURRENT_DATE
) RETURNS price_amount LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT sip.local_price
           FROM public.store_ingredient_prices sip
          WHERE sip.store_id = p_store_id AND sip.ingredient_id = p_ingredient_id
            AND sip.valid_from <= p_date
            AND (sip.valid_until IS NULL OR sip.valid_until >= p_date)
          ORDER BY sip.valid_from DESC LIMIT 1),
        (SELECT srp.qargo_price
           FROM public.fn_resolve_supply_route(p_ingredient_id, p_store_id, p_date) r
           JOIN public.supply_route_prices srp ON srp.supply_route_id = r.supply_route_id
          WHERE srp.valid_from <= p_date
            AND (srp.valid_until IS NULL OR srp.valid_until >= p_date)
          ORDER BY srp.valid_from DESC LIMIT 1),
        (SELECT COALESCE(i.current_price, i.purchase_price)
           FROM public.ingredients i WHERE i.id = p_ingredient_id)
    );
$$;
"""

DOWNGRADE_SQL = r"""
DROP FUNCTION IF EXISTS public.fn_ingredient_unit_cost(bigint, bigint, date);

ALTER TABLE public.store_ingredient_prices DROP CONSTRAINT no_overlap_sip;
ALTER TABLE public.store_ingredient_prices DROP CONSTRAINT ck_sip_validity;
ALTER TABLE public.store_ingredient_prices ALTER COLUMN local_price DROP NOT NULL;
ALTER TABLE public.store_ingredient_prices DROP CONSTRAINT fk_sip_currency;
ALTER TABLE public.store_ingredient_prices
  DROP COLUMN currency_code, DROP COLUMN valid_from, DROP COLUMN valid_until;
ALTER TABLE public.store_ingredient_prices
  ADD CONSTRAINT uq_store_ingredient_prices UNIQUE (store_id, ingredient_id);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
