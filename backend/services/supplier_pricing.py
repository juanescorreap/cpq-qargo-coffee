"""Supplier price writes — the safe path (E2E_ARCHITECTURE_AUDIT G3 + G7).

Routes every price write through fn_ingest_route_price so it gets the per-route
advisory lock AND fires the outbox trigger that enqueues the recompute. Adds a
bounded lock_timeout + retry so a burst of mutations to the same route degrades
to a short wait/retry instead of piling up requests or hanging.

Concurrency note: the engine uses advisory locks + EXCLUDE, NOT SERIALIZABLE, so
the retriable errors are lock_timeout (55P03) and deadlock (40P01) — never 40001.
EXCLUDE overlaps (23P01) are data errors and are NOT retried.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

BUSINESS_TZ = ZoneInfo("America/Bogota")
_LOCK_TIMEOUT = "3s"
_RETRY_SQLSTATES = {"55P03", "40P01"}  # lock_timeout, deadlock


def business_today() -> date:
    """Calendar 'today' in business TZ — never the UTC date (avoids the
    off-by-one when valid_from is computed near midnight on a UTC server)."""
    return datetime.now(BUSINESS_TZ).date()


def _pgcode(exc: OperationalError) -> Optional[str]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "pgcode", None)


def save_route_price(
    db,
    *,
    route_id: int,
    list_price,
    qargo_price,
    currency: str = "COP",
    price_unit_id: Optional[int] = None,
    price_per_unit: str = "",
    created_by: str,
    source: Optional[str] = None,
    valid_from: Optional[date] = None,
    max_retries: int = 2,
) -> int:
    """Insert a new price via fn_ingest_route_price (advisory lock + outbox).

    Validates in Python (fast, clear messages) and relies on the DB function for
    the temporal close+insert. Returns the new price id. Raises ValueError on bad
    input; re-raises non-retriable DB errors.
    """
    try:
        lp = Decimal(str(list_price).replace(",", "").strip())
        qp = Decimal(str(qargo_price).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValueError("Invalid price")
    if lp <= 0 or qp <= 0:
        raise ValueError("Prices must be greater than zero")
    if qp > lp:
        raise ValueError("Negotiated price cannot exceed list price")
    currency = (currency or "COP").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Invalid currency code (must be 3 letters, e.g. COP)")
    if not (created_by or "").strip():
        raise ValueError("Created by is required")

    vf = valid_from or business_today()
    params = {
        "route": route_id, "lp": lp, "qp": qp, "ccy": currency,
        "unit": price_unit_id, "per": (price_per_unit or "").strip(),
        "source": (source or "").strip() or None, "by": created_by.strip(), "vf": vf,
    }

    attempt = 0
    while True:
        try:
            db.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
            new_id = db.execute(text(
                "SELECT fn_ingest_route_price("
                "  :route, :lp, :qp, :ccy, :unit, :per, :source, :by, :vf)"
            ), params).scalar()
            db.commit()
            return new_id
        except OperationalError as exc:
            db.rollback()
            if _pgcode(exc) in _RETRY_SQLSTATES and attempt < max_retries:
                attempt += 1
                time.sleep(0.05 * (2 ** attempt))  # 100ms, 200ms backoff
                continue
            raise
