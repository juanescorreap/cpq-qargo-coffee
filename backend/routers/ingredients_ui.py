from math import ceil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient

router = APIRouter(prefix="/ingredients", tags=["UI - Ingredientes"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)

_PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _categories(db: Session) -> list[str]:
    return [
        r[0]
        for r in db.query(Ingredient.category)
        .filter(Ingredient.category.isnot(None))
        .distinct()
        .order_by(Ingredient.category)
        .all()
    ]


def _build_query(db: Session, search: str, category: str, is_active: str):
    q = db.query(Ingredient).filter(Ingredient.is_active == (is_active != "false"))
    if search:
        q = q.filter(Ingredient.name.ilike(f"%{search}%"))
    if category:
        q = q.filter(Ingredient.category == category)
    return q.order_by(Ingredient.name)


def _paginate(query, page: int, per_page: int) -> tuple[list, int, int]:
    """Devuelve (items, total, total_pages)."""
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return items, total, max(1, ceil(total / per_page))


def _table_ctx(request: Request, db: Session, search: str, category: str,
               is_active: str, page: int) -> dict:
    q = _build_query(db, search, category, is_active)
    items, total, total_pages = _paginate(q, page, _PAGE_SIZE)
    return {
        "request": request,
        "ingredients": items,
        "search": search,
        "category": category,
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
    is_active: str = "true",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    ctx = _table_ctx(request, db, search, category, is_active, page)
    ctx["categories"] = _categories(db)
    return templates.TemplateResponse("ingredients/list.html", ctx)


@router.get("/tabla", response_class=HTMLResponse)
async def table_partial(
    request: Request,
    search: str = "",
    category: str = "",
    is_active: str = "true",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "ingredients/_table.html",
        _table_ctx(request, db, search, category, is_active, page),
    )


@router.get("/nuevo", response_class=HTMLResponse)
async def new_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse("ingredients/_form_modal.html", {
        "request": request,
        "ingredient": None,
        "categories": _categories(db),
    })


@router.get("/{ingredient_id}/editar", response_class=HTMLResponse)
async def edit_form(
    request: Request, ingredient_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    return templates.TemplateResponse("ingredients/_form_modal.html", {
        "request": request,
        "ingredient": ingredient,
        "categories": _categories(db),
    })


# ---------------------------------------------------------------------------
# POST / PUT / DELETE — mutaciones que retornan HTML
# ---------------------------------------------------------------------------

def _parse_form(form) -> dict:
    """Extrae y coerciona los campos del formulario de ingrediente."""
    raw_yield = form.get("yield_percentage")
    return {
        "name":              form["name"],
        "category":          form.get("category") or None,
        "purchase_price":    float(form["purchase_price"]) if form.get("purchase_price") else None,
        "purchase_unit":     form.get("purchase_unit") or None,
        "usage_unit":        form.get("usage_unit") or None,
        "conversion_factor": float(form["conversion_factor"]) if form.get("conversion_factor") else None,
        "yield_percentage":  float(raw_yield) / 100 if raw_yield else 1.0,
        "source_url":        form.get("source_url") or None,
    }


def _table_response(request: Request, db: Session) -> HTMLResponse:
    """Retorna _table.html (pág. 1, todos activos) + HX-Trigger para cerrar modal."""
    ctx = _table_ctx(request, db, "", "", "true", 1)
    response = templates.TemplateResponse("ingredients/_table.html", ctx)
    response.headers["HX-Trigger"] = "closeModal"
    return response


@router.post("", response_class=HTMLResponse)
async def create(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    ingredient = Ingredient(**_parse_form(form))
    db.add(ingredient)
    db.commit()
    db.refresh(ingredient)
    return _table_response(request, db)


@router.put("/{ingredient_id}", response_class=HTMLResponse)
async def update(
    request: Request, ingredient_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")

    form = await request.form()
    for field, value in _parse_form(form).items():
        setattr(ingredient, field, value)
    db.commit()
    db.refresh(ingredient)
    return _table_response(request, db)


@router.delete("/{ingredient_id}", response_class=HTMLResponse)
async def deactivate(
    ingredient_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    ingredient.is_active = False
    db.commit()
    return HTMLResponse("")  # HTMX reemplaza outerHTML del <tr> → lo elimina
