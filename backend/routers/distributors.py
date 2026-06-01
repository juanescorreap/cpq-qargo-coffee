from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.supply_chain import Distributor
from backend.schemas.supply_chain import (
    DistributorCreate,
    DistributorResponse,
    DistributorUpdate,
)

router = APIRouter(prefix="/api/distributors", tags=["supply-chain"])


def _get_or_404(distributor_id: int, db: Session) -> Distributor:
    distributor = db.get(Distributor, distributor_id)
    if distributor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Distributor not found")
    return distributor


@router.get("", response_model=List[DistributorResponse])
def list_distributors(
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[DistributorResponse]:
    """Return all distributors, optionally filtered by active status."""
    q = db.query(Distributor)
    if is_active is not None:
        q = q.filter(Distributor.is_active == is_active)
    return q.order_by(Distributor.name).offset(skip).limit(limit).all()


@router.get("/{distributor_id}", response_model=DistributorResponse)
def get_distributor(distributor_id: int, db: Session = Depends(get_db)) -> DistributorResponse:
    return _get_or_404(distributor_id, db)


@router.post("", response_model=DistributorResponse, status_code=status.HTTP_201_CREATED)
def create_distributor(
    body: DistributorCreate, db: Session = Depends(get_db)
) -> DistributorResponse:
    distributor = Distributor(**body.model_dump())
    db.add(distributor)
    db.commit()
    db.refresh(distributor)
    return distributor


@router.put("/{distributor_id}", response_model=DistributorResponse)
def update_distributor(
    distributor_id: int, body: DistributorUpdate, db: Session = Depends(get_db)
) -> DistributorResponse:
    distributor = _get_or_404(distributor_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(distributor, field, value)
    db.commit()
    db.refresh(distributor)
    return distributor


@router.delete("/{distributor_id}", status_code=status.HTTP_200_OK)
def deactivate_distributor(distributor_id: int, db: Session = Depends(get_db)) -> dict:
    distributor = _get_or_404(distributor_id, db)
    distributor.is_active = False
    db.commit()
    return {"message": "Distributor deactivated"}
