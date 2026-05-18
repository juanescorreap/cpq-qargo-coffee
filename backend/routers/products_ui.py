from math import ceil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.product import Product

router = APIRouter(prefix="/products", tags=["UI - Productos"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)

_PAGE_SIZE = 25


def _categories(db: Session) -> list[str]:
    return [
        r[0]
        for r in db.query(Product.category)
        .filter(Product.category.isnot(None))
        .distinct()
        .order_by(Product.category)
        .all()
    ]


def _build_query(db: Session, search: str, category: str, type_: str, is_active: str):
    q = db.query(Product).filter(Product.is_active == (is_active != "false"))
    if search:
        q = q.filter(Product.name.ilike(f"%{search}%"))
    if category:
        q = q.filter(Product.category == category)
    if type_ == "sub_recipe":
        q = q.filter(Product.is_sub_recipe == True)
    elif type_ == "product":
        q = q.filter(Product.is_sub_recipe == False)
    return q.order_by(Product.name)


def _paginate(query, page: int, per_page: int) -> tuple[list, int, int]:
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return items, total, max(1, ceil(total / per_page))


def _table_ctx(request: Request, db: Session, search: str, category: str,
               type_: str, is_active: str, page: int) -> dict:
    q = _build_query(db, search, category, type_, is_active)
    items, total, total_pages = _paginate(q, page, _PAGE_SIZE)
    return {
        "request": request,
        "products": items,
        "search": search,
        "category": category,
        "type": type_,
        "is_active": is_active,
        "page": page,
        "total": total,
        "total_pages": total_pages,
    }


# ---------------------------------------------------------------------------
# GET — páginas y partiales
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def list_page(
    request: Request,
    search: str = "",
    category: str = "",
    type: str = "",
    is_active: str = "true",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    ctx = _table_ctx(request, db, search, category, type, is_active, page)
    ctx["categories"] = _categories(db)
    return templates.TemplateResponse("products/list.html", ctx)


@router.get("/tabla", response_class=HTMLResponse)
async def table_partial(
    request: Request,
    search: str = "",
    category: str = "",
    type: str = "",
    is_active: str = "true",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "products/_table.html",
        _table_ctx(request, db, search, category, type, is_active, page),
    )


@router.get("/nuevo", response_class=HTMLResponse)
async def new_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse("products/_form_modal.html", {
        "request": request,
        "product": None,
        "categories": _categories(db),
    })


@router.get("/{product_id}/editar", response_class=HTMLResponse)
async def edit_form(
    request: Request, product_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return templates.TemplateResponse("products/_form_modal.html", {
        "request": request,
        "product": product,
        "categories": _categories(db),
    })


# ---------------------------------------------------------------------------
# POST / PUT / DELETE — mutaciones que retornan HTML
# ---------------------------------------------------------------------------

def _parse_form(form) -> dict:
    return {
        "name":                  form["name"],
        "category":              form.get("category") or None,
        "base_size_oz":          float(form["base_size_oz"]) if form.get("base_size_oz") else None,
        "prep_time_minutes":     float(form["prep_time_minutes"]) if form.get("prep_time_minutes") else None,
        "labor_cost_per_minute": float(form["labor_cost_per_minute"]) if form.get("labor_cost_per_minute") else 0,
        "is_sub_recipe":         form.get("is_sub_recipe") == "true",
    }


def _table_response(request: Request, db: Session) -> HTMLResponse:
    ctx = _table_ctx(request, db, "", "", "", "true", 1)
    response = templates.TemplateResponse("products/_table.html", ctx)
    response.headers["HX-Trigger"] = "closeModal"
    return response


@router.post("", response_class=HTMLResponse)
async def create(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    product = Product(**_parse_form(form))
    db.add(product)
    db.commit()
    db.refresh(product)
    return _table_response(request, db)


@router.put("/{product_id}", response_class=HTMLResponse)
async def update(
    request: Request, product_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    form = await request.form()
    for field, value in _parse_form(form).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return _table_response(request, db)


@router.delete("/{product_id}", response_class=HTMLResponse)
async def deactivate(
    product_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    product.is_active = False
    db.commit()
    return HTMLResponse("")
