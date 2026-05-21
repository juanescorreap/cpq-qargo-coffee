import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.store import Store

router = APIRouter(prefix="/reports", tags=["UI - Reportes"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def reports_dashboard(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Renderiza el dashboard de reportes con datos para los selectores."""
    stores = (
        db.query(Store)
        .filter(Store.is_active == True)
        .order_by(Store.name)
        .all()
    )
    ingredients = (
        db.query(Ingredient)
        .filter(Ingredient.is_active == True)
        .order_by(Ingredient.name)
        .all()
    )

    stores_json = json.dumps([{"id": s.id, "name": s.name} for s in stores])
    ingredients_json = json.dumps([{"id": i.id, "name": i.name} for i in ingredients])

    return templates.TemplateResponse("reports/dashboard.html", {
        "request":          request,
        "stores":           stores,
        "ingredients":      ingredients,
        "stores_json":      stores_json,
        "ingredients_json": ingredients_json,
    })
