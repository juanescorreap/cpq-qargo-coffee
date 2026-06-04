"""V3-6: fx_rates table + fn_convert_amount for multi-currency coherence

Every monetary column carries a currency, but nothing reconciles amounts across
currencies. Add effective-dated exchange rates and a single conversion function
so costs/reports can normalize to a reporting currency.

Revision ID: 0017_fx_rates
Revises: 0016_current_state_idx
Create Date: 2026-06-04
"""

from alembic import op

revision = "0017_fx_rates"
down_revision = "0016_current_state_idx"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE TABLE public.fx_rates (
  id          bigint GENERATED ALWAYS AS IDENTITY,
  base_code   char(3) NOT NULL,
  quote_code  char(3) NOT NULL,
  rate        numeric(18, 8) NOT NULL CHECK (rate > 0),
  valid_from  date NOT NULL DEFAULT CURRENT_DATE,
  valid_until date,
  source      varchar(120),
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT pk_fx_rates PRIMARY KEY (id),
  CONSTRAINT ck_fx_rates_diff CHECK (base_code <> quote_code),
  CONSTRAINT ck_fx_rates_validity CHECK (valid_until IS NULL OR valid_until >= valid_from),
  CONSTRAINT fk_fx_base  FOREIGN KEY (base_code)  REFERENCES public.currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT fk_fx_quote FOREIGN KEY (quote_code) REFERENCES public.currencies(code) ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT no_overlap_fx EXCLUDE USING gist (
    base_code WITH =, quote_code WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  )
);
CREATE INDEX idx_fx_pair_current ON public.fx_rates (base_code, quote_code) WHERE valid_until IS NULL;

CREATE OR REPLACE FUNCTION public.fn_convert_amount(
    p_amount numeric, p_from char(3), p_to char(3), p_date date DEFAULT CURRENT_DATE
) RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN p_from = p_to THEN p_amount
        ELSE p_amount * (
            SELECT rate FROM public.fx_rates
            WHERE base_code = p_from AND quote_code = p_to
              AND valid_from <= p_date
              AND (valid_until IS NULL OR valid_until >= p_date)
            ORDER BY valid_from DESC LIMIT 1
        )
    END;
$$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.fn_convert_amount(numeric, char, char, date)")
    op.execute("DROP TABLE IF EXISTS public.fx_rates")
