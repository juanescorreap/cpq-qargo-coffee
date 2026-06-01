"""Fase 3 — Referencias externas, unidades y precios.

Crea ingredient_supplier_refs (nombre/código por proveedor),
supplier_unit_conversions (conversión unidad compra → unidad receta)
y supply_route_prices (precios con vigencia temporal, moneda explícita).

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-06-01
"""
from alembic import op

revision = "c3d4e5f6a1b2"
down_revision = "b2c3d4e5f6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ingredient_supplier_refs ──────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.ingredient_supplier_refs (
            id               SERIAL       PRIMARY KEY,
            ingredient_id    INTEGER      NOT NULL
                                          REFERENCES public.ingredients(id),
            supply_route_id  INTEGER      NOT NULL
                                          REFERENCES public.supply_routes(id),
            external_name    VARCHAR(300) NOT NULL,
            external_code    VARCHAR(100),
            purchase_unit    VARCHAR(100) NOT NULL,
            units_per_pack   NUMERIC,
            is_active        BOOLEAN      NOT NULL DEFAULT true,
            notes            TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

            UNIQUE (supply_route_id, external_code)
        )
    """)
    op.execute("""
        CREATE TRIGGER trg_ingredient_supplier_refs_updated_at
        BEFORE UPDATE ON public.ingredient_supplier_refs
        FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at()
    """)
    op.execute("""
        CREATE INDEX idx_isr_ingredient
        ON public.ingredient_supplier_refs(ingredient_id)
        WHERE is_active = true
    """)
    op.execute("""
        CREATE INDEX idx_isr_route
        ON public.ingredient_supplier_refs(supply_route_id)
        WHERE is_active = true
    """)

    # ── supplier_unit_conversions ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.supplier_unit_conversions (
            id                 SERIAL      PRIMARY KEY,
            ingredient_ref_id  INTEGER     NOT NULL
                                           REFERENCES public.ingredient_supplier_refs(id),
            recipe_unit_id     INTEGER     NOT NULL
                                           REFERENCES public.recipe_units(id),
            purchase_qty       NUMERIC     NOT NULL,
            recipe_qty         NUMERIC     NOT NULL,
            notes              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT suc_quantities_positive CHECK (
                purchase_qty > 0 AND recipe_qty > 0
            ),
            UNIQUE (ingredient_ref_id, recipe_unit_id)
        )
    """)

    # ── supply_route_prices ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.supply_route_prices (
            id                SERIAL       PRIMARY KEY,
            supply_route_id   INTEGER      NOT NULL
                                           REFERENCES public.supply_routes(id),
            list_price        NUMERIC      NOT NULL,
            qargo_price       NUMERIC      NOT NULL,
            currency_code     CHAR(3)      NOT NULL,
            price_per_unit    VARCHAR(100) NOT NULL,
            valid_from        DATE         NOT NULL DEFAULT CURRENT_DATE,
            valid_until       DATE,
            source            VARCHAR(100),
            created_by        VARCHAR(100) NOT NULL,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),

            CONSTRAINT srp_prices_positive CHECK (
                list_price > 0 AND qargo_price > 0
            ),
            CONSTRAINT srp_qargo_lte_list CHECK (
                qargo_price <= list_price
            ),
            CONSTRAINT srp_currency_valid CHECK (
                currency_code ~ '^[A-Z]{3}$'
            ),

            EXCLUDE USING gist (
                supply_route_id WITH =,
                daterange(valid_from, COALESCE(valid_until, '9999-12-31'::date), '[)') WITH &&
            )
        )
    """)
    op.execute("""
        CREATE INDEX idx_srp_route_active
        ON public.supply_route_prices(supply_route_id)
        WHERE valid_until IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.supply_route_prices")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ingredient_supplier_refs_updated_at "
        "ON public.ingredient_supplier_refs"
    )
    op.execute("DROP TABLE IF EXISTS public.supplier_unit_conversions")
    op.execute("DROP TABLE IF EXISTS public.ingredient_supplier_refs")
