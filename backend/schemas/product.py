from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


class ProductBase(BaseModel):
    name: str
    category: Optional[str] = None
    base_size_oz: Optional[Decimal] = None
    prep_time_minutes: Optional[Decimal] = None
    labor_cost_per_minute: Decimal = Decimal("0")
    is_sub_recipe: bool = False

    @field_validator("base_size_oz")
    @classmethod
    def base_size_positive(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("base_size_oz must be > 0")
        return v

    @field_validator("prep_time_minutes")
    @classmethod
    def prep_time_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("prep_time_minutes must be >= 0")
        return v

    @field_validator("labor_cost_per_minute")
    @classmethod
    def labor_cost_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("labor_cost_per_minute must be >= 0")
        return v


class ProductCreate(ProductBase):
    name: str  # explicitly required on create


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    base_size_oz: Optional[Decimal] = None
    prep_time_minutes: Optional[Decimal] = None
    labor_cost_per_minute: Optional[Decimal] = None
    is_sub_recipe: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("base_size_oz")
    @classmethod
    def base_size_positive(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("base_size_oz must be > 0")
        return v

    @field_validator("prep_time_minutes")
    @classmethod
    def prep_time_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("prep_time_minutes must be >= 0")
        return v

    @field_validator("labor_cost_per_minute")
    @classmethod
    def labor_cost_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("labor_cost_per_minute must be >= 0")
        return v


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
