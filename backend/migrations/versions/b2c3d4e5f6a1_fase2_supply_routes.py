"""Fase 2 — Rutas de suministro.

Crea supply_routes (qué ruta existe) y supply_route_assignments
(quién usa qué y cuándo), con constraints EXCLUDE que previenen
solapamiento de vigencias y duplicidad de prioridades por scope.

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # btree_gist es necesaria para EXCLUDE USING gist con tipos no-geométricos
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # ── supply_routes ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.supply_routes (
            id               SERIAL          PRIMARY KEY,
            ingredient_id    INTEGER         NOT NULL
                                             REFERENCES public.ingredients(id),
            manufacturer_id  INTEGER
                                             REFERENCES public.manufacturers(id),
            distributor_id   INTEGER
                                             REFERENCES public.distributors(id),
            is_direct        BOOLEAN         NOT NULL DEFAULT false,
            is_active        BOOLEAN         NOT NULL DEFAULT true,
            metadata         JSONB,
            created_at       TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ     NOT NULL DEFAULT now(),

            CONSTRAINT supply_routes_source_check CHECK (
                is_direct = true
                OR manufacturer_id IS NOT NULL
                OR distributor_id  IS NOT NULL
            ),
            CONSTRAINT supply_routes_direct_no_distributor CHECK (
                NOT (is_direct = true AND distributor_id IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE TRIGGER trg_supply_routes_updated_at
        BEFORE UPDATE ON public.supply_routes
        FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at()
    """)
    op.execute("""
        CREATE INDEX idx_supply_routes_ingredient_active
        ON public.supply_routes(ingredient_id)
        WHERE is_active = true
    """)

    # ── supply_route_assignments ──────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.supply_route_assignments (
            id               SERIAL       PRIMARY KEY,
            supply_route_id  INTEGER      NOT NULL
                                          REFERENCES public.supply_routes(id),
            region_id        INTEGER
                                          REFERENCES public.regions(id),
            store_id         INTEGER
                                          REFERENCES public.stores(id),
            priority         INTEGER      NOT NULL DEFAULT 1,
            valid_from       DATE         NOT NULL DEFAULT CURRENT_DATE,
            valid_until      DATE,
            change_reason    VARCHAR(200),
            assigned_by      VARCHAR(100) NOT NULL,
            notes            TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

            CONSTRAINT sra_scope_required CHECK (
                region_id IS NOT NULL OR store_id IS NOT NULL
            ),
            CONSTRAINT sra_single_scope CHECK (
                NOT (region_id IS NOT NULL AND store_id IS NOT NULL)
            ),
            CONSTRAINT sra_priority_positive CHECK (priority >= 1),

            EXCLUDE USING gist (
                store_id  WITH =,
                priority  WITH =,
                daterange(valid_from, COALESCE(valid_until, '9999-12-31'::date), '[)') WITH &&
            ) WHERE (store_id IS NOT NULL),

            EXCLUDE USING gist (
                region_id WITH =,
                priority  WITH =,
                daterange(valid_from, COALESCE(valid_until, '9999-12-31'::date), '[)') WITH &&
            ) WHERE (region_id IS NOT NULL)
        )
    """)
    op.execute("""
        CREATE INDEX idx_sra_region_active_primary
        ON public.supply_route_assignments(region_id, priority)
        WHERE valid_until IS NULL AND region_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX idx_sra_store_active
        ON public.supply_route_assignments(store_id, priority)
        WHERE valid_until IS NULL AND store_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX idx_sra_valid_from
        ON public.supply_route_assignments(valid_from, valid_until)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.supply_route_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_supply_routes_updated_at ON public.supply_routes")
    op.execute("DROP TABLE IF EXISTS public.supply_routes")
