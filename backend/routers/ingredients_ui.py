from math import ceil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient

router = APIRouter(prefix="/ingredients", tags=["UI - Ingredients"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)

_PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# Private helpers
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
    """Returns (items, total, total_pages)."""
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
# GET — pages and partials
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


def _state(search: str, category: str, is_active: str, page: int) -> dict:
    """Table view-state (filters + page) carried through the edit modal so a save
    re-renders the SAME page/filters instead of resetting to page 1."""
    return {"search": search, "category": category,
            "is_active": is_active, "page": max(1, page)}


@router.get("/nuevo", response_class=HTMLResponse)
async def new_form(
    request: Request,
    search: str = "",
    category: str = "",
    is_active: str = "true",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return templates.TemplateResponse("ingredients/_form_modal.html", {
        "request": request,
        "ingredient": None,
        "categories": _categories(db),
        "state": _state(search, category, is_active, page),
    })


@router.get("/{ingredient_id}/editar", response_class=HTMLResponse)
async def edit_form(
    request: Request,
    ingredient_id: int,
    search: str = "",
    category: str = "",
    is_active: str = "true",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    return templates.TemplateResponse("ingredients/_form_modal.html", {
        "request": request,
        "ingredient": ingredient,
        "categories": _categories(db),
        "state": _state(search, category, is_active, page),
    })


# ---------------------------------------------------------------------------
# POST / PUT / DELETE — mutations that return HTML
# ---------------------------------------------------------------------------

def _parse_form(form) -> dict:
    """Extracts and coerces the ingredient form fields. Raises ValueError on bad input."""
    name = (form.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required")

    try:
        purchase_price = float(form["purchase_price"]) if form.get("purchase_price") else None
    except ValueError:
        raise ValueError("Purchase price must be a valid number")

    try:
        conversion_factor = float(form["conversion_factor"]) if form.get("conversion_factor") else None
    except ValueError:
        raise ValueError("Conversion factor must be a valid number")

    raw_yield = form.get("yield_percentage")
    try:
        yield_pct = float(raw_yield) / 100 if raw_yield else 1.0
    except ValueError:
        raise ValueError("Yield percentage must be a valid number")

    return {
        "name":              name,
        "category":          form.get("category") or None,
        "purchase_price":    purchase_price,
        "purchase_unit":     form.get("purchase_unit") or None,
        "usage_unit":        form.get("usage_unit") or None,
        "conversion_factor": conversion_factor,
        "yield_percentage":  yield_pct,
        "source_url":        form.get("source_url") or None,
    }


def _parse_current_price(form) -> float | None:
    """The engine price. Never set directly on ingredients.current_price — it is
    synced from an INSERT into ingredient_price_history (trigger). Returns the
    submitted value, or None when the field is blank."""
    raw = form.get("current_price")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError("Current price must be a valid number")


def _record_current_price(db: Session, ingredient_id: int, new_price: float | None, old_price) -> None:
    """Insert a history row when the engine price changed, so the trigger syncs
    ingredients.current_price. No-op when unchanged or not provided."""
    if new_price is None:
        return
    if old_price is not None and float(old_price) == new_price:
        return
    db.execute(
        text(
            "INSERT INTO ingredient_price_history (ingredient_id, price, source) "
            "VALUES (:i, :p, 'manual')"
        ),
        {"i": ingredient_id, "p": new_price},
    )


def _form_error(message: str) -> HTMLResponse:
    """Returns an error banner targeted at #form-error inside the open modal."""
    html = (
        f'<div class="text-sm text-red-600 bg-red-50 border border-red-200 '
        f'rounded-lg px-3 py-2">{message}</div>'
    )
    resp = HTMLResponse(html)
    resp.headers["HX-Retarget"] = "#form-error"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp


def _state_from_form(form) -> dict:
    """Read the table view-state the modal carried back as hidden fields.
    Prefixed with _state_ so it never collides with ingredient fields (e.g. the
    ingredient's own 'category')."""
    try:
        page = int(form.get("_state_page") or 1)
    except (ValueError, TypeError):
        page = 1
    return _state(
        form.get("_state_search") or "",
        form.get("_state_category") or "",
        form.get("_state_is_active") or "true",
        page,
    )


def _table_response(request: Request, db: Session, state: dict) -> HTMLResponse:
    """Returns _table.html for the given view-state + HX-Trigger to close modal."""
    ctx = _table_ctx(
        request, db, state["search"], state["category"], state["is_active"], state["page"]
    )
    response = templates.TemplateResponse("ingredients/_table.html", ctx)
    response.headers["HX-Trigger"] = "closeModal"
    return response


@router.post("", response_class=HTMLResponse)
async def create(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    try:
        data = _parse_form(form)
        new_current = _parse_current_price(form)
        with db.begin_nested():
            ingredient = Ingredient(**data)
            db.add(ingredient)
        # current_price is set only via history (trigger), never by setattr.
        _record_current_price(db, ingredient.id, new_current, None)
        db.commit()
        db.refresh(ingredient)
    except ValueError as exc:
        return _form_error(str(exc))
    except SQLAlchemyError:
        db.rollback()
        return _form_error("Could not save ingredient. Please check your data and try again.")
    return _table_response(request, db, _state_from_form(form))


@router.put("/{ingredient_id}", response_class=HTMLResponse)
async def update(
    request: Request, ingredient_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")

    form = await request.form()
    try:
        data = _parse_form(form)
        new_current = _parse_current_price(form)
        old_current = ingredient.current_price
        with db.begin_nested():
            for field, value in data.items():
                setattr(ingredient, field, value)
        # current_price is updated only via history (trigger), never by setattr.
        _record_current_price(db, ingredient_id, new_current, old_current)
        db.commit()
        db.refresh(ingredient)
    except ValueError as exc:
        return _form_error(str(exc))
    except SQLAlchemyError:
        db.rollback()
        return _form_error("Could not save ingredient. Please check your data and try again.")
    return _table_response(request, db, _state_from_form(form))


@router.delete("/{ingredient_id}", response_class=HTMLResponse)
async def deactivate(
    ingredient_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    ingredient.is_active = False
    db.commit()
    return HTMLResponse("")  # HTMX replaces outerHTML of <tr> → removes it
