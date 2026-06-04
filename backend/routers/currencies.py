from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.currency import Currency
from backend.schemas.currency import CurrencyResponse

router = APIRouter(prefix="/api/currencies", tags=["currencies"])


@router.get("", response_model=List[CurrencyResponse])
def list_currencies(
    is_active: Optional[bool] = Query(True, description="Filter by active status"),
    db: Session = Depends(get_db),
) -> List[CurrencyResponse]:
    """Return ISO 4217 currencies. Defaults to active only (COP, USD, EUR)."""
    q = db.query(Currency)
    if is_active is not None:
        q = q.filter(Currency.is_active == is_active)
    return q.order_by(Currency.code).all()


@router.get("/{code}", response_model=CurrencyResponse)
def get_currency(code: str, db: Session = Depends(get_db)) -> CurrencyResponse:
    currency = db.get(Currency, code.strip().upper())
    if currency is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Currency not found"
        )
    return currency
