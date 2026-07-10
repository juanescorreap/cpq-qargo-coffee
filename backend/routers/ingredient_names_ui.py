"""Admin UI for reviewing canonical names of catalog-confirmed ingredients.

Screen: /admin/ingredient-names

Lists every ingredient confirmed as "Keep as new" in the catalog sync
(catalog_match_log.action_taken = 'confirmed_new'), whose auto-generated name
may not follow the project's naming conventions (English, Title Case,
[Modifier][Base]). Flagged names first, inline batch rename.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_db

router = APIRouter(prefix="/admin/ingredient-names", tags=["UI - Ingredient Names"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)

# name_flag → (badge label, tailwind classes). 'ok' has no badge.
_FLAG_BADGE = {
    "all_caps": ("⚠ CAPS", "bg-amber-50 text-amber-700 border-amber-200"),
    "case": ("⚠ Case", "bg-amber-50 text-amber-700 border-amber-200"),
    "special_chars": ("⚠ Chars", "bg-amber-50 text-amber-700 border-amber-200"),
}

# UI filter value → internal name_flag.
_FILTER_TO_FLAG = {
    "caps": "all_caps",
    "case": "case",
    "chars": "special_chars",
    "ok": "ok",
}


def _rows(
    db: Session,
    flag: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Ingredients confirmed as new, flagged-first then category → name.

    flag:     UI flag filter (caps/case/chars/ok/all). 'all'/None = every flag.
    category: category slug filter. 'all'/None = every category.

    name_flag precedence (all_caps checked before case, matching the badge
    table in the spec — an ALL-CAPS name also fails initcap, so the upper()
    test must win first).
    """
    want_flag = _FILTER_TO_FLAG.get(flag) if flag and flag != "all" else None
    cat = None if not category or category == "all" else category
    rows = db.execute(
        text(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (i.id)
                    i.id,
                    i.name                              AS current_name,
                    cml.catalog_name                    AS api_name,
                    cml.catalog_sku                     AS api_sku,
                    i.category                          AS category_slug,
                    COALESCE(c.display_name, i.category, 'Uncategorized')
                                                        AS category_name,
                    CASE
                        WHEN i.name = upper(i.name) THEN 'all_caps'
                        WHEN i.name ~ '[^a-zA-Z0-9 \\-''\\.&]' THEN 'special_chars'
                        WHEN i.name <> initcap(i.name) THEN 'case'
                        ELSE 'ok'
                    END                                 AS name_flag
                FROM catalog_match_log cml
                JOIN ingredients i ON i.id = cml.matched_ingredient_id
                LEFT JOIN categories c ON c.slug = i.category
                WHERE cml.action_taken = 'confirmed_new'
                  AND i.is_active = true
                ORDER BY i.id, cml.created_at DESC
            ) q
            WHERE (:flag IS NULL OR q.name_flag = :flag)
              AND (:cat  IS NULL OR q.category_slug = :cat)
            ORDER BY (q.name_flag <> 'ok') DESC,
                     q.category_name,
                     q.current_name
            """
        ),
        {"flag": want_flag, "cat": cat},
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        badge = _FLAG_BADGE.get(d["name_flag"])
        d["flag_label"] = badge[0] if badge else None
        d["flag_classes"] = badge[1] if badge else None
        out.append(d)
    return out


def _categories(rows: list[dict]) -> list[dict]:
    """Distinct (slug, name) pairs present in the unfiltered set, for the filter."""
    seen: dict[str, str] = {}
    for r in rows:
        seen.setdefault(r["category_slug"] or "", r["category_name"])
    return [{"slug": s, "name": n} for s, n in sorted(seen.items(), key=lambda x: x[1])]


def _one_row(db: Session, ingredient_id: int) -> Optional[dict]:
    """A single row (post-update re-render). None if it left the set."""
    for r in _rows(db):
        if r["id"] == ingredient_id:
            return r
    return None


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def ingredient_names_page(request: Request, db: Session = Depends(get_db)):
    rows = _rows(db)
    return templates.TemplateResponse(
        "admin/ingredient_names/overview.html",
        {
            "request": request,
            "rows": rows,
            "total": len(rows),
            "categories": _categories(rows),
            "active_flag": "all",
            "active_category": "all",
        },
    )


@router.get("/table", response_class=HTMLResponse)
def ingredient_names_table(
    request: Request,
    flag: str = "all",
    category: str = "all",
    db: Session = Depends(get_db),
):
    """Filtered table partial."""
    rows = _rows(db, flag, category)
    return templates.TemplateResponse(
        "admin/ingredient_names/_table.html",
        {"request": request, "rows": rows},
    )


@router.get("/edit/{ingredient_id}", response_class=HTMLResponse)
def ingredient_names_edit(
    request: Request, ingredient_id: int, db: Session = Depends(get_db)
):
    """Expand a row into its inline rename form (HTMX outerHTML swap of the <tr>)."""
    row = _one_row(db, ingredient_id)
    if row is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(
        "admin/ingredient_names/_edit_form.html",
        {"request": request, "ing": row},
    )


@router.get("/row/{ingredient_id}", response_class=HTMLResponse)
def ingredient_names_row(
    request: Request, ingredient_id: int, db: Session = Depends(get_db)
):
    """Collapse back to the read-only row (Cancel from edit)."""
    row = _one_row(db, ingredient_id)
    if row is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(
        "admin/ingredient_names/_ingredient_row.html",
        {"request": request, "ing": row},
    )


@router.post("/update", response_class=HTMLResponse)
def ingredient_names_update(
    request: Request,
    ingredient_id: int = Form(...),
    name: str = Form(""),
    db: Session = Depends(get_db),
):
    """Rename the canonical ingredient. Returns the updated row, or a 400 edit
    form with an inline error on empty/too-long/duplicate name.

    ``name`` defaults to "" (not required) so an empty submit reaches the inline
    validation below instead of FastAPI's 422 — Starlette parses an empty form
    field as a missing value."""
    new_name = (name or "").strip()

    def _error(msg: str) -> HTMLResponse:
        row = _one_row(db, ingredient_id) or {"id": ingredient_id}
        resp = templates.TemplateResponse(
            "admin/ingredient_names/_edit_form.html",
            {"request": request, "ing": row, "error": msg},
        )
        resp.status_code = 400
        return resp

    if not new_name:
        return _error("Name cannot be empty.")
    if len(new_name) > 300:
        return _error("Name must be 300 characters or fewer.")

    # Reject a name already used by another active ingredient (canonical dupes).
    clash = db.execute(
        text(
            "SELECT 1 FROM ingredients "
            "WHERE is_active = true AND id <> :i AND lower(name) = lower(:n) LIMIT 1"
        ),
        {"i": ingredient_id, "n": new_name},
    ).scalar()
    if clash:
        return _error("An ingredient with this name already exists.")

    db.execute(
        text("UPDATE ingredients SET name = :n, updated_at = now() WHERE id = :i"),
        {"n": new_name, "i": ingredient_id},
    )
    db.commit()
    row = _one_row(db, ingredient_id)
    return templates.TemplateResponse(
        "admin/ingredient_names/_ingredient_row.html",
        {"request": request, "ing": row},
    )
