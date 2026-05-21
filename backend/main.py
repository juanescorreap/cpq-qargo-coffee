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

import backend.models  # noqa: F401 — registra todos los modelos en Base.metadata
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


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

app = FastAPI(
    title="CPQ Cafeterías",
    description="Sistema de Configure-Price-Quote con Scraping",
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
# ARCHIVOS ESTÁTICOS Y TEMPLATES
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


# ============================================
# STARTUP
# ============================================
@app.on_event("startup")
async def startup_event():
    """Ejecutar al iniciar la aplicación."""
    print("🚀 Iniciando aplicación...")

    if test_connection():
        init_db()
        print("✅ Conexión a Supabase OK")
    else:
        print("⚠️  Advertencia: No se pudo conectar a Supabase")

    if os.getenv("RUN_MIGRATIONS") == "true":
        print("🔄 Ejecutando migraciones...")
        try:
            from backend.scripts.init_production import init_database
            init_database()
            print("✅ Migraciones completadas")
        except Exception as e:
            print(f"❌ Error en migraciones: {e}")


# ============================================
# ENDPOINTS PRINCIPALES
# ============================================
@app.get("/", tags=["General"])
def root() -> dict:
    return {
        "message": "CPQ Cafeterías API",
        "status": "online",
        "docs": "/docs",
        "scraping_ui": "/scraping",
        "database": "Supabase",
    }


@app.get("/health", tags=["General"])
def health_check() -> dict:
    """Endpoint de health check para Railway."""
    db_status = "healthy" if test_connection() else "unhealthy"
    return {
        "status": "healthy",
        "service": "cpq-cafeterias",
        "database": db_status,
        "environment": os.getenv("RAILWAY_ENVIRONMENT", "development"),
    }


@app.get("/dashboard", response_class=HTMLResponse, tags=["General"])
async def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Renderiza el dashboard principal con estadísticas del catálogo."""
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
