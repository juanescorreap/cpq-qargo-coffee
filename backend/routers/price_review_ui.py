"""Admin UI for reviewing fallback (Excel-import) ingredient prices.

Screen: /admin/price-review/{store_id}  (defaults to Edinburg, store 519).

Shows the "state C" ingredients for a store — those that have a purchase_price
but NO active supply_route price and were NOT freshened by the catalog sync —
grouped by category, flagged ingredients first, with inline edit. Progress is
persisted in price_review_status so an interrupted review resumes cleanly.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_db

router = APIRouter(prefix="/admin/price-review", tags=["UI - Price Review"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)

DEFAULT_STORE_ID = 519

# Flag → (badge label, tailwind classes) for the ⚠ badges.
_FLAG_BADGE = {
    "suspicious_high": ("⚠ High", "bg-amber-50 text-amber-700 border-amber-200"),
    "suspicious_low": ("⚠ Low", "bg-amber-50 text-amber-700 border-amber-200"),
    "suspicious_unit": ("⚠ Unit", "bg-amber-50 text-amber-700 border-amber-200"),
}


# ── Data helpers ─────────────────────────────────────────────────────────────
def _rows(
    db: Session,
    store_id: int,
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """State-C ingredients for a store, ordered category → flagged-first → name.

    category: category slug to filter by ('all'/None = every category).
    status:   pending | reviewed | skipped ('all'/None = every status).
    """
    cat = None if not category or category == "all" else category
    st = None if not status or status == "all" else status
    rows = db.execute(
        text(
            """
            SELECT * FROM (
                SELECT
                    i.id,
                    i.name,
                    i.purchase_price,
                    i.purchase_unit,
                    i.canonical_unit,
                    i.category                       AS category_slug,
                    COALESCE(c.display_name, i.category, 'Uncategorized')
                                                     AS category_name,
                    CASE
                        WHEN i.name = 'Ice Cubes' AND i.purchase_price < 0.10
                            THEN 'suspicious_low'
                        WHEN i.purchase_price > 200 AND i.purchase_unit ILIKE '%L%'
                            THEN 'suspicious_high'
                        WHEN i.purchase_price > 100 AND i.purchase_unit ILIKE '%unit%'
                            THEN 'suspicious_unit'
                        ELSE 'ok'
                    END                              AS price_flag,
                    COALESCE(prs.status, 'pending')  AS review_status
                FROM ingredients i
                LEFT JOIN categories c ON c.slug = i.category
                LEFT JOIN price_review_status prs
                       ON prs.ingredient_id = i.id
                      AND prs.store_id = :store_id
                WHERE i.is_active = true
                  AND i.purchase_price IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM supply_routes sr
                      JOIN supply_route_prices srp ON srp.supply_route_id = sr.id
                      WHERE sr.ingredient_id = i.id
                        AND sr.is_active = true
                        AND srp.valid_until IS NULL
                  )
                  AND i.id NOT IN (
                      SELECT DISTINCT cml.matched_ingredient_id
                      FROM catalog_match_log cml
                      JOIN catalog_sync_log csl ON csl.id = cml.sync_log_id
                      WHERE csl.store_id = :store_id
                        AND cml.action_taken IN ('created', 'updated')
                        AND cml.matched_ingredient_id IS NOT NULL
                  )
            ) q
            WHERE (:cat IS NULL OR q.category_slug = :cat)
              AND (:st  IS NULL OR q.review_status = :st)
            ORDER BY q.category_name,
                     (q.price_flag <> 'ok') DESC,
                     q.name
            """
        ),
        {"store_id": store_id, "cat": cat, "st": st},
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        badge = _FLAG_BADGE.get(d["price_flag"])
        d["flag_label"] = badge[0] if badge else None
        d["flag_classes"] = badge[1] if badge else None
        out.append(d)
    return out


def _grouped(rows: list[dict]) -> list[dict]:
    """Collapse ordered rows into [{category, count, rows:[...]}] (order preserved)."""
    groups: list[dict] = []
    for r in rows:
        if not groups or groups[-1]["category"] != r["category_name"]:
            groups.append({"category": r["category_name"], "rows": []})
        groups[-1]["rows"].append(r)
    for g in groups:
        g["count"] = len(g["rows"])
    return groups


def _progress(db: Session, store_id: int) -> dict:
    """Progress over the full state-C set for a store (ignores active filters).

    total = every state-C ingredient; reviewed = those marked 'reviewed'.
    Skipped does NOT count as progress (spec: skipped ≠ reviewed).
    """
    total = len(_rows(db, store_id))
    reviewed = sum(
        1 for r in _rows(db, store_id) if r["review_status"] == "reviewed"
    )
    pct = round(reviewed / total * 100) if total else 0
    return {"reviewed": reviewed, "total": total, "pct": pct}


def _store_name(db: Session, store_id: int) -> str:
    return (
        db.execute(
            text("SELECT name FROM stores WHERE id = :id"), {"id": store_id}
        ).scalar()
        or f"Store {store_id}"
    )


def _categories(rows: list[dict]) -> list[dict]:
    """Distinct (slug, name) pairs present in the unfiltered set, for the filter."""
    seen: dict[str, str] = {}
    for r in rows:
        seen.setdefault(r["category_slug"] or "", r["category_name"])
    return [{"slug": s, "name": n} for s, n in sorted(seen.items(), key=lambda x: x[1])]


def _one_row(db: Session, store_id: int, ingredient_id: int) -> Optional[dict]:
    """A single state-C row (post-update re-render). None if it left the set."""
    for r in _rows(db, store_id):
        if r["id"] == ingredient_id:
            return r
    return None


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def price_review_root():
    return RedirectResponse(url=f"/admin/price-review/{DEFAULT_STORE_ID}")


@router.get("/{store_id}", response_class=HTMLResponse)
def price_review_page(request: Request, store_id: int, db: Session = Depends(get_db)):
    rows = _rows(db, store_id)
    return templates.TemplateResponse(
        "admin/price_review/overview.html",
        {
            "request": request,
            "store_id": store_id,
            "store_name": _store_name(db, store_id),
            "groups": _grouped(rows),
            "categories": _categories(rows),
            "progress": _progress(db, store_id),
            "active_category": "all",
            "active_status": "all",
        },
    )


@router.get("/{store_id}/table", response_class=HTMLResponse)
def price_review_table(
    request: Request,
    store_id: int,
    category: str = "all",
    status: str = "all",
    db: Session = Depends(get_db),
):
    """Filtered table partial. Carries an OOB progress refresh."""
    rows = _rows(db, store_id, category, status)
    return templates.TemplateResponse(
        "admin/price_review/_table.html",
        {
            "request": request,
            "store_id": store_id,
            "groups": _grouped(rows),
            "progress": _progress(db, store_id),
            "oob_progress": True,
        },
    )


@router.get("/{store_id}/edit/{ingredient_id}", response_class=HTMLResponse)
def price_review_edit(
    request: Request, store_id: int, ingredient_id: int, db: Session = Depends(get_db)
):
    """Expand a row into its inline edit form (HTMX outerHTML swap of the <tr>)."""
    row = _one_row(db, store_id, ingredient_id)
    if row is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(
        "admin/price_review/_edit_form.html",
        {"request": request, "store_id": store_id, "ing": row},
    )


@router.get("/{store_id}/row/{ingredient_id}", response_class=HTMLResponse)
def price_review_row(
    request: Request, store_id: int, ingredient_id: int, db: Session = Depends(get_db)
):
    """Collapse back to the read-only row (Cancel from edit)."""
    row = _one_row(db, store_id, ingredient_id)
    if row is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(
        "admin/price_review/_ingredient_row.html",
        {"request": request, "store_id": store_id, "ing": row},
    )


def _upsert_status(
    db: Session, ingredient_id: int, store_id: int, status: str, reviewed_by: str
) -> None:
    db.execute(
        text(
            """
            INSERT INTO price_review_status
                (ingredient_id, store_id, status, reviewed_by, reviewed_at)
            VALUES (:i, :s, :st, :by, :at)
            ON CONFLICT (ingredient_id, store_id)
            DO UPDATE SET status = EXCLUDED.status,
                          reviewed_by = EXCLUDED.reviewed_by,
                          reviewed_at = EXCLUDED.reviewed_at
            """
        ),
        {
            "i": ingredient_id,
            "s": store_id,
            "st": status,
            "by": reviewed_by,
            "at": datetime.now(timezone.utc),
        },
    )


def _row_with_progress(
    request: Request, db: Session, store_id: int, ingredient_id: int
) -> HTMLResponse:
    """Updated <tr> + an OOB progress-bar swap, in one response."""
    row = _one_row(db, store_id, ingredient_id)
    return templates.TemplateResponse(
        "admin/price_review/_ingredient_row.html",
        {
            "request": request,
            "store_id": store_id,
            "ing": row,
            "progress": _progress(db, store_id),
            "oob_progress": True,
        },
    )


@router.post("/update", response_class=HTMLResponse)
def price_review_update(
    request: Request,
    ingredient_id: int = Form(...),
    store_id: int = Form(...),
    purchase_price: float = Form(...),
    purchase_unit: str = Form(...),
    db: Session = Depends(get_db),
):
    """Save the edited price + mark reviewed. Returns the row + OOB progress."""
    unit = purchase_unit.strip()
    if purchase_price <= 0 or not unit:
        # Re-render the edit form with an inline error rather than 500.
        row = _one_row(db, store_id, ingredient_id) or {"id": ingredient_id}
        resp = templates.TemplateResponse(
            "admin/price_review/_edit_form.html",
            {
                "request": request,
                "store_id": store_id,
                "ing": row,
                "error": "Price must be > 0 and unit is required.",
            },
        )
        resp.status_code = 400
        return resp
    db.execute(
        text(
            "UPDATE ingredients "
            "SET purchase_price = :p, purchase_unit = :u, updated_at = now() "
            "WHERE id = :i"
        ),
        {"p": purchase_price, "u": unit, "i": ingredient_id},
    )
    _upsert_status(db, ingredient_id, store_id, "reviewed", "admin")
    db.commit()
    return _row_with_progress(request, db, store_id, ingredient_id)


@router.post("/skip", response_class=HTMLResponse)
def price_review_skip(
    request: Request,
    ingredient_id: int = Form(...),
    store_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Mark skipped (grey badge). Progress does NOT advance."""
    _upsert_status(db, ingredient_id, store_id, "skipped", "admin")
    db.commit()
    return _row_with_progress(request, db, store_id, ingredient_id)
