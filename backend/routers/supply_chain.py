"""Utility endpoints for the supply chain model.

Currently provides fn_resolve_supply_route as a REST endpoint,
used by the store detail UI (Fase C) to display active routes per ingredient
and by the cost calculator frontend to explain price sources.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.supply_chain import Distributor, Manufacturer, SupplyRoutePrice
from backend.models.store import Store
from backend.schemas.supply_chain import ResolvedRouteResponse, SupplyRoutePriceResponse

router = APIRouter(prefix="/api/supply-chain", tags=["supply-chain"])


def _resolve_route(ingredient_id: int, store_id: int, db: Session) -> Optional[dict]:
    """Call fn_resolve_supply_route and return the row as a dict, or None."""
    row = db.execute(
        text(
            "SELECT assignment_id, supply_route_id, scope, priority, "
            "manufacturer_id, distributor_id, is_direct "
            "FROM public.fn_resolve_supply_route(:iid, :sid)"
        ),
        {"iid": ingredient_id, "sid": store_id},
    ).fetchone()
    return row._asdict() if row else None


@router.get("/resolve-route", response_model=ResolvedRouteResponse)
def resolve_route(
    ingredient_id: int = Query(..., description="Canonical ingredient ID"),
    store_id: int = Query(..., description="Store ID"),
    db: Session = Depends(get_db),
) -> ResolvedRouteResponse:
    """Resolve which supply route a store uses for a given ingredient today.

    Calls fn_resolve_supply_route (the single source of truth defined in Fase 6
    migration). Returns route metadata and the currently active price.
    Returns resolved=false if no route is configured for this combination.
    """
    if db.get(Ingredient, ingredient_id) is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    if db.get(Store, store_id) is None:
        raise HTTPException(status_code=404, detail="Store not found")

    row = _resolve_route(ingredient_id, store_id, db)

    if not row:
        return ResolvedRouteResponse(
            ingredient_id=ingredient_id,
            store_id=store_id,
            resolved=False,
        )

    manufacturer = db.get(Manufacturer, row["manufacturer_id"]) if row["manufacturer_id"] else None
    distributor = db.get(Distributor, row["distributor_id"]) if row["distributor_id"] else None

    active_price = (
        db.query(SupplyRoutePrice)
        .filter(
            SupplyRoutePrice.supply_route_id == row["supply_route_id"],
            SupplyRoutePrice.valid_until.is_(None),
        )
        .first()
    )

    return ResolvedRouteResponse(
        ingredient_id=ingredient_id,
        store_id=store_id,
        resolved=True,
        assignment_id=row["assignment_id"],
        supply_route_id=row["supply_route_id"],
        scope=row["scope"],
        priority=row["priority"],
        manufacturer_id=row["manufacturer_id"],
        manufacturer_name=manufacturer.name if manufacturer else None,
        distributor_id=row["distributor_id"],
        distributor_name=distributor.name if distributor else None,
        is_direct=row["is_direct"],
        active_price=SupplyRoutePriceResponse.model_validate(active_price) if active_price else None,
    )


@router.get("/resolve-routes-bulk", response_model=List[ResolvedRouteResponse])
def resolve_routes_bulk(
    store_id: int = Query(..., description="Store ID"),
    ingredient_ids: str = Query(..., description="Comma-separated ingredient IDs, e.g. 1,2,3"),
    db: Session = Depends(get_db),
) -> List[ResolvedRouteResponse]:
    """Resolve supply routes for multiple ingredients of a store in one call.

    Used by the store detail 'Active Routes' tab (Fase C) to populate the
    full ingredient-route table without N+1 requests.
    """
    if db.get(Store, store_id) is None:
        raise HTTPException(status_code=404, detail="Store not found")

    try:
        ids = [int(i.strip()) for i in ingredient_ids.split(",") if i.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="ingredient_ids must be comma-separated integers")

    if not ids:
        return []

    results = []
    for ingredient_id in ids:
        row = _resolve_route(ingredient_id, store_id, db)
        if not row:
            results.append(ResolvedRouteResponse(
                ingredient_id=ingredient_id,
                store_id=store_id,
                resolved=False,
            ))
            continue

        manufacturer = db.get(Manufacturer, row["manufacturer_id"]) if row["manufacturer_id"] else None
        distributor = db.get(Distributor, row["distributor_id"]) if row["distributor_id"] else None
        active_price = (
            db.query(SupplyRoutePrice)
            .filter(
                SupplyRoutePrice.supply_route_id == row["supply_route_id"],
                SupplyRoutePrice.valid_until.is_(None),
            )
            .first()
        )
        results.append(ResolvedRouteResponse(
            ingredient_id=ingredient_id,
            store_id=store_id,
            resolved=True,
            assignment_id=row["assignment_id"],
            supply_route_id=row["supply_route_id"],
            scope=row["scope"],
            priority=row["priority"],
            manufacturer_id=row["manufacturer_id"],
            manufacturer_name=manufacturer.name if manufacturer else None,
            distributor_id=row["distributor_id"],
            distributor_name=distributor.name if distributor else None,
            is_direct=row["is_direct"],
            active_price=SupplyRoutePriceResponse.model_validate(active_price) if active_price else None,
        ))

    return results
