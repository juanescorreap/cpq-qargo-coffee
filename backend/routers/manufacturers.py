from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.supply_chain import Manufacturer
from backend.schemas.supply_chain import (
    ManufacturerCreate,
    ManufacturerResponse,
    ManufacturerUpdate,
)

router = APIRouter(prefix="/api/manufacturers", tags=["supply-chain"])


def _get_or_404(manufacturer_id: int, db: Session) -> Manufacturer:
    manufacturer = db.get(Manufacturer, manufacturer_id)
    if manufacturer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manufacturer not found")
    return manufacturer


@router.get("", response_model=List[ManufacturerResponse])
def list_manufacturers(
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[ManufacturerResponse]:
    """Return all manufacturers, optionally filtered by active status."""
    q = db.query(Manufacturer)
    if is_active is not None:
        q = q.filter(Manufacturer.is_active == is_active)
    return q.order_by(Manufacturer.name).offset(skip).limit(limit).all()


@router.get("/{manufacturer_id}", response_model=ManufacturerResponse)
def get_manufacturer(manufacturer_id: int, db: Session = Depends(get_db)) -> ManufacturerResponse:
    return _get_or_404(manufacturer_id, db)


@router.post("", response_model=ManufacturerResponse, status_code=status.HTTP_201_CREATED)
def create_manufacturer(
    body: ManufacturerCreate, db: Session = Depends(get_db)
) -> ManufacturerResponse:
    manufacturer = Manufacturer(**body.model_dump())
    db.add(manufacturer)
    db.commit()
    db.refresh(manufacturer)
    return manufacturer


@router.put("/{manufacturer_id}", response_model=ManufacturerResponse)
def update_manufacturer(
    manufacturer_id: int, body: ManufacturerUpdate, db: Session = Depends(get_db)
) -> ManufacturerResponse:
    manufacturer = _get_or_404(manufacturer_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(manufacturer, field, value)
    db.commit()
    db.refresh(manufacturer)
    return manufacturer


@router.delete("/{manufacturer_id}", status_code=status.HTTP_200_OK)
def deactivate_manufacturer(manufacturer_id: int, db: Session = Depends(get_db)) -> dict:
    manufacturer = _get_or_404(manufacturer_id, db)
    manufacturer.is_active = False
    db.commit()
    return {"message": "Manufacturer deactivated"}
