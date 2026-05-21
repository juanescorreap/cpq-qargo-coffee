"""
UI router for the scraping dashboard (Jinja2 / HTMX).

All endpoints here return HTML.  The JSON API lives in routers/scraping.py.

Route map
---------
GET  /scraping                              → full dashboard page
GET  /scraping/partials/scrapers            → scrapers grid partial
GET  /scraping/partials/ingredients         → ingredients table partial
GET  /scraping/partials/competitors         → competitors list partial
GET  /scraping/partials/logs               → activity log partial

POST /scraping/partials/test                → test-scraper result HTML
POST /scraping/partials/scrape-ingredient/{id}    → single ingredient result HTML
POST /scraping/partials/scrape-competitor/{id}    → competitor menu result HTML
POST /scraping/partials/scrape-all-ingredients    → batch result HTML
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.competitor import Competitor, CompetitorProduct
from backend.models.ingredient import Ingredient, IngredientPriceHistory
from backend.services.scraping.core.exceptions import ScraperNotFoundError
from backend.services.scraping.scraper_manager import ScraperManager

router = APIRouter(prefix="/scraping", tags=["scraping-ui"])
logger = logging.getLogger("ui.scraping")

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _manager(db: Session) -> ScraperManager:
    return ScraperManager(db)


def _html(request: Request, template: str, ctx: dict) -> HTMLResponse:
    ctx["request"] = request
    return templates.TemplateResponse(template, ctx)


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Main scraping dashboard page."""
    return _html(request, "scraping/dashboard.html", {})


# ---------------------------------------------------------------------------
# Partials — data loaders (GET, triggered by HTMX on tab activation)
# ---------------------------------------------------------------------------

@router.get("/partials/scrapers", response_class=HTMLResponse)
def partial_scrapers(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        scrapers = _manager(db).list_available_scrapers()
    except Exception as exc:
        logger.error("Error loading scrapers list: %s", exc)
        scrapers = []
    return _html(request, "scraping/_scrapers_grid.html", {"available_scrapers": scrapers})


@router.get("/partials/ingredients", response_class=HTMLResponse)
def partial_ingredients(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    ingredients = (
        db.query(Ingredient)
        .filter(Ingredient.source_url.isnot(None), Ingredient.is_active.is_(True))
        .order_by(Ingredient.name)
        .all()
    )
    return _html(request, "scraping/_ingredients_table.html", {"ingredients": ingredients})


@router.get("/partials/competitors", response_class=HTMLResponse)
def partial_competitors(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = (
        db.query(Competitor, func.count(CompetitorProduct.id).label("product_count"))
        .outerjoin(CompetitorProduct, CompetitorProduct.competitor_id == Competitor.id)
        .group_by(Competitor.id)
        .order_by(Competitor.name)
        .all()
    )
    competitors = [(c, count) for c, count in rows]
    return _html(request, "scraping/_competitors_list.html", {"competitors": competitors})


@router.get("/partials/logs", response_class=HTMLResponse)
def partial_logs(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = (
        db.query(IngredientPriceHistory, Ingredient.name.label("ingredient_name"))
        .join(Ingredient, Ingredient.id == IngredientPriceHistory.ingredient_id)
        .filter(IngredientPriceHistory.source == "scraping")
        .order_by(IngredientPriceHistory.changed_at.desc())
        .limit(50)
        .all()
    )
    history = [(entry, name) for entry, name in rows]
    return _html(request, "scraping/_price_history.html", {"history": history})


# ---------------------------------------------------------------------------
# Action partials — return inline result HTML (POST, triggered by buttons)
# ---------------------------------------------------------------------------

@router.post("/partials/test", response_class=HTMLResponse)
async def partial_test_scraper(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """
    Run a test scrape and return an inline result snippet.

    Reads form data so HTMX can send hx-vals as application/x-www-form-urlencoded.
    """
    form = await request.form()
    scraper_id = form.get("scraper_id", "")
    search_query = form.get("search_query", "coffee")
    limit = int(form.get("limit", 3))

    mgr = _manager(db)
    try:
        scraper = mgr.get_scraper(scraper_id)
    except ScraperNotFoundError as exc:
        return HTMLResponse(_inline_error(f"Scraper no encontrado: {exc.message}"))
    except Exception as exc:
        return HTMLResponse(_inline_error(str(exc)))

    try:
        with scraper:
            products = scraper.search_products(query=search_query, limit=limit)
        return HTMLResponse(_inline_test_result(scraper_id, products))
    except Exception as exc:
        logger.error("Test scraper '%s' failed: %s", scraper_id, exc)
        return HTMLResponse(_inline_error(str(exc)))


@router.post("/partials/scrape-ingredient/{ingredient_id}", response_class=HTMLResponse)
def partial_scrape_ingredient(
    ingredient_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    result = _manager(db).scrape_ingredient(ingredient_id, update_db=True)
    if result["success"]:
        pct = result["price_change_pct"] or 0.0
        sign = "+" if pct >= 0 else ""
        color = "text-emerald-600" if pct <= 0 else "text-red-600"
        html = (
            f'<span class="font-mono font-semibold text-espresso">'
            f'${result["new_price"]:,.0f}</span> '
            f'<span class="{color} text-xs">({sign}{pct:.1f}%)</span>'
        )
    else:
        html = _inline_error(result.get("error", "Error desconocido"))
    return HTMLResponse(html)


@router.post("/partials/scrape-competitor/{competitor_id}", response_class=HTMLResponse)
def partial_scrape_competitor(
    competitor_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    result = _manager(db).scrape_competitor_menu(competitor_id)
    if result["success"]:
        html = (
            f'<div class="rounded-lg bg-emerald-50 border border-emerald-200 px-3 py-2 text-xs text-emerald-700">'
            f'✓ {result["total_products_found"]} productos — '
            f'{result["new_products"]} nuevos, {result["updated_products"]} actualizados'
            f'</div>'
        )
    else:
        html = _inline_error(result.get("error", "Error desconocido"))
    return HTMLResponse(html)


@router.post("/partials/scrape-all-ingredients", response_class=HTMLResponse)
def partial_scrape_all(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    result = _manager(db).scrape_all_ingredients()
    html = (
        f'<div class="rounded-lg bg-stone-50 border border-stone-200 px-4 py-3 text-sm">'
        f'<span class="font-semibold text-espresso">{result["success"]}</span>'
        f' / {result["total"]} ingredientes actualizados'
        f' — <span class="text-red-600">{result["failed"]} errores</span>'
        f', <span class="text-stone-400">{result["skipped"]} omitidos</span>'
        f'</div>'
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Private snippet builders
# ---------------------------------------------------------------------------

def _inline_error(message: str) -> str:
    return (
        f'<div class="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">'
        f'✗ {message}'
        f'</div>'
    )


def _inline_test_result(scraper_id: str, products: list) -> str:
    if not products:
        return (
            '<div class="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-700">'
            '⚠ Scraper funciona pero no se encontraron productos. Verifica los selectores CSS.'
            '</div>'
        )
    rows = "".join(
        f'<li class="flex justify-between gap-4 py-1">'
        f'<span class="truncate text-stone-600">{p.product_name}</span>'
        f'<span class="font-mono text-stone-700 shrink-0">${p.price:,.0f}</span>'
        f'</li>'
        for p in products
    )
    return (
        f'<div class="rounded-lg bg-emerald-50 border border-emerald-200 px-3 py-2 text-xs">'
        f'<p class="font-semibold text-emerald-700 mb-1">✓ {len(products)} producto(s) encontrado(s)</p>'
        f'<ul class="divide-y divide-emerald-100">{rows}</ul>'
        f'</div>'
    )
