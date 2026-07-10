import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, text
from sqlalchemy.orm import Session

import backend.models  # noqa: F401 — registers all models in Base.metadata
from backend.config import settings
from backend.database import get_db, test_connection
from backend.models.ingredient import Ingredient
from backend.models.product import Product
from backend.models.store import Store
from backend.routers import (
    competitors, competitors_ui, costs, costs_ui, ingredients, ingredients_ui,
    pricing, pricing_ui, product_sizes, products, products_ui, recipe_units,
    recipes, recipes_ui, reports, reports_ui, scraping, scraping_ui, stores,
    stores_ui, pricing_overview_ui, catalog_sync_ui, price_review_ui,
    ingredient_names_ui,
)
from backend.routers import (
    currencies,
    regions, manufacturers, distributors,
    supply_routes, supply_route_assignments, supply_route_prices,
    supply_chain, supply_chain_ui,
)
from backend.routers import calc_status


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

log = logging.getLogger("cpq.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan (replaces the deprecated on_event handlers).

    On boot: verify DB connectivity (non-fatal — a down DB still serves /health),
    then, when RUN_MIGRATIONS=true (set this only on the web service, once), bring
    the schema to head via Alembic. The schema is owned exclusively by Alembic —
    Base.metadata.create_all() is intentionally NOT called here. A migration
    failure is allowed to propagate so the deploy fails loudly instead of serving
    a half-migrated schema.
    """
    log.info("Starting application...")
    if test_connection():
        log.info("Supabase connection OK")
    else:
        log.warning("Could not connect to Supabase at startup")

    if os.getenv("RUN_MIGRATIONS") == "true":
        log.info("RUN_MIGRATIONS=true -> applying Alembic migrations...")
        from backend.scripts.init_production import init_database

        init_database()  # raises on failure -> boot aborts, deploy surfaces it
        log.info("Migrations applied")

    # Weekly catalog sync (only starts when the catalog API is configured).
    from backend.services.catalog_scheduler import start_catalog_scheduler

    app.state.catalog_scheduler = start_catalog_scheduler()

    yield
    # Shutdown: stop the scheduler if it was started.
    sched = getattr(app.state, "catalog_scheduler", None)
    if sched is not None:
        sched.shutdown(wait=False)


app = FastAPI(
    title="CPQ Qargo Coffee",
    description="Configure-Price-Quote System with Scraping",
    version="1.0.0",
    lifespan=lifespan,
)

# ============================================
# CORS
# ============================================
# Origins come from Settings, which in production replaces the "*" default with
# the explicit _PRODUCTION_ORIGINS allow-list. Credentials cannot be combined
# with a "*" origin per the CORS spec, so they are only enabled for an explicit
# allow-list. (Auth is HTTP Basic via header, so cross-origin cookies are not
# required for the app to function.)
ALLOWED_ORIGINS = settings.ALLOWED_ORIGINS
_cors_allow_credentials = ALLOWED_ORIGINS != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# AUTH — HTTP Basic gate (whole app)
# ============================================
# Enabled only when BASIC_AUTH_USER + BASIC_AUTH_PASSWORD are set in the env
# (do this in the deploy platform). Disabled in local/test so nothing breaks.
import base64
import secrets

from starlette.responses import PlainTextResponse


# Paths served without auth: static assets must load for the page to render
# styles, and /health must stay reachable for the Railway healthcheck.
_AUTH_PUBLIC_PREFIXES = ("/static/", "/health", "/favicon.ico")


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    _is_public = request.url.path.startswith(_AUTH_PUBLIC_PREFIXES)
    if settings.auth_enabled and not _is_public and request.method != "OPTIONS":
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = (
                    secrets.compare_digest(user, settings.BASIC_AUTH_USER)
                    and secrets.compare_digest(pwd, settings.BASIC_AUTH_PASSWORD)
                )
            except Exception:  # noqa: BLE001 — malformed header => unauthorized
                ok = False
        if not ok:
            return PlainTextResponse(
                "Unauthorized", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="CPQ Qargo Coffee"'},
            )
    return await call_next(request)

# ============================================
# STATIC FILES AND TEMPLATES
# ============================================
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")

# ============================================
# ROUTERS
# ============================================

app.include_router(ingredients.router)
app.include_router(ingredients_ui.router)
app.include_router(products.router)
app.include_router(products_ui.router)
app.include_router(recipes.router)
app.include_router(recipes_ui.router)
app.include_router(product_sizes.router)
app.include_router(costs.router)
app.include_router(costs_ui.router)
app.include_router(calc_status.router)
app.include_router(stores.router)
app.include_router(stores_ui.router)
app.include_router(recipe_units.router)
app.include_router(competitors.router)
app.include_router(competitors_ui.router)
app.include_router(pricing.router)
app.include_router(pricing_ui.router)
app.include_router(pricing_overview_ui.router)
app.include_router(catalog_sync_ui.router)
app.include_router(price_review_ui.router)
app.include_router(ingredient_names_ui.router)
app.include_router(reports.router)
app.include_router(reports_ui.router)
app.include_router(scraping.router)
app.include_router(scraping_ui.router)
app.include_router(currencies.router)
app.include_router(regions.router)
app.include_router(manufacturers.router)
app.include_router(distributors.router)
app.include_router(supply_routes.router)
app.include_router(supply_route_assignments.router)
app.include_router(supply_route_prices.router)
app.include_router(supply_chain.router)
app.include_router(supply_chain_ui.router)


# ============================================
# STARTUP
# ============================================
# ============================================
# MAIN ENDPOINTS
# ============================================
@app.get("/", tags=["General"])
def root() -> dict:
    return {
        "message": "CPQ Qargo Coffee API",
        "status": "online",
        "docs": "/docs",
        "scraping_ui": "/scraping",
        "database": "Supabase",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """No favicon asset; return 204 so browsers stop logging 404s."""
    return Response(status_code=204)


@app.get("/health", tags=["General"])
def health_check(db: Session = Depends(get_db)) -> dict:
    """Health check endpoint for Railway. Reuses the request-scoped session and
    runs a minimal `SELECT 1` (quiet — no logging) to avoid connection churn and
    log spam under the platform's frequent polling."""
    try:
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception:  # noqa: BLE001 — report unhealthy without crashing the probe
        db_status = "unhealthy"
    return {
        "status": "healthy",
        "service": "cpq-cafeterias",
        "database": db_status,
        "environment": os.getenv("RAILWAY_ENVIRONMENT", "development"),
    }


@app.get("/dashboard", response_class=HTMLResponse, tags=["General"])
async def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Renders the main dashboard with catalog statistics."""
    stats = {
        "total_ingredients": db.query(Ingredient).filter(Ingredient.is_active == True).count(),
        "total_products":    db.query(Product).filter(Product.is_active == True).count(),
        "total_stores":      db.query(Store).filter(Store.is_active == True).count(),
        "total_categories":  db.query(func.count(func.distinct(Ingredient.category))).scalar(),
    }
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "stats": stats},
    )


# ============================================
# ENTRY POINT
# ============================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
