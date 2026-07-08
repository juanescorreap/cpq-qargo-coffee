"""Price review progress tracking (/admin/price-review).

One table, ``price_review_status``, that records the per-store review state of an
ingredient's fallback (Excel-import) price without touching the ingredients table.
It lets the Price Review screen resume where the user left off after an
interruption.

UNIQUE (ingredient_id, store_id) makes the row the single source of truth for
"has this store reviewed this ingredient's price yet". FK types are BIGINT to
match ingredients.id / stores.id (both ``bigint GENERATED ALWAYS AS IDENTITY``).

Revision ID: 0034_price_review
Revises: 0033_catalog_sync
"""

from alembic import op

revision = "0034_price_review"
down_revision = "0033_catalog_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE public.price_review_status (
            id              SERIAL PRIMARY KEY,
            ingredient_id   BIGINT NOT NULL REFERENCES public.ingredients(id),
            store_id        BIGINT NOT NULL REFERENCES public.stores(id),
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            reviewed_by     VARCHAR(100),
            reviewed_at     TIMESTAMPTZ,
            notes           TEXT,
            CONSTRAINT ck_prs_status CHECK (status IN ('pending', 'reviewed', 'skipped')),
            CONSTRAINT uq_prs_ingredient_store UNIQUE (ingredient_id, store_id)
        );
    """)
    op.execute("""
        COMMENT ON TABLE public.price_review_status IS
            'Progreso de revision de precios fallback (Excel) por tienda/ingrediente. '
            'Registra el avance sin tocar la tabla ingredients; permite retomar la '
            'revision si se interrumpe. status: pending | reviewed | skipped.';
    """)
    op.execute("""
        CREATE INDEX idx_prs_store_status
            ON public.price_review_status(store_id, status);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.price_review_status;")
