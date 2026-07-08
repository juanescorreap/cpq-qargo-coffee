"""Catalog API integration: mapping + sync/match logs.

Three tables backing the /admin/catalog-sync integration:

- store_catalog_mapping : CPQ store_id  ↔  external catalog store_id (manual,
  one-to-one both ways).
- catalog_sync_log      : one row per sync run (append-only audit).
- catalog_match_log     : one row per catalog item processed in a run
  (append-only, lets you audit every match/skip/create decision).

Reuses the existing public.set_updated_at() trigger function (used by regions,
categories, etc.) for store_catalog_mapping.updated_at.

Revision ID: 0033_catalog_sync
Revises: 0032_sra_strengthen
"""

from alembic import op

revision = "0033_catalog_sync"
down_revision = "0032_sra_strengthen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── store_catalog_mapping ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.store_catalog_mapping (
            id                  SERIAL PRIMARY KEY,
            store_id            INTEGER NOT NULL REFERENCES public.stores(id),
            catalog_store_id    INTEGER NOT NULL,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (store_id),
            UNIQUE (catalog_store_id)
        );
    """)
    op.execute("""
        COMMENT ON TABLE public.store_catalog_mapping IS
            'Mapeo entre el store_id del CPQ y el store_id de la API de catalogo '
            'externa. Se configura manualmente desde /admin/catalog-sync. '
            'Una tienda sin mapeo no puede sincronizarse.';
    """)
    op.execute("""
        CREATE TRIGGER trg_store_catalog_mapping_updated_at
            BEFORE UPDATE ON public.store_catalog_mapping
            FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
    """)

    # ── catalog_sync_log ──────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.catalog_sync_log (
            id                  SERIAL PRIMARY KEY,
            store_id            INTEGER REFERENCES public.stores(id),
            catalog_store_id    INTEGER NOT NULL,
            started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at        TIMESTAMPTZ,
            triggered_by        VARCHAR(50) NOT NULL,
            items_fetched       INTEGER,
            items_matched       INTEGER,
            items_created       INTEGER,
            items_updated       INTEGER,
            items_skipped       INTEGER,
            items_error         INTEGER,
            status              VARCHAR(20) NOT NULL DEFAULT 'running',
            error_detail        TEXT,
            metadata            JSONB
        );
    """)
    op.execute("""
        COMMENT ON TABLE public.catalog_sync_log IS
            'Registro append-only de cada corrida de sincronizacion de catalogo.';
    """)
    op.execute("""
        CREATE INDEX idx_catalog_sync_log_store_started
            ON public.catalog_sync_log(store_id, started_at DESC);
    """)

    # ── catalog_match_log ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE public.catalog_match_log (
            id                    SERIAL PRIMARY KEY,
            sync_log_id           INTEGER NOT NULL REFERENCES public.catalog_sync_log(id),
            catalog_item_id       INTEGER NOT NULL,
            catalog_sku           VARCHAR(100),
            catalog_name          VARCHAR(300) NOT NULL,
            match_type            VARCHAR(20),
            matched_ingredient_id INTEGER REFERENCES public.ingredients(id),
            fuzzy_score           NUMERIC,
            action_taken          VARCHAR(20),
            old_price             NUMERIC,
            new_price             NUMERIC,
            currency_code         CHAR(3),
            notes                 TEXT,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        COMMENT ON TABLE public.catalog_match_log IS
            'Registro append-only por item de catalogo procesado en cada sync. '
            'Permite auditar cada decision de match/skip/create.';
    """)
    op.execute("""
        CREATE INDEX idx_catalog_match_log_sync
            ON public.catalog_match_log(sync_log_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.catalog_match_log;")
    op.execute("DROP TABLE IF EXISTS public.catalog_sync_log;")
    op.execute("DROP TRIGGER IF EXISTS trg_store_catalog_mapping_updated_at ON public.store_catalog_mapping;")
    op.execute("DROP TABLE IF EXISTS public.store_catalog_mapping;")
