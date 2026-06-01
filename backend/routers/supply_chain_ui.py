"""HTML UI router for supply chain admin pages (Fase B).

Pages:
  GET  /supply-chain/regions              — CRUD de regiones
  GET  /supply-chain/manufacturers        — CRUD de fabricantes
  GET  /supply-chain/distributors         — CRUD de distribuidores
  GET  /supply-chain/routes               — Lista de rutas con filtro por ingrediente
  GET  /supply-chain/routes/{id}          — Detalle con tabs: Precio / Referencias / Conversiones
  GET  /supply-chain/assignments          — Gestión de asignaciones vigentes

HTMX partials (no extienden base.html, reemplazan secciones específicas):
  POST   /supply-chain/regions/htmx              — Crear región
  DELETE /supply-chain/regions/htmx/{id}         — Desactivar región
  POST   /supply-chain/manufacturers/htmx        — Crear fabricante
  DELETE /supply-chain/manufacturers/htmx/{id}   — Desactivar fabricante
  POST   /supply-chain/distributors/htmx         — Crear distribuidor
  DELETE /supply-chain/distributors/htmx/{id}    — Desactivar distribuidor
  POST   /supply-chain/routes/htmx               — Crear ruta
  POST   /supply-chain/routes/{id}/prices/htmx   — Registrar precio
  POST   /supply-chain/routes/{id}/refs/htmx     — Agregar referencia de proveedor
  POST   /supply-chain/assignments/htmx          — Crear asignación
  POST   /supply-chain/assignments/htmx/{id}/close — Cerrar asignación
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.recipe_unit import RecipeUnit
from backend.models.store import Store
from backend.models.supply_chain import (
    Distributor,
    IngredientSupplierRef,
    Manufacturer,
    Region,
    SupplierUnitConversion,
    SupplyRoute,
    SupplyRouteAssignment,
    SupplyRoutePrice,
)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
router = APIRouter(prefix="/supply-chain", tags=["supply-chain-ui"])
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_regions(db: Session):
    return db.query(Region).order_by(Region.code).all()


def _all_manufacturers(db: Session):
    return db.query(Manufacturer).order_by(Manufacturer.name).all()


def _all_distributors(db: Session):
    return db.query(Distributor).order_by(Distributor.name).all()


def _route_with_joins(route: SupplyRoute, db: Session) -> dict:
    ingredient = db.get(Ingredient, route.ingredient_id)
    manufacturer = db.get(Manufacturer, route.manufacturer_id) if route.manufacturer_id else None
    distributor = db.get(Distributor, route.distributor_id) if route.distributor_id else None
    active_price = (
        db.query(SupplyRoutePrice)
        .filter(
            SupplyRoutePrice.supply_route_id == route.id,
            SupplyRoutePrice.valid_until.is_(None),
        )
        .first()
    )
    return {
        "id": route.id,
        "ingredient_id": route.ingredient_id,
        "ingredient_name": ingredient.name if ingredient else "—",
        "manufacturer_id": route.manufacturer_id,
        "manufacturer_name": manufacturer.name if manufacturer else None,
        "distributor_id": route.distributor_id,
        "distributor_name": distributor.name if distributor else None,
        "is_direct": route.is_direct,
        "is_active": route.is_active,
        "created_at": route.created_at,
        "active_price": active_price,
    }


def _assignment_with_joins(a: SupplyRouteAssignment, db: Session) -> dict:
    route = db.get(SupplyRoute, a.supply_route_id)
    ingredient = db.get(Ingredient, route.ingredient_id) if route else None
    region = db.get(Region, a.region_id) if a.region_id else None
    store = db.get(Store, a.store_id) if a.store_id else None
    return {
        "id": a.id,
        "supply_route_id": a.supply_route_id,
        "ingredient_name": ingredient.name if ingredient else "—",
        "scope_label": region.name if region else (store.name if store else "—"),
        "scope_type": "region" if region else "store",
        "region_id": a.region_id,
        "store_id": a.store_id,
        "priority": a.priority,
        "valid_from": a.valid_from,
        "valid_until": a.valid_until,
        "change_reason": a.change_reason,
        "assigned_by": a.assigned_by,
        "is_active": a.valid_until is None,
    }


# ===========================================================================
# Regions
# ===========================================================================

@router.get("/regions", response_class=HTMLResponse)
def regions_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("supply_chain/regions/list.html", {
        "request": request,
        "regions": _all_regions(db),
        "error": None,
    })


@router.post("/regions/htmx", response_class=HTMLResponse)
def create_region_htmx(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    country_code: str = Form("CO"),
    db: Session = Depends(get_db),
):
    error = None
    try:
        code = code.strip().upper()
        name = name.strip()
        if not code or not name:
            raise ValueError("Name and code are required")
        from sqlalchemy import func as sqlfunc
        existing = db.query(Region).filter(sqlfunc.upper(Region.code) == code).first()
        if existing:
            raise ValueError(f"Region with code '{code}' already exists")
        db.add(Region(name=name, code=code, country_code=country_code.strip().upper() or "CO"))
        db.commit()
    except Exception as exc:
        db.rollback()
        error = str(exc)
    return templates.TemplateResponse("supply_chain/regions/_content.html", {
        "request": request,
        "regions": _all_regions(db),
        "error": error,
    })


@router.delete("/regions/htmx/{region_id}", response_class=HTMLResponse)
def deactivate_region_htmx(
    region_id: int, request: Request, db: Session = Depends(get_db)
):
    region = db.get(Region, region_id)
    if region:
        region.is_active = False
        db.commit()
    return templates.TemplateResponse("supply_chain/regions/_content.html", {
        "request": request,
        "regions": _all_regions(db),
        "error": None,
    })


# ===========================================================================
# Manufacturers
# ===========================================================================

@router.get("/manufacturers", response_class=HTMLResponse)
def manufacturers_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("supply_chain/manufacturers/list.html", {
        "request": request,
        "manufacturers": _all_manufacturers(db),
        "error": None,
    })


@router.post("/manufacturers/htmx", response_class=HTMLResponse)
def create_manufacturer_htmx(
    request: Request,
    name: str = Form(...),
    country_code: str = Form("CO"),
    tax_id: str = Form(""),
    website: str = Form(""),
    db: Session = Depends(get_db),
):
    error = None
    try:
        name = name.strip()
        if not name:
            raise ValueError("Name is required")
        db.add(Manufacturer(
            name=name,
            country_code=country_code.strip().upper() or "CO",
            tax_id=tax_id.strip() or None,
            website=website.strip() or None,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        error = str(exc)
    return templates.TemplateResponse("supply_chain/manufacturers/_content.html", {
        "request": request,
        "manufacturers": _all_manufacturers(db),
        "error": error,
    })


@router.delete("/manufacturers/htmx/{manufacturer_id}", response_class=HTMLResponse)
def deactivate_manufacturer_htmx(
    manufacturer_id: int, request: Request, db: Session = Depends(get_db)
):
    obj = db.get(Manufacturer, manufacturer_id)
    if obj:
        obj.is_active = False
        db.commit()
    return templates.TemplateResponse("supply_chain/manufacturers/_content.html", {
        "request": request,
        "manufacturers": _all_manufacturers(db),
        "error": None,
    })


# ===========================================================================
# Distributors
# ===========================================================================

@router.get("/distributors", response_class=HTMLResponse)
def distributors_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("supply_chain/distributors/list.html", {
        "request": request,
        "distributors": _all_distributors(db),
        "error": None,
    })


@router.post("/distributors/htmx", response_class=HTMLResponse)
def create_distributor_htmx(
    request: Request,
    name: str = Form(...),
    country_code: str = Form("CO"),
    tax_id: str = Form(""),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    db: Session = Depends(get_db),
):
    error = None
    try:
        name = name.strip()
        if not name:
            raise ValueError("Name is required")
        db.add(Distributor(
            name=name,
            country_code=country_code.strip().upper() or "CO",
            tax_id=tax_id.strip() or None,
            contact_email=contact_email.strip() or None,
            contact_phone=contact_phone.strip() or None,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        error = str(exc)
    return templates.TemplateResponse("supply_chain/distributors/_content.html", {
        "request": request,
        "distributors": _all_distributors(db),
        "error": error,
    })


@router.delete("/distributors/htmx/{distributor_id}", response_class=HTMLResponse)
def deactivate_distributor_htmx(
    distributor_id: int, request: Request, db: Session = Depends(get_db)
):
    obj = db.get(Distributor, distributor_id)
    if obj:
        obj.is_active = False
        db.commit()
    return templates.TemplateResponse("supply_chain/distributors/_content.html", {
        "request": request,
        "distributors": _all_distributors(db),
        "error": None,
    })


# ===========================================================================
# Supply Routes
# ===========================================================================

def _routes_context(db: Session, ingredient_filter: Optional[int] = None) -> dict:
    q = db.query(SupplyRoute)
    if ingredient_filter:
        q = q.filter(SupplyRoute.ingredient_id == ingredient_filter)
    routes = q.order_by(SupplyRoute.ingredient_id, SupplyRoute.id).all()
    return {
        "routes": [_route_with_joins(r, db) for r in routes],
        "ingredients": db.query(Ingredient).filter(Ingredient.is_active == True).order_by(Ingredient.name).all(),
        "manufacturers": _all_manufacturers(db),
        "distributors": _all_distributors(db),
        "ingredient_filter": ingredient_filter,
    }


@router.get("/routes", response_class=HTMLResponse)
def routes_page(
    request: Request,
    ingredient_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    ctx = _routes_context(db, ingredient_id)
    ctx.update({"request": request, "error": None})
    return templates.TemplateResponse("supply_chain/routes/list.html", ctx)


@router.post("/routes/htmx", response_class=HTMLResponse)
def create_route_htmx(
    request: Request,
    ingredient_id: int = Form(...),
    source_type: str = Form(...),   # "manufacturer" | "distributor" | "direct"
    manufacturer_id: str = Form(""),
    distributor_id: str = Form(""),
    db: Session = Depends(get_db),
):
    error = None
    try:
        is_direct = source_type == "direct"
        mfr_id = int(manufacturer_id) if manufacturer_id.strip() and source_type == "manufacturer" else None
        dst_id = int(distributor_id) if distributor_id.strip() and source_type == "distributor" else None

        if not is_direct and not mfr_id and not dst_id:
            raise ValueError("Select manufacturer, distributor, or direct purchase")
        if is_direct and (mfr_id or dst_id):
            raise ValueError("Direct purchase cannot have a manufacturer or distributor")

        db.add(SupplyRoute(
            ingredient_id=ingredient_id,
            manufacturer_id=mfr_id,
            distributor_id=dst_id,
            is_direct=is_direct,
            is_active=True,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        error = str(exc)

    ctx = _routes_context(db)
    ctx.update({"request": request, "error": error})
    return templates.TemplateResponse("supply_chain/routes/list.html", ctx)


@router.delete("/routes/htmx/{route_id}", response_class=HTMLResponse)
def deactivate_route_htmx(
    route_id: int, request: Request, db: Session = Depends(get_db)
):
    route = db.get(SupplyRoute, route_id)
    if route:
        route.is_active = False
        db.commit()
    ctx = _routes_context(db)
    ctx.update({"request": request, "error": None})
    return templates.TemplateResponse("supply_chain/routes/list.html", ctx)


@router.get("/routes/{route_id}", response_class=HTMLResponse)
def route_detail_page(
    route_id: int, request: Request, db: Session = Depends(get_db)
):
    route = db.get(SupplyRoute, route_id)
    if not route:
        return templates.TemplateResponse("supply_chain/routes/list.html", {
            "request": request, "error": "Route not found",
            **_routes_context(db),
        })
    ingredient = db.get(Ingredient, route.ingredient_id)
    manufacturer = db.get(Manufacturer, route.manufacturer_id) if route.manufacturer_id else None
    distributor = db.get(Distributor, route.distributor_id) if route.distributor_id else None
    prices = (
        db.query(SupplyRoutePrice)
        .filter(SupplyRoutePrice.supply_route_id == route_id)
        .order_by(SupplyRoutePrice.valid_from.desc())
        .all()
    )
    refs = (
        db.query(IngredientSupplierRef)
        .filter(IngredientSupplierRef.supply_route_id == route_id)
        .all()
    )
    conversions = (
        db.query(SupplierUnitConversion, IngredientSupplierRef, RecipeUnit)
        .join(IngredientSupplierRef, SupplierUnitConversion.ingredient_ref_id == IngredientSupplierRef.id)
        .join(RecipeUnit, SupplierUnitConversion.recipe_unit_id == RecipeUnit.id)
        .filter(IngredientSupplierRef.supply_route_id == route_id)
        .all()
    )
    recipe_units = db.query(RecipeUnit).filter(RecipeUnit.is_active == True).order_by(RecipeUnit.name).all()
    return templates.TemplateResponse("supply_chain/routes/detail.html", {
        "request": request,
        "route": route,
        "ingredient": ingredient,
        "manufacturer": manufacturer,
        "distributor": distributor,
        "prices": prices,
        "active_price": next((p for p in prices if p.valid_until is None), None),
        "refs": refs,
        "conversions": conversions,
        "recipe_units": recipe_units,
        "error": None,
        "price_error": None,
        "ref_error": None,
    })


def _render_prices(route_id: int, request: Request, db: Session, price_error: Optional[str] = None):
    prices = (
        db.query(SupplyRoutePrice)
        .filter(SupplyRoutePrice.supply_route_id == route_id)
        .order_by(SupplyRoutePrice.valid_from.desc())
        .all()
    )
    return templates.TemplateResponse("supply_chain/routes/_prices.html", {
        "request": request,
        "route_id": route_id,
        "prices": prices,
        "active_price": next((p for p in prices if p.valid_until is None), None),
        "price_error": price_error,
    })


@router.post("/routes/{route_id}/prices/htmx", response_class=HTMLResponse)
def create_route_price_htmx(
    route_id: int,
    request: Request,
    list_price: str = Form(...),
    qargo_price: str = Form(...),
    currency_code: str = Form("COP"),
    price_per_unit: str = Form(...),
    source: str = Form(""),
    created_by: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        lp = Decimal(list_price.replace(",", "").strip())
        qp = Decimal(qargo_price.replace(",", "").strip())
        if lp <= 0 or qp <= 0:
            raise ValueError("Prices must be greater than zero")
        if qp > lp:
            raise ValueError("Negotiated price cannot exceed list price")
        currency = currency_code.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("Invalid currency code (must be 3 letters, e.g. COP)")
        if not created_by.strip():
            raise ValueError("Created by is required")

        # Close active price
        active = (
            db.query(SupplyRoutePrice)
            .filter(SupplyRoutePrice.supply_route_id == route_id, SupplyRoutePrice.valid_until.is_(None))
            .first()
        )
        if active:
            active.valid_until = date.today()

        db.add(SupplyRoutePrice(
            supply_route_id=route_id,
            list_price=lp,
            qargo_price=qp,
            currency_code=currency,
            price_per_unit=price_per_unit.strip(),
            source=source.strip() or None,
            created_by=created_by.strip(),
        ))
        db.commit()
        return _render_prices(route_id, request, db)
    except (InvalidOperation, ValueError) as exc:
        db.rollback()
        return _render_prices(route_id, request, db, price_error=str(exc))


def _render_refs(route_id: int, request: Request, db: Session, ref_error: Optional[str] = None):
    refs = (
        db.query(IngredientSupplierRef)
        .filter(IngredientSupplierRef.supply_route_id == route_id)
        .all()
    )
    # get ingredient name
    route = db.get(SupplyRoute, route_id)
    ingredient = db.get(Ingredient, route.ingredient_id) if route else None
    return templates.TemplateResponse("supply_chain/routes/_refs.html", {
        "request": request,
        "route_id": route_id,
        "ingredient": ingredient,
        "refs": refs,
        "ref_error": ref_error,
    })


@router.post("/routes/{route_id}/refs/htmx", response_class=HTMLResponse)
def create_route_ref_htmx(
    route_id: int,
    request: Request,
    ingredient_id: int = Form(...),
    external_name: str = Form(...),
    external_code: str = Form(""),
    purchase_unit: str = Form(...),
    units_per_pack: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        external_name = external_name.strip()
        purchase_unit = purchase_unit.strip()
        if not external_name or not purchase_unit:
            raise ValueError("External name and purchase unit are required")
        pack = Decimal(units_per_pack.strip()) if units_per_pack.strip() else None
        db.add(IngredientSupplierRef(
            ingredient_id=ingredient_id,
            supply_route_id=route_id,
            external_name=external_name,
            external_code=external_code.strip() or None,
            purchase_unit=purchase_unit,
            units_per_pack=pack,
            notes=notes.strip() or None,
        ))
        db.commit()
        return _render_refs(route_id, request, db)
    except Exception as exc:
        db.rollback()
        return _render_refs(route_id, request, db, ref_error=str(exc))


# ===========================================================================
# Assignments
# ===========================================================================

def _assignments_context(db: Session, show_closed: bool = False) -> dict:
    q = db.query(SupplyRouteAssignment)
    if not show_closed:
        q = q.filter(SupplyRouteAssignment.valid_until.is_(None))
    assignments_raw = q.order_by(
        SupplyRouteAssignment.region_id.nulls_last(),
        SupplyRouteAssignment.store_id.nulls_last(),
        SupplyRouteAssignment.priority,
    ).all()
    return {
        "assignments": [_assignment_with_joins(a, db) for a in assignments_raw],
        "routes": [_route_with_joins(r, db) for r in
                   db.query(SupplyRoute).filter(SupplyRoute.is_active == True).order_by(SupplyRoute.ingredient_id).all()],
        "regions": _all_regions(db),
        "stores": db.query(Store).filter(Store.is_active == True).order_by(Store.name).all(),
        "show_closed": show_closed,
    }


@router.get("/assignments", response_class=HTMLResponse)
def assignments_page(
    request: Request,
    show_closed: bool = False,
    db: Session = Depends(get_db),
):
    ctx = _assignments_context(db, show_closed)
    ctx.update({"request": request, "error": None})
    return templates.TemplateResponse("supply_chain/assignments/list.html", ctx)


@router.post("/assignments/htmx", response_class=HTMLResponse)
def create_assignment_htmx(
    request: Request,
    supply_route_id: int = Form(...),
    scope_type: str = Form(...),   # "region" | "store"
    region_id: str = Form(""),
    store_id: str = Form(""),
    priority: int = Form(1),
    valid_from: str = Form(...),
    assigned_by: str = Form(...),
    change_reason: str = Form(""),
    db: Session = Depends(get_db),
):
    error = None
    try:
        rid = int(region_id) if scope_type == "region" and region_id.strip() else None
        sid = int(store_id) if scope_type == "store" and store_id.strip() else None
        if not rid and not sid:
            raise ValueError("Select a region or store")
        vfrom = date.fromisoformat(valid_from)
        if not assigned_by.strip():
            raise ValueError("Assigned by is required")

        # Auto-close existing active assignment for same scope + priority
        q = (
            db.query(SupplyRouteAssignment)
            .filter(
                SupplyRouteAssignment.priority == priority,
                SupplyRouteAssignment.valid_until.is_(None),
            )
        )
        if rid:
            q = q.filter(SupplyRouteAssignment.region_id == rid)
        else:
            q = q.filter(SupplyRouteAssignment.store_id == sid)
        existing = q.first()
        if existing:
            existing.valid_until = vfrom
            existing.change_reason = change_reason.strip() or None

        db.add(SupplyRouteAssignment(
            supply_route_id=supply_route_id,
            region_id=rid,
            store_id=sid,
            priority=priority,
            valid_from=vfrom,
            assigned_by=assigned_by.strip(),
            change_reason=change_reason.strip() or None,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        error = str(exc)

    ctx = _assignments_context(db)
    ctx.update({"request": request, "error": error})
    return templates.TemplateResponse("supply_chain/assignments/_content.html", ctx)


@router.post("/assignments/htmx/{assignment_id}/close", response_class=HTMLResponse)
def close_assignment_htmx(
    assignment_id: int,
    request: Request,
    change_reason: str = Form(""),
    db: Session = Depends(get_db),
):
    error = None
    obj = db.get(SupplyRouteAssignment, assignment_id)
    if obj:
        if obj.valid_until is not None:
            error = "Assignment is already closed"
        else:
            obj.valid_until = date.today()
            obj.change_reason = change_reason.strip() or None
            db.commit()
    else:
        error = "Assignment not found"

    ctx = _assignments_context(db)
    ctx.update({"request": request, "error": error})
    return templates.TemplateResponse("supply_chain/assignments/_content.html", ctx)
