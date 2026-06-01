from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.supply_chain import Region, SupplyRoute, SupplyRouteAssignment
from backend.models.store import Store
from backend.schemas.supply_chain import (
    SupplyRouteAssignmentClose,
    SupplyRouteAssignmentCreate,
    SupplyRouteAssignmentResponse,
)

router = APIRouter(prefix="/api/supply-route-assignments", tags=["supply-chain"])


def _get_or_404(assignment_id: int, db: Session) -> SupplyRouteAssignment:
    obj = db.get(SupplyRouteAssignment, assignment_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return obj


def _build_response(obj: SupplyRouteAssignment, db: Session) -> dict:
    region = db.get(Region, obj.region_id) if obj.region_id else None
    store = db.get(Store, obj.store_id) if obj.store_id else None
    return {
        "id": obj.id,
        "supply_route_id": obj.supply_route_id,
        "region_id": obj.region_id,
        "region_name": region.name if region else None,
        "store_id": obj.store_id,
        "store_name": store.name if store else None,
        "priority": obj.priority,
        "valid_from": obj.valid_from,
        "valid_until": obj.valid_until,
        "change_reason": obj.change_reason,
        "assigned_by": obj.assigned_by,
        "notes": obj.notes,
        "created_at": obj.created_at,
    }


@router.get("", response_model=List[SupplyRouteAssignmentResponse])
def list_assignments(
    region_id: Optional[int] = Query(None),
    store_id: Optional[int] = Query(None),
    supply_route_id: Optional[int] = Query(None),
    active_only: bool = Query(True, description="Return only assignments where valid_until IS NULL"),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> List[SupplyRouteAssignmentResponse]:
    """List route assignments. By default returns only currently active ones."""
    q = db.query(SupplyRouteAssignment)
    if region_id is not None:
        q = q.filter(SupplyRouteAssignment.region_id == region_id)
    if store_id is not None:
        q = q.filter(SupplyRouteAssignment.store_id == store_id)
    if supply_route_id is not None:
        q = q.filter(SupplyRouteAssignment.supply_route_id == supply_route_id)
    if active_only:
        q = q.filter(SupplyRouteAssignment.valid_until.is_(None))
    assignments = q.order_by(
        SupplyRouteAssignment.region_id,
        SupplyRouteAssignment.store_id,
        SupplyRouteAssignment.priority,
    ).offset(skip).limit(limit).all()
    return [
        SupplyRouteAssignmentResponse.model_validate(_build_response(a, db))
        for a in assignments
    ]


@router.get("/{assignment_id}", response_model=SupplyRouteAssignmentResponse)
def get_assignment(
    assignment_id: int, db: Session = Depends(get_db)
) -> SupplyRouteAssignmentResponse:
    obj = _get_or_404(assignment_id, db)
    return SupplyRouteAssignmentResponse.model_validate(_build_response(obj, db))


@router.post("", response_model=SupplyRouteAssignmentResponse, status_code=status.HTTP_201_CREATED)
def create_assignment(
    body: SupplyRouteAssignmentCreate, db: Session = Depends(get_db)
) -> SupplyRouteAssignmentResponse:
    """Assign a supply route to a region or store.

    Business rule: close any existing assignment with the same scope and priority
    before inserting the new one (prevents EXCLUDE constraint violations).
    The close sets valid_until = body.valid_from - 1 day.
    """
    if db.get(SupplyRoute, body.supply_route_id) is None:
        raise HTTPException(status_code=404, detail="Supply route not found")
    if body.region_id and db.get(Region, body.region_id) is None:
        raise HTTPException(status_code=404, detail="Region not found")
    if body.store_id and db.get(Store, body.store_id) is None:
        raise HTTPException(status_code=404, detail="Store not found")
    if not body.region_id and not body.store_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either region_id or store_id must be provided",
        )
    if body.region_id and body.store_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either region_id or store_id, not both",
        )

    # Close any active assignment for the same scope + priority
    q = (
        db.query(SupplyRouteAssignment)
        .filter(
            SupplyRouteAssignment.priority == body.priority,
            SupplyRouteAssignment.valid_until.is_(None),
        )
    )
    if body.region_id:
        q = q.filter(SupplyRouteAssignment.region_id == body.region_id)
    else:
        q = q.filter(SupplyRouteAssignment.store_id == body.store_id)

    existing = q.first()
    if existing:
        from datetime import timedelta
        existing.valid_until = body.valid_from - timedelta(days=1)
        existing.change_reason = body.change_reason

    obj = SupplyRouteAssignment(**body.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return SupplyRouteAssignmentResponse.model_validate(_build_response(obj, db))


@router.post("/{assignment_id}/close", response_model=SupplyRouteAssignmentResponse)
def close_assignment(
    assignment_id: int,
    body: SupplyRouteAssignmentClose,
    db: Session = Depends(get_db),
) -> SupplyRouteAssignmentResponse:
    """Close an active assignment by setting valid_until = today.

    Use this when a store or region stops using a route. The route history
    is preserved — this follows the append-only principle from CLAUDE.md P2.
    """
    obj = _get_or_404(assignment_id, db)
    if obj.valid_until is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assignment is already closed",
        )
    obj.valid_until = date.today()
    if body.change_reason:
        obj.change_reason = body.change_reason
    db.commit()
    db.refresh(obj)
    return SupplyRouteAssignmentResponse.model_validate(_build_response(obj, db))
