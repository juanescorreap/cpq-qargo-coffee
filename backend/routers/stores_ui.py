from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, text
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.product import Product, RecipeIngredient, StoreProduct
from backend.models.store import Store, StoreIngredientPrice
from backend.models.supply_chain import (
    Distributor,
    Manufacturer,
    Region,
    SupplyRoutePrice,
)

router = APIRouter(prefix="/stores", tags=["UI - Stores"])

templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


@router.get("/{store_id}/pricing-overview")
def store_pricing_overview(store_id: int):
    """Entry point 2: deep-link to the Pricing Overview preselected on this store."""
    return RedirectResponse(url=f"/pricing/overview?store_id={store_id}", status_code=303)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _price_rows(store_id: int, db: Session) -> list[dict]:
    rows = (
        db.query(StoreIngredientPrice, Ingredient.name, Ingredient.usage_unit)
        .join(Ingredient, StoreIngredientPrice.ingredient_id == Ingredient.id)
        .filter(StoreIngredientPrice.store_id == store_id)
        .order_by(Ingredient.name)
        .all()
    )
    return [
        {
            "id":              p.id,
            "ingredient_id":   p.ingredient_id,
            "ingredient_name": name,
            "unit":            unit or "",
            "local_price":     float(p.local_price) if p.local_price else 0.0,
            "local_supplier":  p.local_supplier or "",
            "updated_at":      p.updated_at,
        }
        for p, name, unit in rows
    ]


def _product_rows(store_id: int, db: Session) -> list[dict]:
    rows = (
        db.query(Product, StoreProduct)
        .outerjoin(
            StoreProduct,
            and_(
                StoreProduct.product_id == Product.id,
                StoreProduct.store_id == store_id,
            ),
        )
        .filter(Product.is_active == True, Product.is_sub_recipe == False)
        .order_by(Product.category, Product.name)
        .all()
    )
    return [
        {
            "id":             product.id,
            "name":           product.name,
            "category":       product.category or "",
            "is_available":   sp.is_available if sp else False,
            "seasonal_start": sp.seasonal_start_date.isoformat() if sp and sp.seasonal_start_date else "",
            "seasonal_end":   sp.seasonal_end_date.isoformat()   if sp and sp.seasonal_end_date   else "",
        }
        for product, sp in rows
    ]


def _ingredients_for_select(db: Session) -> list:
    return (
        db.query(Ingredient)
        .filter(Ingredient.is_active == True)
        .order_by(Ingredient.name)
        .all()
    )


def _regions_for_select(db: Session) -> list:
    return db.query(Region).filter(Region.is_active == True).order_by(Region.code).all()


def _resolve_active_routes(store_id: int, db: Session) -> list[dict]:
    """Return resolved supply routes for every ingredient in the store's active menu.

    Uses a single LATERAL SQL call to invoke fn_resolve_supply_route for all
    ingredients at once, then bulk-loads supporting entities — down from 5×N
    queries to 6 total regardless of N.
    """
    ingredient_ids = (
        db.query(RecipeIngredient.ingredient_id)
        .join(Product, RecipeIngredient.product_id == Product.id)
        .join(
            StoreProduct,
            and_(
                StoreProduct.product_id == Product.id,
                StoreProduct.store_id == store_id,
                StoreProduct.is_available == True,
            ),
        )
        .filter(Product.is_active == True, Product.is_sub_recipe == False)
        .distinct()
        .all()
    )

    if not ingredient_ids:
        return []

    iid_list = [iid for (iid,) in ingredient_ids]

    # Single LATERAL call — fn_resolve_supply_route invoked once per row via unnest.
    # iid_list contains integer PKs from the DB; no injection risk.
    iid_literals = ",".join(str(i) for i in iid_list)
    route_rows = db.execute(
        text(
            f"SELECT t.ing_id, r.assignment_id, r.supply_route_id, r.scope, r.priority, "
            f"r.manufacturer_id, r.distributor_id, r.is_direct "
            f"FROM unnest(ARRAY[{iid_literals}]::int[]) AS t(ing_id) "
            f"LEFT JOIN LATERAL public.fn_resolve_supply_route(t.ing_id, :sid) AS r ON true"
        ),
        {"sid": store_id},
    ).fetchall()

    # Bulk load ingredients
    ingredients_by_id = {
        i.id: i
        for i in db.query(Ingredient).filter(Ingredient.id.in_(iid_list)).all()
    }

    # Bulk load manufacturers
    mfr_ids = list({r.manufacturer_id for r in route_rows if r.manufacturer_id})
    manufacturers_by_id: dict = {}
    if mfr_ids:
        manufacturers_by_id = {
            m.id: m
            for m in db.query(Manufacturer).filter(Manufacturer.id.in_(mfr_ids)).all()
        }

    # Bulk load distributors
    dst_ids = list({r.distributor_id for r in route_rows if r.distributor_id})
    distributors_by_id: dict = {}
    if dst_ids:
        distributors_by_id = {
            d.id: d
            for d in db.query(Distributor).filter(Distributor.id.in_(dst_ids)).all()
        }

    # Bulk load active prices for all resolved routes
    route_ids = list({r.supply_route_id for r in route_rows if r.supply_route_id})
    prices_by_route: dict = {}
    if route_ids:
        prices_by_route = {
            p.supply_route_id: p
            for p in db.query(SupplyRoutePrice)
            .filter(
                SupplyRoutePrice.supply_route_id.in_(route_ids),
                SupplyRoutePrice.valid_until.is_(None),
            )
            .all()
        }

    results = []
    for row in route_rows:
        iid = row.ing_id
        ingredient = ingredients_by_id.get(iid)
        ing_name = ingredient.name if ingredient else f"Ingredient #{iid}"

        if row.supply_route_id is not None:
            mfr = manufacturers_by_id.get(row.manufacturer_id)
            dst = distributors_by_id.get(row.distributor_id)
            price = prices_by_route.get(row.supply_route_id)
            results.append({
                "ingredient_name": ing_name,
                "ingredient_id": iid,
                "resolved": True,
                "scope": row.scope,
                "priority": row.priority,
                "supply_route_id": row.supply_route_id,
                "source_name": mfr.name if mfr else (dst.name if dst else "Direct purchase"),
                "is_direct": row.is_direct,
                "price": price,
            })
        else:
            results.append({
                "ingredient_name": ing_name,
                "ingredient_id": iid,
                "resolved": False,
                "scope": None,
                "priority": None,
                "supply_route_id": None,
                "source_name": None,
                "is_direct": False,
                "price": None,
            })

    return sorted(results, key=lambda x: (0 if not x["resolved"] else 1, x["ingredient_name"]))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def store_list(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    stores = db.query(Store).order_by(Store.city, Store.name).all()
    return templates.TemplateResponse("stores/list.html", {
        "request": request,
        "stores":  stores,
    })


@router.get("/{store_id}", response_class=HTMLResponse)
async def store_detail(
    request: Request, store_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    store = db.get(Store, store_id)
    if not store:
        return HTMLResponse("Store not found", status_code=404)

    return templates.TemplateResponse("stores/detail.html", {
        "request":               request,
        "store":                 store,
        "region":                db.get(Region, store.region_id) if store.region_id else None,
        "regions":               _regions_for_select(db),
        "active_routes":         _resolve_active_routes(store_id, db),
        "prices":                _price_rows(store_id, db),
        "products":              _product_rows(store_id, db),
        "ingredients_available": _ingredients_for_select(db),
        "error":                 None,
    })


# ── Local prices (HTMX partials) ────────────────────────────────────────────

@router.post("/{store_id}/prices-htmx", response_class=HTMLResponse)
async def upsert_price(
    request: Request, store_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    store = db.get(Store, store_id)
    form  = await request.form()

    raw_ing   = form.get("ingredient_id", "")
    raw_price = form.get("local_price",   "")

    def _prices_response(error: Optional[str] = None) -> HTMLResponse:
        return templates.TemplateResponse("stores/_prices.html", {
            "request":               request,
            "store":                 store,
            "prices":                _price_rows(store_id, db),
            "ingredients_available": _ingredients_for_select(db),
            "error":                 error,
        })

    if not raw_ing or not raw_price:
        return _prices_response("Ingredient and price are required.")

    try:
        local_price = float(raw_price)
        if local_price < 0:
            return _prices_response("Price cannot be negative.")
    except ValueError:
        return _prices_response("Invalid price.")

    ingredient_id    = int(raw_ing)
    local_supplier   = form.get("local_supplier", "").strip() or None

    existing = (
        db.query(StoreIngredientPrice)
        .filter(
            StoreIngredientPrice.store_id     == store_id,
            StoreIngredientPrice.ingredient_id == ingredient_id,
        )
        .first()
    )
    if existing:
        existing.local_price    = local_price
        existing.local_supplier = local_supplier
    else:
        db.add(StoreIngredientPrice(
            store_id=store_id, ingredient_id=ingredient_id,
            local_price=local_price, local_supplier=local_supplier,
        ))
    db.commit()
    return _prices_response()


@router.delete("/{store_id}/prices-htmx/{ingredient_id}", response_class=HTMLResponse)
async def delete_price(
    request: Request, store_id: int, ingredient_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    store = db.get(Store, store_id)
    price = (
        db.query(StoreIngredientPrice)
        .filter(
            StoreIngredientPrice.store_id     == store_id,
            StoreIngredientPrice.ingredient_id == ingredient_id,
        )
        .first()
    )
    if price:
        db.delete(price)
        db.commit()

    return templates.TemplateResponse("stores/_prices.html", {
        "request":               request,
        "store":                 store,
        "prices":                _price_rows(store_id, db),
        "ingredients_available": _ingredients_for_select(db),
        "error":                 None,
    })


# ── Product availability (HTMX partials) ────────────────────────────────────

@router.post("/{store_id}/products-htmx/{product_id}", response_class=HTMLResponse)
async def upsert_store_product(
    request: Request, store_id: int, product_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    form = await request.form()

    is_available   = form.get("is_available",   "0") == "1"
    raw_start      = form.get("seasonal_start", "").strip()
    raw_end        = form.get("seasonal_end",   "").strip()
    seasonal_start = date.fromisoformat(raw_start) if raw_start else None
    seasonal_end   = date.fromisoformat(raw_end)   if raw_end   else None

    sp = (
        db.query(StoreProduct)
        .filter(
            StoreProduct.store_id  == store_id,
            StoreProduct.product_id == product_id,
        )
        .first()
    )
    if sp:
        sp.is_available        = is_available
        sp.seasonal_start_date = seasonal_start
        sp.seasonal_end_date   = seasonal_end
    else:
        sp = StoreProduct(
            store_id=store_id, product_id=product_id,
            is_available=is_available,
            seasonal_start_date=seasonal_start,
            seasonal_end_date=seasonal_end,
        )
        db.add(sp)
    db.commit()

    product = db.get(Product, product_id)
    row = {
        "id":             product_id,
        "name":           product.name,
        "category":       product.category or "",
        "is_available":   sp.is_available,
        "seasonal_start": sp.seasonal_start_date.isoformat() if sp.seasonal_start_date else "",
        "seasonal_end":   sp.seasonal_end_date.isoformat()   if sp.seasonal_end_date   else "",
    }
    return templates.TemplateResponse("stores/_product_row.html", {
        "request": request,
        "store":   store,
        "product": row,
    })


# ── Region assignment (HTMX partial) ────────────────────────────────────────

@router.post("/{store_id}/region-htmx", response_class=HTMLResponse)
async def assign_region(
    request: Request, store_id: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Assign or clear the region for a store.

    Returns the `_region_section.html` partial that replaces only the
    region section inside the info card, preserving the tab state.
    """
    form = await request.form()
    raw_region = form.get("region_id", "").strip()

    store = db.get(Store, store_id)
    if store:
        store.region_id = int(raw_region) if raw_region else None
        db.commit()
        db.refresh(store)

    return templates.TemplateResponse("stores/_region_section.html", {
        "request": request,
        "store":   store,
        "region":  db.get(Region, store.region_id) if store and store.region_id else None,
        "regions": _regions_for_select(db),
    })
