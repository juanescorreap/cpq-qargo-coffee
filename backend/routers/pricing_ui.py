import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.product import Product, ProductSize
from backend.models.store import Store

router = APIRouter(prefix="/pricing", tags=["UI - Precios"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


@router.get("/manager", response_class=HTMLResponse)
async def pricing_manager(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    products = (
        db.query(Product)
        .filter(Product.is_active == True)
        .order_by(Product.name)
        .all()
    )
    stores = (
        db.query(Store)
        .filter(Store.is_active == True)
        .order_by(Store.name)
        .all()
    )

    sizes_by_product = {}
    for p in products:
        sizes = (
            db.query(ProductSize)
            .filter(ProductSize.product_id == p.id)
            .order_by(ProductSize.scale_factor)
            .all()
        )
        sizes_by_product[p.id] = [
            {"id": s.id, "name": s.size_name, "volume": float(s.volume_oz) if s.volume_oz else None}
            for s in sizes
        ]

    products_json = json.dumps([
        {
            "id":    p.id,
            "name":  p.name,
            "sizes": sizes_by_product.get(p.id, []),
        }
        for p in products
    ])

    stores_json = json.dumps([
        {"id": s.id, "currency": s.default_currency_code or "COP"}
        for s in stores
    ])

    return templates.TemplateResponse("pricing/manager.html", {
        "request":       request,
        "products":      products,
        "stores":        stores,
        "products_json": products_json,
        "stores_json":   stores_json,
    })
