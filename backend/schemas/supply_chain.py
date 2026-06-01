"""Pydantic schemas for the supply chain model.

Covers: regions, manufacturers, distributors, supply routes,
route assignments, route prices, and ingredient supplier refs.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------

class RegionBase(BaseModel):
    name: str
    code: str
    country_code: str = "CO"

    @field_validator("code")
    @classmethod
    def code_upper(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("code must not be blank")
        return v.strip().upper()

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()

    @field_validator("country_code")
    @classmethod
    def country_code_upper(cls, v: str) -> str:
        return v.strip().upper()


class RegionCreate(RegionBase):
    pass


class RegionUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    country_code: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("code")
    @classmethod
    def code_upper(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().upper() if v else v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("name must not be blank")
        return v.strip() if v else v


class RegionResponse(RegionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Manufacturer
# ---------------------------------------------------------------------------

class ManufacturerBase(BaseModel):
    name: str
    country_code: str = "CO"
    tax_id: Optional[str] = None
    website: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()


class ManufacturerCreate(ManufacturerBase):
    pass


class ManufacturerUpdate(BaseModel):
    name: Optional[str] = None
    country_code: Optional[str] = None
    tax_id: Optional[str] = None
    website: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("name must not be blank")
        return v.strip() if v else v


class ManufacturerResponse(ManufacturerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Distributor
# ---------------------------------------------------------------------------

class DistributorBase(BaseModel):
    name: str
    country_code: str = "CO"
    tax_id: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()


class DistributorCreate(DistributorBase):
    pass


class DistributorUpdate(BaseModel):
    name: Optional[str] = None
    country_code: Optional[str] = None
    tax_id: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("name must not be blank")
        return v.strip() if v else v


class DistributorResponse(DistributorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# SupplyRoute
# ---------------------------------------------------------------------------

class SupplyRouteCreate(BaseModel):
    ingredient_id: int
    manufacturer_id: Optional[int] = None
    distributor_id: Optional[int] = None
    is_direct: bool = False

    @field_validator("is_direct", mode="before")
    @classmethod
    def validate_source(cls, v: bool) -> bool:
        return v


class SupplyRouteUpdate(BaseModel):
    manufacturer_id: Optional[int] = None
    distributor_id: Optional[int] = None
    is_direct: Optional[bool] = None
    is_active: Optional[bool] = None


class SupplyRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient_id: int
    ingredient_name: Optional[str] = None
    manufacturer_id: Optional[int] = None
    manufacturer_name: Optional[str] = None
    distributor_id: Optional[int] = None
    distributor_name: Optional[str] = None
    is_direct: bool
    is_active: bool
    created_at: datetime


class SupplyRouteWithPrice(SupplyRouteResponse):
    """Supply route response extended with its current active price."""
    active_price: Optional["SupplyRoutePriceResponse"] = None


# ---------------------------------------------------------------------------
# SupplyRouteAssignment
# ---------------------------------------------------------------------------

class SupplyRouteAssignmentCreate(BaseModel):
    supply_route_id: int
    region_id: Optional[int] = None
    store_id: Optional[int] = None
    priority: int = 1
    valid_from: date
    assigned_by: str
    change_reason: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("assigned_by")
    @classmethod
    def assigned_by_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("assigned_by must not be blank")
        return v.strip()

    @field_validator("priority")
    @classmethod
    def priority_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("priority must be >= 1")
        return v


class SupplyRouteAssignmentClose(BaseModel):
    change_reason: Optional[str] = None


class SupplyRouteAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supply_route_id: int
    region_id: Optional[int] = None
    region_name: Optional[str] = None
    store_id: Optional[int] = None
    store_name: Optional[str] = None
    priority: int
    valid_from: date
    valid_until: Optional[date] = None
    change_reason: Optional[str] = None
    assigned_by: str
    notes: Optional[str] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# SupplyRoutePrice
# ---------------------------------------------------------------------------

class SupplyRoutePriceCreate(BaseModel):
    supply_route_id: int
    list_price: Decimal
    qargo_price: Decimal
    currency_code: str = "COP"
    price_per_unit: str
    source: Optional[str] = None
    created_by: str

    @field_validator("list_price", "qargo_price")
    @classmethod
    def price_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("price must be > 0")
        return v

    @field_validator("currency_code")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("currency_code must be a 3-letter ISO 4217 code (e.g. COP, USD)")
        return v

    @field_validator("created_by")
    @classmethod
    def created_by_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("created_by must not be blank")
        return v.strip()

    @model_validator(mode="after")
    def qargo_not_greater_than_list(self) -> "SupplyRoutePriceCreate":
        if self.qargo_price > self.list_price:
            raise ValueError("qargo_price cannot be greater than list_price")
        return self


class SupplyRoutePriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supply_route_id: int
    list_price: Decimal
    qargo_price: Decimal
    currency_code: str
    price_per_unit: str
    valid_from: date
    valid_until: Optional[date] = None
    source: Optional[str] = None
    created_by: str
    created_at: datetime


# ---------------------------------------------------------------------------
# IngredientSupplierRef
# ---------------------------------------------------------------------------

class IngredientSupplierRefCreate(BaseModel):
    ingredient_id: int
    supply_route_id: int
    external_name: str
    external_code: Optional[str] = None
    purchase_unit: str
    units_per_pack: Optional[Decimal] = None
    notes: Optional[str] = None

    @field_validator("external_name", "purchase_unit")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must not be blank")
        return v.strip()


class IngredientSupplierRefUpdate(BaseModel):
    external_name: Optional[str] = None
    external_code: Optional[str] = None
    purchase_unit: Optional[str] = None
    units_per_pack: Optional[Decimal] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class IngredientSupplierRefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient_id: int
    ingredient_name: Optional[str] = None
    supply_route_id: int
    external_name: str
    external_code: Optional[str] = None
    purchase_unit: str
    units_per_pack: Optional[Decimal] = None
    notes: Optional[str] = None
    is_active: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# ResolvedRoute  (utility response for fn_resolve_supply_route)
# ---------------------------------------------------------------------------

class ResolvedRouteResponse(BaseModel):
    """Result of calling fn_resolve_supply_route for an ingredient + store."""

    ingredient_id: int
    store_id: int
    resolved: bool
    assignment_id: Optional[int] = None
    supply_route_id: Optional[int] = None
    scope: Optional[str] = None        # "store_override" | "region_default"
    priority: Optional[int] = None
    manufacturer_id: Optional[int] = None
    manufacturer_name: Optional[str] = None
    distributor_id: Optional[int] = None
    distributor_name: Optional[str] = None
    is_direct: Optional[bool] = None
    active_price: Optional[SupplyRoutePriceResponse] = None


# Rebuild forward ref
SupplyRouteWithPrice.model_rebuild()
