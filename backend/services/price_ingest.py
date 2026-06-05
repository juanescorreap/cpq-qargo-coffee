"""Supplier price ingestion (ENGINE_SUPPLIER_PLAN_V2 §4.3 / B5).

Thin Python wrapper over the DB-side ``fn_ingest_route_price`` (migration 0024),
which does the temporal close+insert under a per-route advisory lock and fires
the outbox trigger that enqueues the recompute. Use for batch / CSV loads.

Each row dict requires:
  route_id, list_price, qargo_price, currency, price_unit_id, price_per_unit,
  created_by ; optional: source, valid_from.
"""

from __future__ import annotations

from typing import Iterable, List

from sqlalchemy import text

_INGEST_SQL = text(
    "SELECT fn_ingest_route_price("
    "  :route_id, :list_price, :qargo_price, :currency, :price_unit_id, "
    "  :price_per_unit, :source, :created_by, "
    "  COALESCE(:valid_from, CURRENT_DATE)) AS new_id"
)


def ingest_route_prices(db, rows: Iterable[dict], commit: bool = True) -> List[int]:
    """Ingest supplier prices row-by-row via fn_ingest_route_price.

    Returns the list of new supply_route_prices ids. The DB function validates
    qargo_price <= list_price and serialises concurrent loads per route; on any
    row error the whole batch is rolled back.
    """
    new_ids: List[int] = []
    try:
        for r in rows:
            new_id = db.execute(
                _INGEST_SQL,
                {
                    "route_id": r["route_id"],
                    "list_price": r["list_price"],
                    "qargo_price": r["qargo_price"],
                    "currency": r["currency"],
                    "price_unit_id": r.get("price_unit_id"),
                    "price_per_unit": r.get("price_per_unit", ""),
                    "source": r.get("source"),
                    "created_by": r["created_by"],
                    "valid_from": r.get("valid_from"),
                },
            ).scalar()
            new_ids.append(new_id)
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return new_ids
