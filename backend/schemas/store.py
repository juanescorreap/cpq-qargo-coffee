from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class StoreBase(BaseModel):
    code: str
    name: str
    city: str

    @field_validator("code")
    @classmethod
    def code_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("code must not be blank")
        return v.strip().upper()

    @field_validator("name", "city")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must not be blank")
        return v.strip()


class StoreCreate(StoreBase):
    pass


class StoreUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    city: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("code")
    @classmethod
    def code_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("code must not be blank")
        return v.strip().upper() if v else v

    @field_validator("name", "city")
    @classmethod
    def not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("field must not be blank")
        return v.strip() if v else v


class StoreResponse(StoreBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


# ---------------------------------------------------------------------------
# StoreIngredientPrice
# ---------------------------------------------------------------------------

class StoreIngredientPriceBase(BaseModel):
    ingredient_id: int
    local_price: Decimal
    local_supplier: Optional[str] = None

    @field_validator("local_price")
    @classmethod
    def price_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("local_price must be >= 0")
        return v


class StoreIngredientPriceCreate(StoreIngredientPriceBase):
    pass


class StoreIngredientPriceResponse(StoreIngredientPriceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    ingredient_name: Optional[str] = None
    updated_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def extract_ingredient_name(cls, data: Any) -> Any:
        """Pull ingredient name from loaded ORM relationship when available."""
        if not hasattr(data, "__dict__"):
            return data
        if not getattr(data, "ingredient_name", None):
            ingredient = getattr(data, "ingredient", None)
            if ingredient is not None:
                data.__dict__["ingredient_name"] = ingredient.name
        return data
