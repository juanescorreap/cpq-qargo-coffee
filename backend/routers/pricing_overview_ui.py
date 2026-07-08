"""Store Pricing Overview — read-only view of every product's cost/price for a store.

This screen NEVER recalculates costs. It reads already-computed rows from
``product_pricing`` and derives markup / gross-margin / status badges in Python
(same thresholds as the Pricing Manager). The only thing it resolves live is the
"No supplier" flag, which reuses the cost engine's sourcing context
(``load_context``) so the indicator matches ``costs/_result.html`` exactly.
"""

import csv
import io
from datetime import date
from math import ceil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services.cost_calculator import load_context

router = APIRouter(prefix="/pricing/overview", tags=["UI - Pricing Overview"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)

_PAGE_SIZE = 50

# Markup thresholds shared with the Pricing Manager.
_WATCH_THRESHOLD = 40.0   # markup >= 40 -> OK
_ALERT_THRESHOLD = 20.0   # markup < 20  -> Alert; 20..40 -> Watch

# Main data query. NOTE: schema deviates from the spec — categories keys on
# `slug` (not `id`) and products carries a `category` varchar (not category_id).
_ROWS_SQL = text(
    """
    SELECT
        p.id                    AS product_id,
        p.name                  AS product,
        ps.id                   AS size_id,
        ps.size_name            AS size,
        pp.calculated_cost      AS cost,
        pp.final_price          AS price,
        pp.currency_code        AS currency_code,
        c.slug                  AS category_slug,
        COALESCE(c.display_name, p.category) AS category
    FROM product_pricing pp
    JOIN products       p  ON p.id = pp.product_id
    JOIN product_sizes  ps ON ps.id = pp.size_id
    LEFT JOIN categories c ON c.slug = p.category
    WHERE pp.store_id = :store_id
      AND p.is_active = true
    ORDER BY category, p.name, ps.size_name
    """
)


# ── Helpers ────────────────────────────────────────────────────────────────
def _active_stores(db: Session) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT id, code, name FROM stores "
            "WHERE is_active = true ORDER BY code"
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _store(db: Session, store_id: int) -> Optional[dict]:
    r = db.execute(
        text("SELECT id, code, name, default_currency_code FROM stores WHERE id = :id"),
        {"id": store_id},
    ).mappings().first()
    return dict(r) if r else None


def _status_of(markup: Optional[float]) -> tuple[str, int]:
    """Return (status_key, severity). Lower severity = more severe (for sorting)."""
    if markup is None:
        return "alert", 0
    if markup < _ALERT_THRESHOLD:
        return "alert", 0
    if markup < _WATCH_THRESHOLD:
        return "watch", 1
    return "ok", 2


def _no_supplier_products(db: Session, store_id: int, product_ids: set[int]) -> set[int]:
    """Products that have at least one ingredient sourced with no supplier.

    Reuses the cost engine's ``load_context`` so the flag matches the
    ``costs/_result.html`` badge: an ingredient counts as "no supplier" when its
    resolved sourcing has ``source == 'catalog'`` (no store price, no active
    supply route). Walks the full BOM (direct lines + sub-recipes + packaging).
    """
    if not product_ids:
        return set()
    ctx = load_context(db, store_id, product_ids)

    def unsourced(ingredient_id: int, recipe_unit_id) -> bool:
        src = ctx.sourcing.get((ingredient_id, recipe_unit_id))
        return src is None or src.source == "catalog"

    flagged: set[int] = set()
    for pid in product_ids:
        seen_subs: set[int] = set()
        stack = [pid]
        bad = False
        while stack and not bad:
            cur = stack.pop()
            if cur in seen_subs:
                continue
            seen_subs.add(cur)
            for line in ctx.recipe_lines.get(cur, []):
                if unsourced(line.ingredient_id, line.recipe_unit_id):
                    bad = True
                    break
            if bad:
                break
            for sub in ctx.sub_recipes.get(cur, []):
                stack.append(sub.sub_id)
        # Packaging is keyed by size_id; scan every size of this product.
        if not bad:
            for sizes in ctx.sizes.get(pid, []):
                for pkg in ctx.packaging.get(sizes.id, []):
                    if unsourced(pkg.ingredient_id, None):
                        bad = True
                        break
                if bad:
                    break
        if bad:
            flagged.add(pid)
    return flagged


def _build_rows(
    db: Session,
    store_id: int,
    search: str,
    categories: list[str],
    alerts_only: bool,
) -> list[dict]:
    """Load, enrich and filter rows for a store (unpaginated, ungrouped)."""
    raw = db.execute(_ROWS_SQL, {"store_id": store_id}).mappings().all()
    if not raw:
        return []

    no_sup = _no_supplier_products(db, store_id, {r["product_id"] for r in raw})

    rows: list[dict] = []
    for r in raw:
        cost = float(r["cost"]) if r["cost"] is not None else 0.0
        price = float(r["price"]) if r["price"] is not None else 0.0
        markup = ((price / cost) - 1) * 100 if cost > 0 else None
        gm = ((price - cost) / price) * 100 if price > 0 else None
        status_key, severity = _status_of(markup)
        rows.append({
            "product_id": r["product_id"],
            "size_id": r["size_id"],
            "product": r["product"],
            "size": r["size"],
            "category": r["category"] or "Uncategorized",
            "cost": cost,
            "price": price,
            "currency_code": r["currency_code"],
            "markup": markup,
            "gm": gm,
            "status": status_key,
            "severity": severity,
            "no_supplier": r["product_id"] in no_sup,
        })

    # Filters (applied to the same set the CSV export reads).
    if search:
        q = search.lower()
        rows = [r for r in rows if q in r["product"].lower()]
    if categories:
        cats = set(categories)
        rows = [r for r in rows if r["category"] in cats]
    if alerts_only:
        rows = [r for r in rows if r["severity"] < 2]
    return rows


_SORT_KEYS = {
    "product": lambda r: r["product"].lower(),
    "size": lambda r: r["size"].lower(),
    "cost": lambda r: r["cost"],
    "price": lambda r: r["price"],
    "markup": lambda r: (r["markup"] is None, r["markup"] or 0.0),
    "gm": lambda r: (r["gm"] is None, r["gm"] or 0.0),
    "status": lambda r: r["severity"],
}


def _sort_rows(rows: list[dict], sort: str, direction: str) -> list[dict]:
    """Sort within category groups: category first (keeps grouping), then column."""
    key = _SORT_KEYS.get(sort, _SORT_KEYS["product"])
    reverse = direction == "desc"
    rows = sorted(rows, key=key, reverse=reverse)
    # Stable secondary pass to keep categories together and alphabetical.
    rows = sorted(rows, key=lambda r: r["category"].lower())
    return rows


def _group(rows: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for r in rows:
        if not groups or groups[-1]["category"] != r["category"]:
            groups.append({"category": r["category"], "rows": []})
        groups[-1]["rows"].append(r)
    for g in groups:
        g["count"] = len(g["rows"])
    return groups


def _split_categories(category: Optional[list[str]]) -> list[str]:
    """Category filter is multi-select: repeated ?category=A&category=B (each
    value may itself be a comma-joined list)."""
    if not category:
        return []
    out: list[str] = []
    for chunk in category:
        out.extend(c for c in chunk.split(",") if c)
    return out


def _table_ctx(
    request: Request,
    db: Session,
    store_id: int,
    search: str,
    categories: list[str],
    alerts_only: bool,
    sort: str,
    direction: str,
    page: int,
) -> dict:
    has_data = db.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM product_pricing pp JOIN products p "
            "ON p.id = pp.product_id WHERE pp.store_id = :sid AND p.is_active = true)"
        ),
        {"sid": store_id},
    ).scalar()
    rows = _build_rows(db, store_id, search, categories, alerts_only)
    rows = _sort_rows(rows, sort, direction)
    total = len(rows)
    total_pages = max(1, ceil(total / _PAGE_SIZE))
    page = min(max(1, page), total_pages)
    page_rows = rows[(page - 1) * _PAGE_SIZE: page * _PAGE_SIZE]
    return {
        "request": request,
        "store_id": store_id,
        "has_data": bool(has_data),
        "groups": _group(page_rows),
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "search": search,
        "selected_categories": categories,
        "alerts_only": alerts_only,
        "sort": sort,
        "direction": direction,
    }


def _store_categories(db: Session, store_id: int) -> list[str]:
    """Distinct category display names present in the store's priced products."""
    rows = db.execute(
        text(
            """
            SELECT DISTINCT COALESCE(c.display_name, p.category) AS category
            FROM product_pricing pp
            JOIN products p ON p.id = pp.product_id
            LEFT JOIN categories c ON c.slug = p.category
            WHERE pp.store_id = :store_id AND p.is_active = true
            ORDER BY category
            """
        ),
        {"store_id": store_id},
    ).all()
    return [r[0] for r in rows if r[0]]


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def overview_page(
    request: Request,
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    stores = _active_stores(db)
    store = _store(db, store_id) if store_id else None
    ctx = {
        "request": request,
        "stores": stores,
        "store": store,
        "store_id": store.get("id") if store else None,
        "categories": _store_categories(db, store_id) if store else [],
        "locked": False,
    }
    return templates.TemplateResponse("pricing_overview/overview.html", ctx)


@router.get("/store-changed", response_class=HTMLResponse)
def overview_store_changed(
    request: Request,
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Store dropdown changed → swap the summary+controls+table body."""
    store = _store(db, store_id) if store_id else None
    if not store:
        return HTMLResponse(
            '<div class="bg-white rounded-xl shadow-sm py-20 text-center">'
            '<p class="text-5xl mb-4">📊</p>'
            '<p class="text-lg font-semibold text-espresso">Select a store to begin</p></div>'
        )
    ctx = {
        "request": request,
        "store": store,
        "store_id": store["id"],
        "categories": _store_categories(db, store_id),
    }
    return templates.TemplateResponse("pricing_overview/_body.html", ctx)


@router.get("/summary", response_class=HTMLResponse)
def overview_summary(
    request: Request,
    store_id: int,
    db: Session = Depends(get_db),
):
    rows = _build_rows(db, store_id, "", [], False)
    n = len(rows)
    markups = [r["markup"] for r in rows if r["markup"] is not None]
    gms = [r["gm"] for r in rows if r["gm"] is not None]
    below = sum(1 for r in rows if r["severity"] < 2)
    ctx = {
        "request": request,
        "store_id": store_id,
        "count": n,
        "avg_markup": (sum(markups) / len(markups)) if markups else None,
        "avg_gm": (sum(gms) / len(gms)) if gms else None,
        "below_threshold": below,
    }
    return templates.TemplateResponse("pricing_overview/_summary.html", ctx)


@router.get("/table", response_class=HTMLResponse)
def overview_table(
    request: Request,
    store_id: int,
    search: str = "",
    category: Optional[list[str]] = Query(default=None),
    alerts_only: bool = False,
    sort: str = "product",
    direction: str = "asc",
    page: int = 1,
    db: Session = Depends(get_db),
):
    ctx = _table_ctx(
        request, db, store_id, search.strip(),
        _split_categories(category), alerts_only, sort, direction, page,
    )
    return templates.TemplateResponse("pricing_overview/_table.html", ctx)


@router.get("/export")
def overview_export(
    store_id: int,
    search: str = "",
    category: Optional[list[str]] = Query(default=None),
    alerts_only: bool = False,
    db: Session = Depends(get_db),
):
    """Stream a CSV of exactly what the filters/search currently show (all rows,
    no pagination). Column order matches the spec; `store` first so the file is
    self-describing when shared."""
    store = _store(db, store_id)
    store_code = store["code"] if store else str(store_id)
    store_label = f"{store['code']} — {store['name']}" if store else str(store_id)

    rows = _build_rows(db, store_id, search.strip(), _split_categories(category), alerts_only)
    rows = _sort_rows(rows, "product", "asc")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "store", "category", "product", "size",
        "cost_usd", "price_usd", "markup_pct", "gross_margin_pct", "status",
    ])
    for r in rows:
        writer.writerow([
            store_label,
            r["category"],
            r["product"],
            r["size"],
            f"{r['cost']:.2f}",
            f"{r['price']:.2f}",
            f"{r['markup']:.1f}" if r["markup"] is not None else "",
            f"{r['gm']:.1f}" if r["gm"] is not None else "",
            r["status"],
        ])
    output.seek(0)

    filename = f"qargo_pricing_{store_code}_{date.today():%Y-%m-%d}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
