import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import backend.models  # noqa: F401 — registra todos los modelos en Base.metadata
from backend.config import settings
from backend.database import get_db, init_db, test_connection
from backend.models.ingredient import Ingredient
from backend.models.product import Product
from backend.models.store import Store
from backend.routers import (
    competitors, competitors_ui, costs, costs_ui, ingredients, ingredients_ui,
    pricing, pricing_ui, product_sizes, products, products_ui, recipe_units, recipes, recipes_ui,
    reports, reports_ui, scraping, scraping_ui, stores, stores_ui,
)


# ---------------------------------------------------------------------------
# Rutas base del proyecto (relativas a este archivo)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


# ---------------------------------------------------------------------------
# Lifespan: reemplaza los deprecated @app.on_event handlers en FastAPI >= 0.95
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestiona el ciclo de vida de la aplicación.

    Ejecuta tareas de arranque antes de yield y tareas de cierre después.
    """
    print("🚀 Starting cpq-qargo-coffee...")

    if test_connection():
        init_db()
        print("✅ Ready to rock!")
    else:
        print("❌ No se pudo conectar a Supabase. Revisa DATABASE_URL_POOLING en .env")

    yield  # la aplicación corre aquí

    # Tareas de cierre (agregar cleanup si se necesita en el futuro)


# ---------------------------------------------------------------------------
# Aplicación principal
# ---------------------------------------------------------------------------
app = FastAPI(
    title="cpq-qargo-coffee",
    description="Sistema de costeo y pricing para cadena de cafeterías",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
# allow_origins=["*"] es válido solo en desarrollo.
# En producción reemplazar con la lista exacta de dominios permitidos.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Archivos estáticos y templates
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/", tags=["General"])
async def root() -> dict:
    """Endpoint raíz que confirma que la API está en línea.

    Returns:
        dict: Estado de la API, mensaje de bienvenida y versión.
    """
    return {
        "status": "ok",
        "message": "cpq-qargo-coffee API",
        "version": app.version,
    }


@app.get("/health", tags=["General"])
async def health() -> dict:
    """Verifica el estado de salud de la aplicación y sus dependencias.

    Usado por Railway como health-check endpoint. Retorna 200 mientras la
    aplicación esté en pie; Railway reinicia el servicio si este endpoint
    no responde según la política definida en railway.json.

    Returns:
        dict: Estado general, servicio y entorno.
    """
    return {
        "status": "healthy",
        "service": "cpq-cafeterias",
        "environment": settings.ENVIRONMENT,
        "database": "supabase",
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


# ---------------------------------------------------------------------------
# Entry point para ejecución directa
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.DEBUG,
    )
