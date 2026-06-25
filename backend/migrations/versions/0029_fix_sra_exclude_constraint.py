"""Fix supply_route_assignments EXCLUDE constraints to include supply_route_id.

The original constraints prevented any two priority-1 rows for the same store,
even for different ingredients. Correct constraint: per (store, supply_route, priority)
— which is per (store, ingredient-route) — so each ingredient can have its own
active assignment at priority 1 simultaneously.

Old:  EXCLUDE (store_id, priority, daterange)
New:  EXCLUDE (store_id, supply_route_id, priority, daterange)

Revision ID: 0029_fix_sra_exclude_constraint
Revises: 0028_fk_covering_indexes
"""

from alembic import op

revision = "0029_fix_sra_exclude_constraint"
down_revision = "0028_fk_covering_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old constraints (named by Postgres based on table+column pattern)
    op.execute("""
        DO $$
        DECLARE
            r record;
        BEGIN
            FOR r IN
                SELECT conname
                FROM pg_constraint
                WHERE contype = 'x'
                  AND conrelid = 'public.supply_route_assignments'::regclass
            LOOP
                EXECUTE 'ALTER TABLE public.supply_route_assignments DROP CONSTRAINT ' || quote_ident(r.conname);
            END LOOP;
        END$$;
    """)

    # Add corrected EXCLUDE constraints that include supply_route_id.
    # btree_gist is already enabled (0016 or earlier).
    op.execute("""
        ALTER TABLE public.supply_route_assignments
            ADD CONSTRAINT sra_store_route_priority_no_overlap
            EXCLUDE USING gist (
                store_id        WITH =,
                supply_route_id WITH =,
                priority        WITH =,
                daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
            ) WHERE (store_id IS NOT NULL);
    """)

    op.execute("""
        ALTER TABLE public.supply_route_assignments
            ADD CONSTRAINT sra_region_route_priority_no_overlap
            EXCLUDE USING gist (
                region_id       WITH =,
                supply_route_id WITH =,
                priority        WITH =,
                daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
            ) WHERE (region_id IS NOT NULL);
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.supply_route_assignments
            DROP CONSTRAINT IF EXISTS sra_store_route_priority_no_overlap,
            DROP CONSTRAINT IF EXISTS sra_region_route_priority_no_overlap;
    """)

    op.execute("""
        ALTER TABLE public.supply_route_assignments
            ADD CONSTRAINT supply_route_assignments_store_id_priority_daterange_excl
            EXCLUDE USING gist (
                store_id  WITH =,
                priority  WITH =,
                daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
            ) WHERE (store_id IS NOT NULL);
    """)

    op.execute("""
        ALTER TABLE public.supply_route_assignments
            ADD CONSTRAINT supply_route_assignments_region_id_priority_daterange_excl
            EXCLUDE USING gist (
                region_id WITH =,
                priority  WITH =,
                daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
            ) WHERE (region_id IS NOT NULL);
    """)
