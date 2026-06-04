from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# ---------------------------------------------------------------------------
# Competitor
# ---------------------------------------------------------------------------

class CompetitorBase(BaseModel):
    name: str
    website_url: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()


class CompetitorCreate(CompetitorBase):
    pass


class CompetitorUpdate(BaseModel):
    name: Optional[str] = None
    website_url: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("name must not be blank")
        return v.strip() if v else v


class CompetitorResponse(CompetitorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


# ---------------------------------------------------------------------------
# CompetitorProduct
# ---------------------------------------------------------------------------

class CompetitorProductBase(BaseModel):
    product_name: str
    category: Optional[str] = None
    size_description: str
    price: Decimal
    source_url: Optional[str] = None

    @field_validator("product_name", "size_description")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must not be blank")
        return v.strip()

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("price must be >= 0")
        return v


class CompetitorProductCreate(CompetitorProductBase):
    competitor_id: int


class CompetitorProductResponse(BaseModel):
    """Catalog product enriched with its latest observed price (V2 split).

    ``price``, ``source_url`` and ``scraped_at`` are derived from the most
    recent ``competitor_price_observations`` row; they are None when the
    catalog product has no observations yet.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    competitor_id: int
    competitor_name: Optional[str] = None
    product_name: str
    category: Optional[str] = None
    size_description: Optional[str] = None
    price: Optional[Decimal] = None
    source_url: Optional[str] = None
    scraped_at: Optional[datetime] = None


class CompetitorPriceObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    competitor_product_id: int
    price: Optional[Decimal] = None
    currency_code: str
    source_url: Optional[str] = None
    scraped_at: datetime


# ---------------------------------------------------------------------------
# ProductCompetitorMatch
# ---------------------------------------------------------------------------

class ProductCompetitorMatchBase(BaseModel):
    our_product_id: int
    our_size_id: int
    competitor_product_id: int
    matched_by: str
    notes: Optional[str] = None

    @field_validator("matched_by")
    @classmethod
    def matched_by_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("matched_by must not be blank")
        return v.strip()


class ProductCompetitorMatchCreate(ProductCompetitorMatchBase):
    pass


class ProductCompetitorMatchResponse(ProductCompetitorMatchBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    matched_at: Optional[datetime] = None
    our_product_name: Optional[str] = None
    our_size_name: Optional[str] = None
    competitor_product_name: Optional[str] = None
    competitor_name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def extract_related_names(cls, data: Any) -> Any:
        """Pull display names from loaded ORM relationships when available."""
        if not hasattr(data, "__dict__"):
            return data
        d = data.__dict__

        if not d.get("our_product_name"):
            product = getattr(data, "our_product", None)
            if product is not None:
                d["our_product_name"] = product.name

        if not d.get("our_size_name"):
            size = getattr(data, "our_size", None)
            if size is not None:
                d["our_size_name"] = size.size_name

        if not d.get("competitor_product_name"):
            cp = getattr(data, "competitor_product", None)
            if cp is not None:
                d["competitor_product_name"] = cp.product_name
                if not d.get("competitor_name"):
                    competitor = getattr(cp, "competitor", None)
                    if competitor is not None:
                        d["competitor_name"] = competitor.name

        return data
