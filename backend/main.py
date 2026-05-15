from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import backend.models  # noqa: F401 — registra todos los modelos en Base.metadata
from backend.database import init_db, test_connection

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
    allow_origins=["*"],
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

    Returns:
        dict: Estado general y fuente de base de datos.
    """
    return {
        "status": "ok",
        "database": "supabase",
    }


@app.get("/dashboard", response_class=HTMLResponse, tags=["General"])
async def dashboard(request: Request) -> HTMLResponse:
    """Endpoint de prueba que renderiza el template base con contexto mínimo.

    Sirve para verificar que la integración entre FastAPI, Jinja2 y los
    archivos estáticos funciona correctamente antes de construir vistas reales.

    Args:
        request: Objeto Request de FastAPI, requerido por Jinja2 para generar
                 URLs (url_for) dentro del template.

    Returns:
        HTMLResponse: Página HTML renderizada desde base.html.
    """
    return templates.TemplateResponse(
        "base.html",
        {
            "request": request,
            "title": "Dashboard - CPQ Cafeterías",
            "message": "Sistema inicializado correctamente",
        },
    )


# ---------------------------------------------------------------------------
# Entry point para ejecución directa
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
