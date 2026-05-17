from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# ---------------------------------------------------------------------------
# ProductSize
# ---------------------------------------------------------------------------

class ProductSizeBase(BaseModel):
    size_name: Optional[str] = None
    volume_oz: Optional[Decimal] = None
    scale_factor: Decimal = Decimal("1.0")
    is_default: bool = False

    @field_validator("volume_oz")
    @classmethod
    def volume_positive(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("volume_oz must be > 0")
        return v

    @field_validator("scale_factor")
    @classmethod
    def scale_factor_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("scale_factor must be > 0")
        return v


class ProductSizeCreate(ProductSizeBase):
    product_id: int


class ProductSizeResponse(ProductSizeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int


# ---------------------------------------------------------------------------
# SizePackaging
# ---------------------------------------------------------------------------

class SizePackagingBase(BaseModel):
    packaging_ingredient_id: int
    quantity: Decimal = Decimal("1")

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("quantity must be > 0")
        return v


class SizePackagingCreate(SizePackagingBase):
    pass


class SizePackagingResponse(SizePackagingBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    size_id: int
    packaging_name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def extract_packaging_name(cls, data: Any) -> Any:
        """Pull packaging ingredient name from loaded ORM relationship when available."""
        if not hasattr(data, "__dict__"):
            return data

        if not getattr(data, "packaging_name", None):
            ingredient = getattr(data, "packaging_ingredient", None)
            if ingredient is not None:
                data.__dict__["packaging_name"] = ingredient.name

        return data
