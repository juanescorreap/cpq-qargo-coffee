from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class IngredientBase(BaseModel):
    name: str
    category: Optional[str] = None
    purchase_unit: Optional[str] = None
    purchase_price: Optional[Decimal] = None   # NULL when price is unknown
    usage_unit: Optional[str] = None
    conversion_factor: Optional[Decimal] = None
    # Stored as fraction 0.0–1.0 (e.g. 0.98 = 98 % yield)
    yield_percentage: Optional[Decimal] = None
    source_url: Optional[str] = None

    @field_validator("purchase_price")
    @classmethod
    def purchase_price_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("purchase_price must be >= 0")
        return v

    @field_validator("conversion_factor")
    @classmethod
    def conversion_factor_positive(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("conversion_factor must be > 0")
        return v

    @field_validator("yield_percentage")
    @classmethod
    def yield_percentage_range(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and (v < 0 or v > 1):
            raise ValueError("yield_percentage must be between 0.0 and 1.0")
        return v


class IngredientCreate(IngredientBase):
    name: str  # required on create


class IngredientUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    purchase_unit: Optional[str] = None
    purchase_price: Optional[Decimal] = None
    usage_unit: Optional[str] = None
    conversion_factor: Optional[Decimal] = None
    yield_percentage: Optional[Decimal] = None
    source_url: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("purchase_price")
    @classmethod
    def purchase_price_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("purchase_price must be >= 0")
        return v

    @field_validator("conversion_factor")
    @classmethod
    def conversion_factor_positive(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("conversion_factor must be > 0")
        return v

    @field_validator("yield_percentage")
    @classmethod
    def yield_percentage_range(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and (v < 0 or v > 1):
            raise ValueError("yield_percentage must be between 0.0 and 1.0")
        return v


class IngredientResponse(IngredientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    last_scraped: Optional[datetime] = None
