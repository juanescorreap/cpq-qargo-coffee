"""Fase 5 — Historial de relaciones y snapshots de costo.

Crea store_supplier_history (log de auditoría de qué ruta usó cada
tienda por ingrediente) y recipe_cost_snapshots (registro inmutable
de cálculos de costo, append-only).

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-06-01
"""
from alembic import op

revision = "e5f6a1b2c3d4"
down_revision = "d4e5f6a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── store_supplier_history ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.store_supplier_history (
            id               SERIAL       PRIMARY KEY,
            store_id         INTEGER      NOT NULL
                                          REFERENCES public.stores(id),
            ingredient_id    INTEGER      NOT NULL
                                          REFERENCES public.ingredients(id),
            supply_route_id  INTEGER      NOT NULL
                                          REFERENCES public.supply_routes(id),
            valid_from       DATE         NOT NULL,
            valid_until      DATE,
            change_reason    VARCHAR(200),
            changed_by       VARCHAR(100),
            notes            TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

            EXCLUDE USING gist (
                store_id      WITH =,
                ingredient_id WITH =,
                daterange(valid_from, COALESCE(valid_until, '9999-12-31'::date), '[)') WITH &&
            )
        )
    """)
    op.execute("""
        CREATE INDEX idx_ssh_store_ingredient_active
        ON public.store_supplier_history(store_id, ingredient_id)
        WHERE valid_until IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_ssh_store_active
        ON public.store_supplier_history(store_id)
        WHERE valid_until IS NULL
    """)

    # ── recipe_cost_snapshots ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.recipe_cost_snapshots (
            id                SERIAL       PRIMARY KEY,
            product_id        INTEGER      NOT NULL
                                           REFERENCES public.products(id),
            store_id          INTEGER      NOT NULL
                                           REFERENCES public.stores(id),
            calculated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
            base_cost         NUMERIC      NOT NULL,
            effective_cost    NUMERIC      NOT NULL,
            currency_code     CHAR(3)      NOT NULL,
            has_substitutes   BOOLEAN      NOT NULL DEFAULT false,
            snapshot_detail   JSONB        NOT NULL,
            triggered_by      VARCHAR(100),

            CONSTRAINT rcs_costs_positive CHECK (
                base_cost > 0 AND effective_cost > 0
            )
        )
    """)
    op.execute("""
        CREATE INDEX idx_rcs_product_store
        ON public.recipe_cost_snapshots(product_id, store_id, calculated_at DESC)
    """)
    op.execute("""
        CREATE INDEX idx_rcs_store_date
        ON public.recipe_cost_snapshots(store_id, calculated_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.recipe_cost_snapshots")
    op.execute("DROP TABLE IF EXISTS public.store_supplier_history")
