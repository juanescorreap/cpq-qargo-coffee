from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from backend.database import get_db
from backend.models.supply_chain import Region
from backend.schemas.supply_chain import RegionCreate, RegionResponse, RegionUpdate

router = APIRouter(prefix="/api/regions", tags=["supply-chain"])


def _get_or_404(region_id: int, db: Session) -> Region:
    region = db.get(Region, region_id)
    if region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found")
    return region


@router.get("", response_model=List[RegionResponse])
def list_regions(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[RegionResponse]:
    """Return all regions, optionally filtered by active status."""
    q = db.query(Region)
    if is_active is not None:
        q = q.filter(Region.is_active == is_active)
    return q.order_by(Region.code).offset(skip).limit(limit).all()


@router.get("/{region_id}", response_model=RegionResponse)
def get_region(region_id: int, db: Session = Depends(get_db)) -> RegionResponse:
    return _get_or_404(region_id, db)


@router.post("", response_model=RegionResponse, status_code=status.HTTP_201_CREATED)
def create_region(body: RegionCreate, db: Session = Depends(get_db)) -> RegionResponse:
    """Create a new region. code is automatically uppercased and must be unique."""
    existing = db.query(Region).filter(func.upper(Region.code) == body.code.upper()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Region with code '{body.code}' already exists",
        )
    region = Region(**body.model_dump())
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


@router.put("/{region_id}", response_model=RegionResponse)
def update_region(
    region_id: int, body: RegionUpdate, db: Session = Depends(get_db)
) -> RegionResponse:
    """Update region fields. Only provided fields are changed."""
    region = _get_or_404(region_id, db)
    updates = body.model_dump(exclude_unset=True)
    if "code" in updates:
        conflict = (
            db.query(Region)
            .filter(func.upper(Region.code) == updates["code"].upper(), Region.id != region_id)
            .first()
        )
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Region with code '{updates['code']}' already exists",
            )
    for field, value in updates.items():
        setattr(region, field, value)
    db.commit()
    db.refresh(region)
    return region


@router.delete("/{region_id}", status_code=status.HTTP_200_OK)
def deactivate_region(region_id: int, db: Session = Depends(get_db)) -> dict:
    """Soft-delete a region (mark as inactive)."""
    region = _get_or_404(region_id, db)
    region.is_active = False
    db.commit()
    return {"message": "Region deactivated"}
