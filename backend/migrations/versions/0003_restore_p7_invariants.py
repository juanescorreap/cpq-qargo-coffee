"""restore P7 integrity invariants dropped by the validated target DDL

The supply-chain expansion (CLAUDE.md, Principle P7) enforced several invariants
at the database level that ``files/schema_refactorizado.sql`` relaxed. This
migration re-adds them so incoherent states remain impossible in the DB, not
merely "should not happen" in application code:

  1. supply_route_assignments: no two assignments with the same priority for the
     same scope (store or region) may have overlapping validity windows.
  2. store_supplier_history: a store cannot have two routes active for the same
     ingredient over overlapping periods.
  3. supply_routes: a direct purchase (is_direct) cannot carry a distributor.

Revision ID: 0003_restore_p7_invariants
Revises: 0002_resolve_supply_route_fn
Create Date: 2026-06-04
"""

from alembic import op

revision = "0003_restore_p7_invariants"
down_revision = "0002_resolve_supply_route_fn"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
-- 1. supply_route_assignments: no overlapping priority per scope
ALTER TABLE public.supply_route_assignments
  ADD CONSTRAINT no_overlap_sra_store EXCLUDE USING gist (
    store_id WITH =,
    priority WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  ) WHERE (store_id IS NOT NULL);

ALTER TABLE public.supply_route_assignments
  ADD CONSTRAINT no_overlap_sra_region EXCLUDE USING gist (
    region_id WITH =,
    priority WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  ) WHERE (region_id IS NOT NULL);

-- 2. store_supplier_history: one active route per store+ingredient over time
ALTER TABLE public.store_supplier_history
  ADD CONSTRAINT no_overlap_ssh EXCLUDE USING gist (
    store_id WITH =,
    ingredient_id WITH =,
    daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[)') WITH &&
  );

-- 3. supply_routes: direct purchase cannot have a distributor
ALTER TABLE public.supply_routes
  ADD CONSTRAINT ck_supply_routes_direct_no_distributor
  CHECK (NOT (is_direct = true AND distributor_id IS NOT NULL));
"""

DOWNGRADE_SQL = r"""
ALTER TABLE public.supply_routes
  DROP CONSTRAINT IF EXISTS ck_supply_routes_direct_no_distributor;
ALTER TABLE public.store_supplier_history
  DROP CONSTRAINT IF EXISTS no_overlap_ssh;
ALTER TABLE public.supply_route_assignments
  DROP CONSTRAINT IF EXISTS no_overlap_sra_region;
ALTER TABLE public.supply_route_assignments
  DROP CONSTRAINT IF EXISTS no_overlap_sra_store;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
