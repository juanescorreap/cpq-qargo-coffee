"""Fase 1 — Geografía, fabricantes y distribuidores.

Crea las entidades base del modelo de cadena de suministro:
regions, manufacturers, distributors. También define la función
fn_set_updated_at() usada por los triggers de las fases siguientes.

Revision ID: a1b2c3d4e5f6
Revises: f8d2a3b1c9e7
Create Date: 2026-06-01
"""
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f8d2a3b1c9e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Shared updated_at trigger function ───────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION public.fn_set_updated_at()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$
    """)

    # ── regions ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.regions (
            id           SERIAL          PRIMARY KEY,
            name         VARCHAR(100)    NOT NULL,
            code         VARCHAR(20)     NOT NULL UNIQUE,
            country_code CHAR(2)         NOT NULL DEFAULT 'CO',
            is_active    BOOLEAN         NOT NULL DEFAULT true,
            metadata     JSONB,
            created_at   TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ     NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TRIGGER trg_regions_updated_at
        BEFORE UPDATE ON public.regions
        FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at()
    """)

    # ── manufacturers ────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.manufacturers (
            id           SERIAL          PRIMARY KEY,
            name         VARCHAR(200)    NOT NULL,
            country_code CHAR(2)         NOT NULL DEFAULT 'CO',
            tax_id       VARCHAR(50),
            website      TEXT,
            is_active    BOOLEAN         NOT NULL DEFAULT true,
            metadata     JSONB,
            created_at   TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ     NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TRIGGER trg_manufacturers_updated_at
        BEFORE UPDATE ON public.manufacturers
        FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at()
    """)

    # ── distributors ─────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.distributors (
            id            SERIAL          PRIMARY KEY,
            name          VARCHAR(200)    NOT NULL,
            country_code  CHAR(2)         NOT NULL DEFAULT 'CO',
            tax_id        VARCHAR(50),
            contact_email VARCHAR(200),
            contact_phone VARCHAR(50),
            is_active     BOOLEAN         NOT NULL DEFAULT true,
            metadata      JSONB,
            created_at    TIMESTAMPTZ     NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ     NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TRIGGER trg_distributors_updated_at
        BEFORE UPDATE ON public.distributors
        FOR EACH ROW EXECUTE FUNCTION public.fn_set_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_distributors_updated_at ON public.distributors")
    op.execute("DROP TABLE IF EXISTS public.distributors")
    op.execute("DROP TRIGGER IF EXISTS trg_manufacturers_updated_at ON public.manufacturers")
    op.execute("DROP TABLE IF EXISTS public.manufacturers")
    op.execute("DROP TRIGGER IF EXISTS trg_regions_updated_at ON public.regions")
    op.execute("DROP TABLE IF EXISTS public.regions")
    op.execute("DROP FUNCTION IF EXISTS public.fn_set_updated_at()")
