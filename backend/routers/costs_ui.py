from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services import catalog_queries
from backend.services.cost_calculator import CostCalculator

router = APIRouter(prefix="/costs", tags=["UI - Costos"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


@router.get("", response_class=RedirectResponse)
@router.get("/", response_class=RedirectResponse)
async def costs_redirect() -> RedirectResponse:
    return RedirectResponse(url="/costs/calculator", status_code=302)


@router.get("/calculator", response_class=HTMLResponse)
async def cost_calculator(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    products = catalog_queries.active_products(db)
    stores = catalog_queries.active_stores(db)
    return templates.TemplateResponse("costs/calculator.html", {
        "request":  request,
        "products": products,
        "stores":   stores,
    })


@router.get("/sizes", response_class=HTMLResponse)
async def load_sizes(
    request: Request,
    product_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    sizes = catalog_queries.product_sizes(db, product_id) if product_id else []
    return templates.TemplateResponse("costs/_sizes.html", {
        "request": request,
        "sizes":   sizes,
    })


@router.post("/calculate-htmx", response_class=HTMLResponse)
async def calculate_cost_htmx(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    form = await request.form()

    raw_product = form.get("product_id", "")
    if not raw_product:
        return templates.TemplateResponse("costs/_result.html", {
            "request":   request,
            "error":     "Selecciona un producto.",
            "breakdown": None,
        })

    product_id: int          = int(raw_product)
    size_id:    Optional[int] = int(form["size_id"])  if form.get("size_id")  else None
    store_id:   Optional[int] = int(form["store_id"]) if form.get("store_id") else None

    try:
        breakdown = CostCalculator(db).get_cost_breakdown(product_id, size_id, store_id)
    except (ValueError, RecursionError) as exc:
        return templates.TemplateResponse("costs/_result.html", {
            "request":   request,
            "error":     str(exc),
            "breakdown": None,
        })

    return templates.TemplateResponse("costs/_result.html", {
        "request":   request,
        "error":     None,
        "breakdown": breakdown,
    })
