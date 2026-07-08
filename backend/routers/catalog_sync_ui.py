"""Admin UI for the catalog API sync (/admin/catalog-sync)."""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services.catalog_sync import (
    CatalogSyncService,
    confirm_as_new,
    deactivate_pending,
    map_to_canonical,
)

router = APIRouter(prefix="/admin/catalog-sync", tags=["UI - Catalog Sync"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


# ── Data helpers ────────────────────────────────────────────────────────────
def _mapping_rows(db: Session) -> list[dict]:
    """One row per active store with its catalog mapping (if any)."""
    rows = db.execute(
        text(
            """
            SELECT s.id, s.code, s.name, m.catalog_store_id
            FROM stores s
            LEFT JOIN store_catalog_mapping m ON m.store_id = s.id
            WHERE s.is_active = true
            ORDER BY s.code
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _mapping_ctx(
    request: Request,
    db: Session,
    editing: Optional[int] = None,
    error: Optional[str] = None,
    error_store: Optional[int] = None,
) -> dict:
    return {
        "request": request,
        "rows": _mapping_rows(db),
        "editing": editing,
        "error": error,
        "error_store": error_store,
    }


def _store_name(db: Session, store_id: int) -> Optional[str]:
    return db.execute(
        text("SELECT name FROM stores WHERE id = :id"), {"id": store_id}
    ).scalar()


# ── Endpoints ───────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def catalog_sync_page(request: Request, db: Session = Depends(get_db)):
    ctx = _mapping_ctx(request, db)
    return templates.TemplateResponse("admin/catalog_sync/overview.html", ctx)


@router.get("/mapping", response_class=HTMLResponse)
def mapping_table(
    request: Request,
    editing: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Re-render the mapping table, optionally with one row in edit mode."""
    ctx = _mapping_ctx(request, db, editing=editing)
    return templates.TemplateResponse("admin/catalog_sync/_mapping_table.html", ctx)


@router.post("/mapping", response_class=HTMLResponse)
def set_mapping(
    request: Request,
    store_id: int = Form(...),
    catalog_store_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Create/update a store↔catalog mapping. Rejects a catalog_store_id already
    assigned to a different store with an inline error naming that store."""
    clash = db.execute(
        text(
            "SELECT store_id FROM store_catalog_mapping "
            "WHERE catalog_store_id = :cid AND store_id <> :sid"
        ),
        {"cid": catalog_store_id, "sid": store_id},
    ).scalar()
    if clash is not None:
        other = _store_name(db, clash) or f"store {clash}"
        ctx = _mapping_ctx(
            request, db, editing=store_id,
            error=f"This catalog ID is already assigned to {other}",
            error_store=store_id,
        )
        resp = templates.TemplateResponse("admin/catalog_sync/_mapping_table.html", ctx)
        resp.status_code = 400
        return resp

    db.execute(
        text(
            """
            INSERT INTO store_catalog_mapping (store_id, catalog_store_id)
            VALUES (:sid, :cid)
            ON CONFLICT (store_id)
            DO UPDATE SET catalog_store_id = EXCLUDED.catalog_store_id,
                          updated_at = now()
            """
        ),
        {"sid": store_id, "cid": catalog_store_id},
    )
    db.commit()
    ctx = _mapping_ctx(request, db)
    return templates.TemplateResponse("admin/catalog_sync/_mapping_table.html", ctx)


def _status_rows(db: Session) -> list[dict]:
    """Configured stores with their most-recent sync summary."""
    rows = db.execute(
        text(
            """
            SELECT s.id, s.code, s.name, m.catalog_store_id,
                   l.started_at, l.completed_at, l.status,
                   l.items_fetched, l.items_matched, l.items_created, l.items_updated
            FROM store_catalog_mapping m
            JOIN stores s ON s.id = m.store_id
            LEFT JOIN LATERAL (
                SELECT * FROM catalog_sync_log
                WHERE store_id = m.store_id
                ORDER BY started_at DESC LIMIT 1
            ) l ON true
            ORDER BY s.code
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/status", response_class=HTMLResponse)
def sync_status(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "admin/catalog_sync/_sync_status.html",
        {"request": request, "rows": _status_rows(db)},
    )


@router.post("/run", response_class=HTMLResponse)
async def run_sync(request: Request, store_id: int, db: Session = Depends(get_db)):
    mapped = db.execute(
        text("SELECT 1 FROM store_catalog_mapping WHERE store_id = :s"),
        {"s": store_id},
    ).scalar()
    if not mapped:
        resp = templates.TemplateResponse(
            "admin/catalog_sync/_sync_status.html",
            {"request": request, "rows": _status_rows(db),
             "error": "Store has no catalog mapping"},
        )
        resp.status_code = 400
        return resp
    await CatalogSyncService(db).sync_store(store_id, "manual")
    return templates.TemplateResponse(
        "admin/catalog_sync/_sync_status.html",
        {"request": request, "rows": _status_rows(db)},
    )


@router.post("/run-all", response_class=HTMLResponse)
async def run_sync_all(request: Request, db: Session = Depends(get_db)):
    await CatalogSyncService(db).sync_all_stores("manual")
    return templates.TemplateResponse(
        "admin/catalog_sync/_sync_status.html",
        {"request": request, "rows": _status_rows(db)},
    )


@router.get("/log", response_class=HTMLResponse)
def sync_log(request: Request, db: Session = Depends(get_db)):
    """Section 3: last 20 sync runs."""
    rows = db.execute(
        text(
            """
            SELECT l.id, s.code, s.name, l.catalog_store_id, l.started_at,
                   l.completed_at, l.triggered_by, l.status,
                   l.items_fetched, l.items_matched, l.items_created,
                   l.items_updated, l.items_skipped, l.items_error
            FROM catalog_sync_log l
            LEFT JOIN stores s ON s.id = l.store_id
            ORDER BY l.started_at DESC
            LIMIT 20
            """
        )
    ).mappings().all()
    return templates.TemplateResponse(
        "admin/catalog_sync/_sync_log.html",
        {"request": request, "rows": [dict(r) for r in rows]},
    )


@router.get("/log/{sync_id}", response_class=HTMLResponse)
def sync_log_detail(request: Request, sync_id: int, db: Session = Depends(get_db)):
    """Expandable detail: every catalog_match_log row for one sync."""
    rows = db.execute(
        text(
            """
            SELECT catalog_item_id, catalog_sku, catalog_name, match_type,
                   matched_ingredient_id, fuzzy_score, action_taken,
                   old_price, new_price, currency_code, notes
            FROM catalog_match_log
            WHERE sync_log_id = :sid
            ORDER BY id
            """
        ),
        {"sid": sync_id},
    ).mappings().all()
    return templates.TemplateResponse(
        "admin/catalog_sync/_sync_log_detail.html",
        {"request": request, "rows": [dict(r) for r in rows], "sync_id": sync_id},
    )


_STATUS_MAP = {
    "created": "pending",
    "mapped": "mapped",
    "confirmed_new": "confirmed_new",
    "deactivated_manual": "deactivated",
}


@router.get("/new-ingredients", response_class=HTMLResponse)
def new_ingredients(request: Request, db: Session = Depends(get_db)):
    """Section 4: catalog-created ingredients grouped by review state.

    Pending = still 'created', active, no recipe yet (need action).
    Already processed = mapped / confirmed_new / deactivated_manual.
    Note: action_taken is 'created' in prod, not the spec's 'ingredient_created'.
    """
    rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (i.id)
                   i.id, i.name, i.category, i.purchase_unit, i.purchase_price,
                   ml.catalog_name, ml.catalog_sku, ml.new_price, ml.currency_code,
                   ml.action_taken, ml.notes,
                   csl.started_at AS sync_date, s.name AS store_name,
                   i.is_active,
                   EXISTS (
                       SELECT 1 FROM recipe_ingredients ri WHERE ri.ingredient_id = i.id
                   ) AS has_recipe
            FROM catalog_match_log ml
            JOIN ingredients i ON i.id = ml.matched_ingredient_id
            JOIN catalog_sync_log csl ON csl.id = ml.sync_log_id
            LEFT JOIN stores s ON s.id = csl.store_id
            WHERE ml.match_type = 'new'
              AND ml.action_taken IN
                  ('created', 'mapped', 'confirmed_new', 'deactivated_manual')
            ORDER BY i.id, ml.created_at DESC
            """
        )
    ).mappings().all()

    pending: list[dict] = []
    processed: list[dict] = []
    for r in rows:
        card = dict(r)
        card["status"] = _STATUS_MAP.get(card["action_taken"], "pending")
        if card["action_taken"] == "created":
            # A 'created' row that got a recipe or was deactivated elsewhere is
            # resolved — keep it out of the Pending group.
            if card["is_active"] and not card["has_recipe"]:
                pending.append(card)
        else:
            processed.append(card)

    counts = {
        "mapped": sum(1 for c in processed if c["status"] == "mapped"),
        "confirmed_new": sum(1 for c in processed if c["status"] == "confirmed_new"),
        "deactivated": sum(1 for c in processed if c["status"] == "deactivated"),
    }
    return templates.TemplateResponse(
        "admin/catalog_sync/_new_ingredients.html",
        {
            "request": request,
            "pending": pending,
            "processed": processed,
            "counts": counts,
        },
    )


@router.post("/ingredient/{ingredient_id}/deactivate", response_class=HTMLResponse)
def deactivate_ingredient(request: Request, ingredient_id: int, db: Session = Depends(get_db)):
    db.execute(
        text("UPDATE ingredients SET is_active = false, updated_at = now() WHERE id = :i"),
        {"i": ingredient_id},
    )
    db.commit()
    return new_ingredients(request, db)


# ── Pending-review manual mapping (pending_review_mapping_spec) ───────────────
def _pending_card_row(db: Session, ingredient_id: int) -> Optional[dict]:
    """All fields one pending-review card needs, keyed by ingredient id.

    Price and purchase unit come from the ingredients table (the pending
    catalog-sync ingredients have no supply_routes / supply_route_prices — see
    pre-check 2 — so a route join would always be NULL). SKU, catalog name and
    sync context come from the latest catalog_match_log row for the ingredient.
    Distributor is not persisted for auto-created items, so it is left None.
    """
    row = db.execute(
        text(
            """
            SELECT DISTINCT ON (i.id)
                   i.id, i.name, i.category,
                   i.purchase_unit, i.purchase_price,
                   ml.catalog_name, ml.catalog_sku,
                   ml.new_price, ml.currency_code, ml.action_taken,
                   csl.started_at AS sync_date, s.name AS store_name
            FROM catalog_match_log ml
            JOIN ingredients i ON i.id = ml.matched_ingredient_id
            JOIN catalog_sync_log csl ON csl.id = ml.sync_log_id
            LEFT JOIN stores s ON s.id = csl.store_id
            WHERE ml.matched_ingredient_id = :i
            ORDER BY i.id, ml.created_at DESC
            """
        ),
        {"i": ingredient_id},
    ).mappings().first()
    return dict(row) if row else None


def _render_card(
    request: Request,
    card: dict,
    status: str,
    error: Optional[str] = None,
    searching: bool = False,
) -> HTMLResponse:
    """Render one pending-review card partial (HTMX outerHTML swap target)."""
    resp = templates.TemplateResponse(
        "admin/catalog_sync/_ingredient_card.html",
        {
            "request": request,
            "card": card,
            "status": status,
            "error": error,
            "searching": searching,
        },
    )
    if error:
        resp.status_code = 400
    return resp


@router.post("/map-ingredient", response_class=HTMLResponse)
def map_ingredient(
    request: Request,
    pending_ingredient_id: int = Form(...),
    canonical_ingredient_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Map a duplicate to an existing canonical ingredient (one transaction)."""
    card = _pending_card_row(db, pending_ingredient_id) or {"id": pending_ingredient_id}
    try:
        canonical_name = map_to_canonical(db, pending_ingredient_id, canonical_ingredient_id)
    except ValueError as exc:
        return _render_card(request, card, status="pending", error=str(exc))
    except Exception:  # noqa: BLE001 — surface a clean inline error, DB rolled back
        db.rollback()
        return _render_card(
            request, card, status="pending",
            error="Database error — no changes were made",
        )
    card["mapped_to"] = canonical_name
    return _render_card(request, card, status="mapped")


@router.post("/confirm-new", response_class=HTMLResponse)
def confirm_new_ingredient(
    request: Request,
    pending_ingredient_id: int = Form(...),
    db: Session = Depends(get_db),
):
    card = _pending_card_row(db, pending_ingredient_id) or {"id": pending_ingredient_id}
    confirm_as_new(db, pending_ingredient_id)
    return _render_card(request, card, status="confirmed_new")


@router.post("/deactivate-ingredient", response_class=HTMLResponse)
def deactivate_pending_ingredient(
    request: Request,
    pending_ingredient_id: int = Form(...),
    db: Session = Depends(get_db),
):
    card = _pending_card_row(db, pending_ingredient_id) or {"id": pending_ingredient_id}
    deactivate_pending(db, pending_ingredient_id)
    return _render_card(request, card, status="deactivated")


@router.get("/review-ingredient/{ingredient_id}", response_class=HTMLResponse)
def review_ingredient(request: Request, ingredient_id: int, db: Session = Depends(get_db)):
    """Expand a pending card into its 'Map to existing' search state."""
    card = _pending_card_row(db, ingredient_id) or {"id": ingredient_id}
    return _render_card(request, card, status="pending", searching=True)


@router.get("/card/{ingredient_id}", response_class=HTMLResponse)
def ingredient_card(request: Request, ingredient_id: int, db: Session = Depends(get_db)):
    """Collapse back to the base pending card (Cancel from the search state)."""
    card = _pending_card_row(db, ingredient_id) or {"id": ingredient_id}
    return _render_card(request, card, status="pending")


@router.get("/search-ingredients", response_class=HTMLResponse)
def search_ingredients(
    request: Request,
    q: str = "",
    pending_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Live ingredient search for the mapping flow. Empty query → no results
    (we don't dump all ingredients). Active only, max 8, ILIKE %q%."""
    term = (q or "").strip()
    results: list[dict] = []
    if term:
        rows = db.execute(
            text(
                """
                SELECT id, name, category, purchase_unit,
                       COALESCE(current_price, purchase_price) AS price
                FROM ingredients
                WHERE is_active = true
                  AND name ILIKE :q
                  AND (:pid IS NULL OR id <> :pid)
                ORDER BY name
                LIMIT 8
                """
            ),
            {"q": f"%{term}%", "pid": pending_id},
        ).mappings().all()
        results = [dict(r) for r in rows]
    return templates.TemplateResponse(
        "admin/catalog_sync/_ingredient_search_results.html",
        {"request": request, "results": results, "q": term},
    )


@router.delete("/mapping/{store_id}", response_class=HTMLResponse)
def delete_mapping(request: Request, store_id: int, db: Session = Depends(get_db)):
    db.execute(
        text("DELETE FROM store_catalog_mapping WHERE store_id = :sid"),
        {"sid": store_id},
    )
    db.commit()
    ctx = _mapping_ctx(request, db)
    return templates.TemplateResponse("admin/catalog_sync/_mapping_table.html", ctx)
