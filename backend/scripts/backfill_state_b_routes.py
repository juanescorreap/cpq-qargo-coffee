"""One-shot backfill: give the 7 "state B" Edinburg ingredients a traceable route.

These ingredients arrived from the catalog API with a fresh price but no
supply_route, so the cost engine treats them as untracked fallbacks. This script
creates, for each one, a distributor → supply_route → ingredient_supplier_ref →
supply_route_price (reusing CatalogSyncService._ensure_supply_route) plus a
supply_route_assignment for Edinburg (store 519, priority 1).

Rules honoured (edinburg_production_plan.md §"Reglas que NO cambian"):
  * No invented prices — uses the ingredient's existing purchase_price as the
    route's qargo_price/list_price.
  * Append-only — INSERTs a new supply_route_price, never UPDATEs purchase_price
    as if it were a route price.
  * Atomic per ingredient — one ingredient failing rolls back only itself.
  * Idempotent — re-running creates nothing new (skips ingredients that already
    have an active route and/or an active assignment). Safe to run twice.

Run:  python -m backend.scripts.backfill_state_b_routes  [--store 519]  [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from backend.database import SessionLocal
from backend.services.catalog_sync import CatalogSyncService

# The 7 state-B ingredient ids (Milk, Coconut Milk, Coconut Syrup, Dragon Fruit
# Syrup, Strawberry Fruit Puree, Water, Focaccia).
STATE_B_IDS = [1, 3, 21, 23, 32, 52, 68]
DEFAULT_STORE_ID = 519


def _latest_match(db, ingredient_id: int) -> dict:
    """Most recent catalog_match_log row for this ingredient (for name/sku/item id)."""
    row = db.execute(
        text(
            """
            SELECT catalog_item_id, catalog_sku, catalog_name
            FROM catalog_match_log
            WHERE matched_ingredient_id = :i
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"i": ingredient_id},
    ).mappings().first()
    return dict(row) if row else {}


def _ensure_assignment(db, store_id: int, route_id: int, assigned_by: str) -> bool:
    """Create a store-scoped priority-1 assignment if none is live. Returns created?."""
    live = db.execute(
        text(
            "SELECT 1 FROM supply_route_assignments "
            "WHERE store_id = :s AND supply_route_id = :r AND priority = 1 "
            "AND valid_until IS NULL"
        ),
        {"s": store_id, "r": route_id},
    ).scalar()
    if live:
        return False
    db.execute(
        text(
            "INSERT INTO supply_route_assignments "
            "(supply_route_id, store_id, priority, valid_from, assigned_by, change_reason) "
            "VALUES (:r, :s, 1, CURRENT_DATE, :by, 'backfill_state_b')"
        ),
        {"r": route_id, "s": store_id, "by": assigned_by},
    )
    return True


def backfill(store_id: int = DEFAULT_STORE_ID, dry_run: bool = False) -> int:
    db = SessionLocal()
    svc = CatalogSyncService(db)
    created_routes = created_assigns = skipped = errors = 0
    try:
        for iid in STATE_B_IDS:
            try:
                ing = db.execute(
                    text(
                        "SELECT id, name, purchase_price, purchase_unit "
                        "FROM ingredients WHERE id = :i"
                    ),
                    {"i": iid},
                ).mappings().first()
                if ing is None:
                    print(f"  [skip] id={iid}: ingredient not found")
                    skipped += 1
                    continue

                match = _latest_match(db, iid)
                route_id, route_created = svc._ensure_supply_route(
                    iid,
                    external_name=match.get("catalog_name") or ing["name"],
                    external_code=match.get("catalog_sku"),
                    purchase_unit=ing["purchase_unit"],
                    price=ing["purchase_price"],
                    distributor_name=None,  # not persisted for auto-created items
                    catalog_item_id=match.get("catalog_item_id"),
                )
                if route_id is None:
                    print(
                        f"  [warn] id={iid} ({ing['name']}): no route — "
                        f"purchase_price missing/<=0 ({ing['purchase_price']})"
                    )
                    skipped += 1
                    db.rollback()
                    continue

                assign_created = _ensure_assignment(db, store_id, route_id, "backfill")

                if dry_run:
                    db.rollback()
                else:
                    db.commit()

                created_routes += int(route_created)
                created_assigns += int(assign_created)
                status = []
                status.append("route+ref+price CREATED" if route_created
                              else f"route EXISTS (id={route_id})")
                status.append("assignment CREATED" if assign_created
                              else "assignment EXISTS")
                print(f"  [ok]   id={iid} ({ing['name']}) route={route_id}: "
                      + "; ".join(status))
            except Exception as exc:  # noqa: BLE001 — isolate one ingredient
                db.rollback()
                errors += 1
                print(f"  [ERR]  id={iid}: {exc!r}")

        print(
            f"\nDone{' (DRY RUN — rolled back)' if dry_run else ''}: "
            f"{created_routes} routes created, {created_assigns} assignments created, "
            f"{skipped} skipped, {errors} errors."
        )
        return 1 if errors else 0
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=int, default=DEFAULT_STORE_ID)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(f"Backfilling state-B routes for store {args.store} "
          f"(ids {STATE_B_IDS})...")
    sys.exit(backfill(args.store, args.dry_run))
