"""Strengthen supply_route_assignments: trigger + sra_single_scope CHECK.

The EXCLUDE constraints from 0029 prevent the same route appearing twice for the
same scope+priority+period. But they don't prevent TWO DIFFERENT routes for the
SAME ingredient sharing the same scope+priority+period — because ingredient_id
lives in supply_routes, not here.

A plain EXCLUDE can't JOIN to supply_routes, so we use a BEFORE INSERT/UPDATE
trigger that does the check explicitly.

Also adds the missing sra_single_scope CHECK (region_id and store_id cannot both
be non-null simultaneously), which was in the original CLAUDE.md design but was
never implemented.

Active violation fixed separately (assignment 78, store 516, ingredient 75).

Revision ID: 0032_sra_strengthen
Revises: 0031_fix_more_cf
"""

from alembic import op

revision = "0032_sra_strengthen"
down_revision = "0031_fix_more_cf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. sra_single_scope CHECK ─────────────────────────────────────────
    op.execute("""
        ALTER TABLE public.supply_route_assignments
            ADD CONSTRAINT sra_single_scope
            CHECK (NOT (region_id IS NOT NULL AND store_id IS NOT NULL));
    """)

    # ── 2. Trigger function ───────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION public.fn_check_sra_no_duplicate_ingredient_priority()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        DECLARE
            v_ingredient_id INTEGER;
            conflict_count  INTEGER;
        BEGIN
            SELECT ingredient_id INTO v_ingredient_id
            FROM public.supply_routes
            WHERE id = NEW.supply_route_id;

            SELECT COUNT(*) INTO conflict_count
            FROM public.supply_route_assignments sra
            JOIN public.supply_routes sr ON sr.id = sra.supply_route_id
            WHERE sr.ingredient_id    = v_ingredient_id
              AND sra.priority        = NEW.priority
              AND sra.id             <> COALESCE(NEW.id, -1)
              AND sra.supply_route_id <> NEW.supply_route_id
              AND (
                  (NEW.store_id  IS NOT NULL AND sra.store_id  = NEW.store_id)
                  OR
                  (NEW.region_id IS NOT NULL AND sra.region_id = NEW.region_id)
              )
              AND daterange(sra.valid_from, COALESCE(sra.valid_until, '9999-12-31'::date), '[)') &&
                  daterange(NEW.valid_from,  COALESCE(NEW.valid_until, '9999-12-31'::date), '[)');

            IF conflict_count > 0 THEN
                RAISE EXCEPTION
                    'supply_route_assignments: ingredient_id=% already has an active '
                    'route at priority=% for this scope in the given period. '
                    'Close the existing assignment (set valid_until) before inserting a new one.',
                    v_ingredient_id, NEW.priority
                    USING ERRCODE = 'exclusion_violation';
            END IF;

            RETURN NEW;
        END;
        $$;
    """)

    # ── 3. Attach trigger ─────────────────────────────────────────────────
    op.execute("""
        CREATE TRIGGER trg_sra_no_duplicate_ingredient_priority
            BEFORE INSERT OR UPDATE ON public.supply_route_assignments
            FOR EACH ROW EXECUTE FUNCTION
                public.fn_check_sra_no_duplicate_ingredient_priority();
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS trg_sra_no_duplicate_ingredient_priority
            ON public.supply_route_assignments;
    """)
    op.execute("""
        DROP FUNCTION IF EXISTS public.fn_check_sra_no_duplicate_ingredient_priority();
    """)
    op.execute("""
        ALTER TABLE public.supply_route_assignments
            DROP CONSTRAINT IF EXISTS sra_single_scope;
    """)
