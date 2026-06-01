from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.supply_chain import (
    Distributor,
    Manufacturer,
    SupplyRoute,
    SupplyRoutePrice,
    IngredientSupplierRef,
)
from backend.schemas.supply_chain import (
    IngredientSupplierRefCreate,
    IngredientSupplierRefResponse,
    IngredientSupplierRefUpdate,
    SupplyRouteCreate,
    SupplyRoutePriceResponse,
    SupplyRouteResponse,
    SupplyRouteUpdate,
    SupplyRouteWithPrice,
)

router = APIRouter(prefix="/api/supply-routes", tags=["supply-chain"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(route_id: int, db: Session) -> SupplyRoute:
    route = db.get(SupplyRoute, route_id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supply route not found")
    return route


def _build_response(route: SupplyRoute, db: Session) -> dict:
    """Build SupplyRouteResponse dict with joined names."""
    ingredient = db.get(Ingredient, route.ingredient_id)
    manufacturer = db.get(Manufacturer, route.manufacturer_id) if route.manufacturer_id else None
    distributor = db.get(Distributor, route.distributor_id) if route.distributor_id else None
    return {
        "id": route.id,
        "ingredient_id": route.ingredient_id,
        "ingredient_name": ingredient.name if ingredient else None,
        "manufacturer_id": route.manufacturer_id,
        "manufacturer_name": manufacturer.name if manufacturer else None,
        "distributor_id": route.distributor_id,
        "distributor_name": distributor.name if distributor else None,
        "is_direct": route.is_direct,
        "is_active": route.is_active,
        "created_at": route.created_at,
    }


def _get_active_price(route_id: int, db: Session) -> Optional[SupplyRoutePrice]:
    return (
        db.query(SupplyRoutePrice)
        .filter(
            SupplyRoutePrice.supply_route_id == route_id,
            SupplyRoutePrice.valid_until.is_(None),
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Supply route endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=List[SupplyRouteResponse])
def list_supply_routes(
    ingredient_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[SupplyRouteResponse]:
    """List supply routes with optional filters. Includes joined names."""
    q = db.query(SupplyRoute)
    if ingredient_id is not None:
        q = q.filter(SupplyRoute.ingredient_id == ingredient_id)
    if is_active is not None:
        q = q.filter(SupplyRoute.is_active == is_active)
    routes = q.offset(skip).limit(limit).all()
    return [SupplyRouteResponse.model_validate(_build_response(r, db)) for r in routes]


@router.get("/{route_id}", response_model=SupplyRouteWithPrice)
def get_supply_route(route_id: int, db: Session = Depends(get_db)) -> SupplyRouteWithPrice:
    """Get a single supply route with its current active price."""
    route = _get_or_404(route_id, db)
    data = _build_response(route, db)
    active_price = _get_active_price(route_id, db)
    data["active_price"] = active_price
    return SupplyRouteWithPrice.model_validate(data)


@router.post("", response_model=SupplyRouteResponse, status_code=status.HTTP_201_CREATED)
def create_supply_route(
    body: SupplyRouteCreate, db: Session = Depends(get_db)
) -> SupplyRouteResponse:
    """Create a supply route. Validates source constraints before inserting."""
    if db.get(Ingredient, body.ingredient_id) is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    if body.manufacturer_id and db.get(Manufacturer, body.manufacturer_id) is None:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    if body.distributor_id and db.get(Distributor, body.distributor_id) is None:
        raise HTTPException(status_code=404, detail="Distributor not found")
    if body.is_direct and body.distributor_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Direct purchase route cannot have a distributor",
        )
    if not body.is_direct and not body.manufacturer_id and not body.distributor_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Non-direct route must have at least a manufacturer or distributor",
        )
    route = SupplyRoute(**body.model_dump())
    db.add(route)
    db.commit()
    db.refresh(route)
    return SupplyRouteResponse.model_validate(_build_response(route, db))


@router.put("/{route_id}", response_model=SupplyRouteResponse)
def update_supply_route(
    route_id: int, body: SupplyRouteUpdate, db: Session = Depends(get_db)
) -> SupplyRouteResponse:
    route = _get_or_404(route_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(route, field, value)
    db.commit()
    db.refresh(route)
    return SupplyRouteResponse.model_validate(_build_response(route, db))


@router.delete("/{route_id}", status_code=status.HTTP_200_OK)
def deactivate_supply_route(route_id: int, db: Session = Depends(get_db)) -> dict:
    route = _get_or_404(route_id, db)
    route.is_active = False
    db.commit()
    return {"message": "Supply route deactivated"}


@router.get("/{route_id}/active-price", response_model=Optional[SupplyRoutePriceResponse])
def get_active_price(
    route_id: int, db: Session = Depends(get_db)
) -> Optional[SupplyRoutePriceResponse]:
    """Return the current active price for a supply route, or null if none set."""
    _get_or_404(route_id, db)
    return _get_active_price(route_id, db)


# ---------------------------------------------------------------------------
# Ingredient supplier refs (sub-resource of supply route)
# ---------------------------------------------------------------------------

@router.get("/{route_id}/refs", response_model=List[IngredientSupplierRefResponse])
def list_refs(
    route_id: int,
    db: Session = Depends(get_db),
) -> List[IngredientSupplierRefResponse]:
    """List external supplier references for a route (names, SKUs, purchase units)."""
    _get_or_404(route_id, db)
    refs = (
        db.query(IngredientSupplierRef, Ingredient.name.label("ingredient_name"))
        .join(Ingredient, IngredientSupplierRef.ingredient_id == Ingredient.id)
        .filter(IngredientSupplierRef.supply_route_id == route_id)
        .all()
    )
    results = []
    for ref, ingredient_name in refs:
        data = {
            "id": ref.id,
            "ingredient_id": ref.ingredient_id,
            "ingredient_name": ingredient_name,
            "supply_route_id": ref.supply_route_id,
            "external_name": ref.external_name,
            "external_code": ref.external_code,
            "purchase_unit": ref.purchase_unit,
            "units_per_pack": ref.units_per_pack,
            "notes": ref.notes,
            "is_active": ref.is_active,
            "created_at": ref.created_at,
        }
        results.append(IngredientSupplierRefResponse.model_validate(data))
    return results


@router.post(
    "/{route_id}/refs",
    response_model=IngredientSupplierRefResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_ref(
    route_id: int,
    body: IngredientSupplierRefCreate,
    db: Session = Depends(get_db),
) -> IngredientSupplierRefResponse:
    """Add an external supplier reference to a route."""
    _get_or_404(route_id, db)
    ingredient = db.get(Ingredient, body.ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    ref = IngredientSupplierRef(**body.model_dump())
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return IngredientSupplierRefResponse.model_validate({
        "id": ref.id,
        "ingredient_id": ref.ingredient_id,
        "ingredient_name": ingredient.name,
        "supply_route_id": ref.supply_route_id,
        "external_name": ref.external_name,
        "external_code": ref.external_code,
        "purchase_unit": ref.purchase_unit,
        "units_per_pack": ref.units_per_pack,
        "notes": ref.notes,
        "is_active": ref.is_active,
        "created_at": ref.created_at,
    })


@router.put("/refs/{ref_id}", response_model=IngredientSupplierRefResponse)
def update_ref(
    ref_id: int,
    body: IngredientSupplierRefUpdate,
    db: Session = Depends(get_db),
) -> IngredientSupplierRefResponse:
    ref = db.get(IngredientSupplierRef, ref_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="Supplier ref not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(ref, field, value)
    db.commit()
    db.refresh(ref)
    ingredient = db.get(Ingredient, ref.ingredient_id)
    return IngredientSupplierRefResponse.model_validate({
        "id": ref.id,
        "ingredient_id": ref.ingredient_id,
        "ingredient_name": ingredient.name if ingredient else None,
        "supply_route_id": ref.supply_route_id,
        "external_name": ref.external_name,
        "external_code": ref.external_code,
        "purchase_unit": ref.purchase_unit,
        "units_per_pack": ref.units_per_pack,
        "notes": ref.notes,
        "is_active": ref.is_active,
        "created_at": ref.created_at,
    })
