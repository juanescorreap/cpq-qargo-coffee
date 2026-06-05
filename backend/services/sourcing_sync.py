"""Decoupled supplier-history sync (ENGINE_SUPPLIER_PLAN_V2 §2.2 / B4).

Records which supply route each store uses per ingredient in
``store_supplier_history`` using the close+insert temporal pattern. This is a
STANDALONE maintenance job — it must NOT run inside the cost batch: writing the
history there would contend on the table's EXCLUDE constraint under parallel
workers and conflate auditing with costing.

Each (store, ingredient) is serialised with a fine-grained advisory xact lock, so
concurrent syncs for different ingredients never block each other. Idempotent:
re-running with no route change writes nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import text


def sync_store_supplier_history(
    db,
    store_id: int,
    as_of: Optional[date] = None,
    changed_by: Optional[str] = None,
    change_reason: Optional[str] = None,
    commit: bool = True,
) -> int:
    """Reconcile store_supplier_history for one store against today's resolved
    routes. Returns the number of (ingredient) rows changed.

    For every ingredient used by an active product, resolve the current route via
    ``fn_resolve_supply_route``. If it differs from the open history row, close
    the old one and insert the new (close+insert; never UPDATE business data).
    """
    as_of = as_of or date.today()
    changed = 0

    ingredient_ids = [
        row[0]
        for row in db.execute(
            text(
                "SELECT DISTINCT ri.ingredient_id "
                "FROM recipe_ingredients ri "
                "JOIN products p ON p.id = ri.product_id "
                "WHERE p.is_active = true"
            )
        ).all()
    ]

    for ing_id in ingredient_ids:
        # Resolve the route that wins for this store+ingredient today.
        new_route = db.execute(
            text(
                "SELECT supply_route_id "
                "FROM fn_resolve_supply_route(:i, :s, :d) LIMIT 1"
            ),
            {"i": ing_id, "s": store_id, "d": as_of},
        ).scalar()
        if new_route is None:
            continue  # no route assigned -> nothing to audit

        # Serialise only this (store, ingredient) for the transaction.
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"ssh:{store_id}:{ing_id}"},
        )

        current = db.execute(
            text(
                "SELECT supply_route_id FROM store_supplier_history "
                "WHERE store_id = :s AND ingredient_id = :i AND valid_until IS NULL "
                "FOR UPDATE"
            ),
            {"s": store_id, "i": ing_id},
        ).scalar()

        if current == new_route:
            continue

        if current is not None:
            db.execute(
                text(
                    "UPDATE store_supplier_history SET valid_until = :d, "
                    "change_reason = COALESCE(:r, change_reason) "
                    "WHERE store_id = :s AND ingredient_id = :i AND valid_until IS NULL"
                ),
                {"d": as_of, "r": change_reason, "s": store_id, "i": ing_id},
            )

        db.execute(
            text(
                "INSERT INTO store_supplier_history "
                "(store_id, ingredient_id, supply_route_id, valid_from, "
                " change_reason, changed_by) "
                "VALUES (:s, :i, :r, :d, :reason, :by)"
            ),
            {
                "s": store_id, "i": ing_id, "r": new_route, "d": as_of,
                "reason": change_reason, "by": changed_by,
            },
        )
        changed += 1

    if commit:
        db.commit()
    return changed
