"""Schemas for the cost calculation engine.

Typical flow:
    Client  →  CostCalculationRequest  →  costing engine
    Engine  →  CostBreakdownResponse   →  client

CostBreakdownResponse breaks down the total cost into four independent
categories (ingredients, sub_recipes, packaging, labor) so that the client
can show cost transparency to the end user and facilitate margin analysis
by category.
"""

from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class CostCalculationRequest(BaseModel):
    """Input parameters for calculating the cost of a product.

    If size_id is None the engine uses the product's base size
    (scale_factor = 1.0). If store_id is None no store-level cost
    adjustments are applied.
    """

    product_id: int
    size_id: Optional[int] = None
    store_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Detail line items
# ---------------------------------------------------------------------------

class IngredientCostDetail(BaseModel):
    """Cost breakdown per ingredient line in the recipe.

    unit_cost already includes the ingredient waste (yield_percentage) and
    the process waste (process_yield_loss): it is the actual cost per
    usage_unit after applying both factors.

    total_cost = quantity × unit_cost
    """

    name: str
    quantity: Decimal
    unit: str
    unit_cost: Decimal
    total_cost: Decimal


class SubRecipeCostDetail(BaseModel):
    """Cost breakdown per sub-recipe (batch component) referenced.

    unit_cost is the full cost of one portion of the sub-recipe,
    calculated recursively by the engine expanding its ingredients.

    total_cost = quantity × unit_cost
    """

    name: str
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal


class PackagingCostDetail(BaseModel):
    """Cost breakdown per packaging item associated with the size.

    Packaging is costed at the ingredient price divided by its
    conversion factor (units per box). total_cost = quantity
    × unit cost of the packaging.
    """

    name: str
    quantity: Decimal
    total_cost: Decimal


class LaborCostDetail(BaseModel):
    """Labor cost breakdown.

    total_cost = prep_time_minutes × cost_per_minute.
    cost_per_minute comes from the product's labor_cost_per_minute field.
    """

    prep_time_minutes: Decimal
    cost_per_minute: Decimal
    total_cost: Decimal

    @field_validator("prep_time_minutes", "cost_per_minute")
    @classmethod
    def non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Labor cost fields must be >= 0")
        return v


# ---------------------------------------------------------------------------
# Full breakdown response
# ---------------------------------------------------------------------------

class CostBreakdownResponse(BaseModel):
    """Complete response from the costing engine for a product and size.

    totals groups the subtotals by category for direct access:
        {
            "ingredients": Decimal,
            "sub_recipes":  Decimal,
            "packaging":    Decimal,
            "labor":        Decimal,
            "total":        Decimal,   # sum of the four categories
        }

    total_cost == totals["total"] and is kept as a top-level field
    for easy access without parsing the dict.
    """

    product_id: int
    product_name: str
    size_id: Optional[int] = None
    size_name: Optional[str] = None
    store_id: Optional[int] = None
    store_name: Optional[str] = None

    ingredients: List[IngredientCostDetail]
    sub_recipes: List[SubRecipeCostDetail]
    packaging: List[PackagingCostDetail]
    labor: Optional[LaborCostDetail] = None

    totals: Dict[str, Decimal]
    total_cost: Decimal
