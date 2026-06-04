"""relax categories.slug format to match the app's underscore convention

The validated DDL enforced ``slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'`` (hyphen-only),
but the application's real category keys use underscores
(``bebidas_calientes``, ``bebidas_frias``, ``alimentos``, ``otros`` — see
migrations/seed_data.py and CLAUDE.md). Hyphen-only would reject every existing
category. Allow both ``_`` and ``-`` as word separators.

Revision ID: 0004_category_slug_allow_underscore
Revises: 0003_restore_p7_invariants
Create Date: 2026-06-04
"""

from alembic import op

revision = "0004_category_slug_underscore"
down_revision = "0003_restore_p7_invariants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.categories DROP CONSTRAINT IF EXISTS ck_categories_slug_format"
    )
    op.execute(
        "ALTER TABLE public.categories "
        "ADD CONSTRAINT ck_categories_slug_format "
        "CHECK (slug ~ '^[a-z0-9]+([_-][a-z0-9]+)*$')"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.categories DROP CONSTRAINT IF EXISTS ck_categories_slug_format"
    )
    op.execute(
        "ALTER TABLE public.categories "
        "ADD CONSTRAINT ck_categories_slug_format "
        "CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')"
    )
