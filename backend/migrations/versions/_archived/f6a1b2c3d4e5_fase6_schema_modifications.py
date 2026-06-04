"""Fase 6 — Modificaciones al schema existente.

Conecta el schema original con las nuevas tablas y corrige los
problemas de diseño identificados:
  - stores: agrega region_id (FK → regions) y default_currency_code
  - ingredients: agrega canonical_unit y updated_at con trigger
  - product_pricing: agrega currency_code
  - product_price_history: agrega currency_code
  - índices adicionales de consulta
  - función fn_resolve_supply_route (única fuente de verdad)

Revision ID: f6a1b2c3d4e5
Revises: e5f6a1b2c3d4
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "f6a1b2c3d4e5"
down_revision = "e5f6a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 6.2 stores: region_id y default_currency_code ────────────────────
    op.add_column(
        "stores",
        sa.Column("region_id", sa.Integer(), sa.ForeignKey("regions.id"), nullable=True),
    )
    op.add_column(
        "stores",
        sa.Column(
            "default_currency_code",
            sa.String(3),
            nullable=False,
            server_default="COP",
        ),
    )

    # ── 6.3 ingredients: canonical_unit y updated_at ──────────────────────
    op.add_column(
        "ingredients",
        sa.Column("canonical_unit", sa.String(100), nullable=True),
    )
    op.add_column(
        "ingredients",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute("""
        CREATE TRIGGER trg_ingredients_updated_at
        BEFORE UPDATE ON public.ingredients
        FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at()
    """)

    # ── 6.4 product_pricing: currency_code ───────────────────────────────
    op.add_column(
        "product_pricing",
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
            server_default="COP",
        ),
    )

    # ── 6.5 product_price_history: currency_code ─────────────────────────
    op.add_column(
        "product_price_history",
        sa.Column(
            "currency_code",
            sa.String(3),
            nullable=False,
            server_default="COP",
        ),
    )

    # ── Índices adicionales ───────────────────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_product_pricing_current
        ON public.product_pricing(product_id, store_id, effective_date DESC)
        WHERE store_id IS NOT NULL
    """)

    # ── fn_resolve_supply_route ───────────────────────────────────────────
    # Fuente única de verdad para resolver qué ruta usa una tienda
    # para un ingrediente en una fecha dada.
    # Prioridad: override de tienda > asignación regional.
    # Dentro del mismo scope: priority 1 (primaria) > 2 (alternativa).
    op.execute("""
        CREATE OR REPLACE FUNCTION public.fn_resolve_supply_route(
            p_ingredient_id  INTEGER,
            p_store_id       INTEGER,
            p_date           DATE DEFAULT CURRENT_DATE
        )
        RETURNS TABLE (
            assignment_id    INTEGER,
            supply_route_id  INTEGER,
            scope            VARCHAR,
            priority         INTEGER,
            manufacturer_id  INTEGER,
            distributor_id   INTEGER,
            is_direct        BOOLEAN
        )
        LANGUAGE sql STABLE AS $$
            SELECT *
            FROM (
                SELECT
                    sra.id                    AS assignment_id,
                    sra.supply_route_id,
                    'store_override'::VARCHAR  AS scope,
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

                SELECT
                    sra.id                     AS assignment_id,
                    sra.supply_route_id,
                    'region_default'::VARCHAR   AS scope,
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
                CASE scope WHEN 'store_override' THEN 0 ELSE 1 END,
                priority ASC
            LIMIT 1;
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.fn_resolve_supply_route(INTEGER, INTEGER, DATE)")
    op.execute("DROP INDEX IF EXISTS idx_product_pricing_current")
    op.execute("DROP TRIGGER IF EXISTS trg_ingredients_updated_at ON public.ingredients")
    op.drop_column("product_price_history", "currency_code")
    op.drop_column("product_pricing", "currency_code")
    op.drop_column("ingredients", "updated_at")
    op.drop_column("ingredients", "canonical_unit")
    op.drop_column("stores", "default_currency_code")
    op.drop_column("stores", "region_id")
