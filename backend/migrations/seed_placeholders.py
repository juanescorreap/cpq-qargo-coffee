"""Seed placeholder manufacturer, distributor, supply_routes, and ingredient_supplier_refs
for the 133 ingredients that have no real sourcing decision yet.

ALL entities created here have is_active=False so they are invisible to:
  - fn_resolve_supply_route (filters sr.is_active = true)
  - fn_resolve_ingredient_sourcing (calls fn_resolve_supply_route)
  - cost_calculator.py (LATERAL join via fn_resolve_ingredient_sourcing)

Purpose: represent known-missing data as a DB record with is_active=False and
metadata.placeholder=true, rather than as a line in a dead-letter CSV with no
traceability. Allows future auditors to query:
  SELECT * FROM supply_routes WHERE is_active = false AND metadata->>'placeholder' = 'true'

Usage:
  DRY RUN (default — no writes):
    python -m backend.migrations.seed_placeholders

  EXECUTE (writes to DB):
    python -m backend.migrations.seed_placeholders --execute

Audit CSV is always written regardless of dry-run flag.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

import backend.models  # noqa — register ORM
from backend.database import SessionLocal
from backend.models.ingredient import Ingredient
from backend.models.supply_chain import (
    Distributor,
    IngredientSupplierRef,
    Manufacturer,
    SupplyRoute,
)

_AUDIT_PATH = Path("data/_rejects/suc_placeholder_audit.csv")
_BACKLOG_CSV = Path("data/_rejects/suc_no_route_backlog.csv")

_PLACEHOLDER_NAME = "Unassigned — Pending Sourcing"
_PLACEHOLDER_MAN_META = {
    "placeholder": True,
    "reason": "manufacturer real no identificado al momento de carga inicial",
    "created_for_audit": "supplier_unit_conversions backlog 2026-06",
}
_PLACEHOLDER_DIST_META = {
    "placeholder": True,
    "reason": "distribuidor real no identificado al momento de carga inicial",
}
_PLACEHOLDER_ROUTE_META = {
    "placeholder": True,
    "pending_decision": True,
}


def _get_or_create_placeholder_manufacturer(db, dry_run: bool) -> int:
    """Return id of the placeholder manufacturer, creating it if absent."""
    existing = db.query(Manufacturer).filter(Manufacturer.name == _PLACEHOLDER_NAME).first()
    if existing:
        return existing.id
    if dry_run:
        print(f"  [DRY] would CREATE manufacturer: '{_PLACEHOLDER_NAME}'")
        return -1  # sentinel
    man = Manufacturer(
        name=_PLACEHOLDER_NAME,
        country_code="US",
        is_active=False,
        metadata_=_PLACEHOLDER_MAN_META,
    )
    db.add(man)
    db.flush()
    print(f"  CREATED manufacturer id={man.id}: '{_PLACEHOLDER_NAME}'")
    return man.id


def _get_or_create_placeholder_distributor(db, dry_run: bool) -> int:
    existing = db.query(Distributor).filter(Distributor.name == _PLACEHOLDER_NAME).first()
    if existing:
        return existing.id
    if dry_run:
        print(f"  [DRY] would CREATE distributor: '{_PLACEHOLDER_NAME}'")
        return -1
    dist = Distributor(
        name=_PLACEHOLDER_NAME,
        country_code="US",
        is_active=False,
        metadata_=_PLACEHOLDER_DIST_META,
    )
    db.add(dist)
    db.flush()
    print(f"  CREATED distributor id={dist.id}: '{_PLACEHOLDER_NAME}'")
    return dist.id


def run(dry_run: bool = True) -> None:
    db = SessionLocal()
    try:
        # Load backlog ingredient list
        if not _BACKLOG_CSV.exists():
            print(f"ERROR: backlog CSV not found at {_BACKLOG_CSV}")
            return
        with _BACKLOG_CSV.open(newline="", encoding="utf-8") as fh:
            backlog_ings = [r["ingredient_name"] for r in csv.DictReader(fh)]

        print(f"\n{'='*64}")
        print(f"seed_placeholders — {'DRY RUN' if dry_run else 'EXECUTE'}")
        print(f"{'='*64}")
        print(f"Ingredientes en backlog: {len(backlog_ings)}")

        man_id = _get_or_create_placeholder_manufacturer(db, dry_run)
        dist_id = _get_or_create_placeholder_distributor(db, dry_run)

        # Build ingredient name → id map
        ing_map = {
            i.name.lower(): i.id
            for i in db.query(Ingredient.id, Ingredient.name).all()
        }

        audit_rows: list[dict] = []
        created_routes = skipped_routes = 0

        for ing_name in backlog_ings:
            ing_id = ing_map.get(ing_name.lower())
            if ing_id is None:
                print(f"  WARNING: ingredient '{ing_name}' not found — skipping")
                continue

            # Check if placeholder route already exists for this ingredient
            existing_route = db.query(SupplyRoute).filter(
                SupplyRoute.ingredient_id == ing_id,
                SupplyRoute.manufacturer_id == man_id if man_id != -1 else True,
            ).first() if man_id != -1 else None

            # More robust check: look for placeholder metadata
            existing_placeholder = (
                db.query(SupplyRoute)
                .filter(
                    SupplyRoute.ingredient_id == ing_id,
                    SupplyRoute.is_active == False,
                )
                .first()
            ) if not dry_run else None

            if existing_placeholder:
                skipped_routes += 1
                audit_rows.append({
                    "ingredient_id": ing_id,
                    "ingredient_name": ing_name,
                    "supply_route_id": existing_placeholder.id,
                    "ingredient_supplier_ref_id": "already_exists",
                    "fecha_creacion": "pre-existing",
                    "action": "skipped",
                })
                continue

            if dry_run:
                print(f"  [DRY] would CREATE route + ref for '{ing_name}' (ing_id={ing_id})")
                audit_rows.append({
                    "ingredient_id": ing_id,
                    "ingredient_name": ing_name,
                    "supply_route_id": "DRY_RUN",
                    "ingredient_supplier_ref_id": "DRY_RUN",
                    "fecha_creacion": str(date.today()),
                    "action": "would_create",
                })
                created_routes += 1
                continue

            # Create placeholder supply_route
            route = SupplyRoute(
                ingredient_id=ing_id,
                manufacturer_id=man_id,
                distributor_id=dist_id,
                is_direct=False,
                is_active=False,
                metadata_=_PLACEHOLDER_ROUTE_META,
            )
            db.add(route)
            db.flush()

            # Create placeholder ingredient_supplier_ref
            ref = IngredientSupplierRef(
                ingredient_id=ing_id,
                supply_route_id=route.id,
                external_name=ing_name,
                purchase_unit="unknown — pending sourcing decision",
                is_active=False,
            )
            db.add(ref)
            db.flush()

            audit_rows.append({
                "ingredient_id": ing_id,
                "ingredient_name": ing_name,
                "supply_route_id": route.id,
                "ingredient_supplier_ref_id": ref.id,
                "fecha_creacion": str(date.today()),
                "action": "created",
            })
            created_routes += 1

        if not dry_run:
            db.commit()
            print(f"\n  ✅ Committed: {created_routes} routes + refs created, {skipped_routes} skipped")
        else:
            db.rollback()
            print(f"\n  [DRY] Would create: {created_routes} routes + refs ({skipped_routes} already exist)")

        # Always write audit CSV
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "ingredient_id", "ingredient_name", "supply_route_id",
                "ingredient_supplier_ref_id", "fecha_creacion", "action",
            ])
            w.writeheader()
            w.writerows(audit_rows)
        print(f"  Audit CSV: {_AUDIT_PATH}")

        print(f"\n{'='*64}")
        if dry_run:
            print("DRY RUN complete — no DB writes. Run with --execute to apply.")
        else:
            print("EXECUTE complete.")
        print(f"{'='*64}")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Actually write to DB (default: dry run)")
    args = parser.parse_args()
    run(dry_run=not args.execute)
