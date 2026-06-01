import os
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

import backend.models  # noqa: F401 — registers all models in Base.metadata
from backend.database import get_db, init_db, test_connection
from backend.models.ingredient import Ingredient
from backend.models.product import Product
from backend.models.store import Store
from backend.routers import (
    competitors, competitors_ui, costs, costs_ui, ingredients, ingredients_ui,
    pricing, pricing_ui, product_sizes, products, products_ui, recipe_units,
    recipes, recipes_ui, reports, reports_ui, scraping, scraping_ui, stores,
    stores_ui,
)
from backend.routers import (
    regions, manufacturers, distributors,
    supply_routes, supply_route_assignments, supply_route_prices,
    supply_chain,
)


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

app = FastAPI(
    title="CPQ Qargo Coffee",
    description="Configure-Price-Quote System with Scraping",
    version="1.0.0",
)

# ============================================
# CORS
# ============================================
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS = _raw_origins.split(",") if _raw_origins != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(stores.router)
app.include_router(stores_ui.router)
app.include_router(recipe_units.router)
app.include_router(competitors.router)
app.include_router(competitors_ui.router)
app.include_router(pricing.router)
app.include_router(pricing_ui.router)
app.include_router(reports.router)
app.include_router(reports_ui.router)
app.include_router(scraping.router)
app.include_router(scraping_ui.router)
app.include_router(regions.router)
app.include_router(manufacturers.router)
app.include_router(distributors.router)
app.include_router(supply_routes.router)
app.include_router(supply_route_assignments.router)
app.include_router(supply_route_prices.router)
app.include_router(supply_chain.router)


# ============================================
# STARTUP
# ============================================
@app.on_event("startup")
async def startup_event():
    """Run when the application starts."""
    print("🚀 Starting application...")

    if test_connection():
        init_db()
        print("✅ Supabase connection OK")
    else:
        print("⚠️  Warning: Could not connect to Supabase")

    if os.getenv("RUN_MIGRATIONS") == "true":
        print("🔄 Running migrations...")
        try:
            from backend.scripts.init_production import init_database
            init_database()
            print("✅ Migrations completed")
        except Exception as e:
            print(f"❌ Error in migrations: {e}")


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


@app.get("/health", tags=["General"])
def health_check() -> dict:
    """Health check endpoint for Railway."""
    db_status = "healthy" if test_connection() else "unhealthy"
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
