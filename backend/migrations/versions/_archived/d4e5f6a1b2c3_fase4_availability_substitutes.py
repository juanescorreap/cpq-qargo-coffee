"""Fase 4 — Disponibilidad regional y sustitutos.

Crea ingredient_availability (registro de estado de disponibilidad
por ruta o región) e ingredient_substitutes (sustitutos aprobados
por corporativo con ratio, condición de activación y alcance regional).

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-06-01
"""
from alembic import op

revision = "d4e5f6a1b2c3"
down_revision = "c3d4e5f6a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ingredient_availability ───────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.ingredient_availability (
            id               SERIAL       PRIMARY KEY,
            ingredient_id    INTEGER      NOT NULL
                                          REFERENCES public.ingredients(id),
            supply_route_id  INTEGER
                                          REFERENCES public.supply_routes(id),
            region_id        INTEGER
                                          REFERENCES public.regions(id),
            status           VARCHAR(50)  NOT NULL,
            expected_resume  DATE,
            valid_from       DATE         NOT NULL DEFAULT CURRENT_DATE,
            valid_until      DATE,
            reported_by      VARCHAR(100),
            notes            TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

            CONSTRAINT ia_scope_required CHECK (
                supply_route_id IS NOT NULL OR region_id IS NOT NULL
            ),
            CONSTRAINT ia_status_valid CHECK (
                status IN ('available', 'shortage', 'discontinued', 'seasonal')
            ),
            CONSTRAINT ia_resume_only_for_shortage CHECK (
                expected_resume IS NULL OR status = 'shortage'
            )
        )
    """)
    op.execute("""
        CREATE INDEX idx_ia_ingredient_active
        ON public.ingredient_availability(ingredient_id, status)
        WHERE valid_until IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_ia_route_active
        ON public.ingredient_availability(supply_route_id)
        WHERE valid_until IS NULL AND supply_route_id IS NOT NULL
    """)

    # ── ingredient_substitutes ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.ingredient_substitutes (
            id                       SERIAL       PRIMARY KEY,
            original_ingredient_id   INTEGER      NOT NULL
                                                  REFERENCES public.ingredients(id),
            substitute_ingredient_id INTEGER      NOT NULL
                                                  REFERENCES public.ingredients(id),
            approved_by              VARCHAR(100) NOT NULL,
            approval_date            DATE         NOT NULL,
            activation_condition     VARCHAR(50)  NOT NULL DEFAULT 'shortage',
            quantity_ratio           NUMERIC      NOT NULL DEFAULT 1.0,
            recipe_unit_id           INTEGER
                                                  REFERENCES public.recipe_units(id),
            cost_impact_pct          NUMERIC,
            affects_regions          INTEGER[],
            valid_from               DATE         NOT NULL DEFAULT CURRENT_DATE,
            valid_until              DATE,
            notes                    TEXT,
            created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),

            CONSTRAINT is_no_self_substitute CHECK (
                original_ingredient_id <> substitute_ingredient_id
            ),
            CONSTRAINT is_activation_valid CHECK (
                activation_condition IN ('shortage', 'unavailable', 'always')
            ),
            CONSTRAINT is_ratio_positive CHECK (
                quantity_ratio > 0
            ),
            UNIQUE (original_ingredient_id, substitute_ingredient_id, valid_from)
        )
    """)
    op.execute("""
        CREATE INDEX idx_is_original_active
        ON public.ingredient_substitutes(original_ingredient_id)
        WHERE valid_until IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.ingredient_substitutes")
    op.execute("DROP TABLE IF EXISTS public.ingredient_availability")
