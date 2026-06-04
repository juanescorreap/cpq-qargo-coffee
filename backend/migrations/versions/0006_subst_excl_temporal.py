"""C1: ingredient_substitutes temporal EXCLUDE instead of UNIQUE(orig,sub)

UNIQUE(original,substitute) allows only one row per pair, breaking the temporal
versioning pattern (close with valid_until + insert). Replace with an EXCLUDE
that forbids overlapping validity windows but allows re-approval over time.

Revision ID: 0006_subst_excl_temporal
Revises: 0005_sr_direct_endpoint
Create Date: 2026-06-04
"""

from alembic import op

revision = "0006_subst_excl_temporal"
down_revision = "0005_sr_direct_endpoint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.ingredient_substitutes "
        "DROP CONSTRAINT uq_ingredient_substitutes"
    )
    op.execute(
        "ALTER TABLE public.ingredient_substitutes ADD CONSTRAINT no_overlap_isub "
        "EXCLUDE USING gist ("
        "  original_ingredient_id WITH =, substitute_ingredient_id WITH =,"
        "  daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.ingredient_substitutes DROP CONSTRAINT no_overlap_isub"
    )
    op.execute(
        "ALTER TABLE public.ingredient_substitutes ADD CONSTRAINT uq_ingredient_substitutes "
        "UNIQUE (original_ingredient_id, substitute_ingredient_id)"
    )
