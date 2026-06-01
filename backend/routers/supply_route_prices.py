from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.supply_chain import SupplyRoute, SupplyRoutePrice
from backend.schemas.supply_chain import SupplyRoutePriceCreate, SupplyRoutePriceResponse

router = APIRouter(prefix="/api/supply-route-prices", tags=["supply-chain"])


def _get_route_or_404(route_id: int, db: Session) -> SupplyRoute:
    route = db.get(SupplyRoute, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Supply route not found")
    return route


@router.get("/route/{route_id}/active", response_model=Optional[SupplyRoutePriceResponse])
def get_active_price(
    route_id: int, db: Session = Depends(get_db)
) -> Optional[SupplyRoutePriceResponse]:
    """Return the currently active price for a route (valid_until IS NULL)."""
    _get_route_or_404(route_id, db)
    return (
        db.query(SupplyRoutePrice)
        .filter(
            SupplyRoutePrice.supply_route_id == route_id,
            SupplyRoutePrice.valid_until.is_(None),
        )
        .first()
    )


@router.get("/route/{route_id}/history", response_model=List[SupplyRoutePriceResponse])
def get_price_history(
    route_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> List[SupplyRoutePriceResponse]:
    """Return the full price history for a route, most recent first."""
    _get_route_or_404(route_id, db)
    return (
        db.query(SupplyRoutePrice)
        .filter(SupplyRoutePrice.supply_route_id == route_id)
        .order_by(SupplyRoutePrice.valid_from.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.post("", response_model=SupplyRoutePriceResponse, status_code=status.HTTP_201_CREATED)
def create_price(
    body: SupplyRoutePriceCreate, db: Session = Depends(get_db)
) -> SupplyRoutePriceResponse:
    """Set a new price for a supply route.

    Automatically closes the currently active price (valid_until = today)
    before inserting the new one. This follows the append-only principle:
    never UPDATE price data, always close + insert.
    """
    _get_route_or_404(body.supply_route_id, db)

    if body.qargo_price > body.list_price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="qargo_price cannot be greater than list_price",
        )

    # Close the currently active price
    active = (
        db.query(SupplyRoutePrice)
        .filter(
            SupplyRoutePrice.supply_route_id == body.supply_route_id,
            SupplyRoutePrice.valid_until.is_(None),
        )
        .first()
    )
    if active:
        active.valid_until = date.today()

    new_price = SupplyRoutePrice(**body.model_dump())
    db.add(new_price)
    db.commit()
    db.refresh(new_price)
    return new_price
