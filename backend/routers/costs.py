from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services import catalog_queries
from backend.schemas.cost import (
    CostBreakdownResponse,
    CostCalculationRequest,
    IngredientCostDetail,
    LaborCostDetail,
    PackagingCostDetail,
    SubRecipeCostDetail,
)
from backend.services.cost_calculator import CostCalculator

router = APIRouter(prefix="/api/costs", tags=["costs"])


class BatchCostRequest(BaseModel):
    product_ids: List[int]
    store_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _map_breakdown(raw: Dict) -> CostBreakdownResponse:
    """Adapt the dict returned by CostCalculator.get_cost_breakdown() to the response schema.

    get_cost_breakdown nests detail lists under a 'breakdown' key and uses
    different field names for labor ('minutes', 'cost') vs LaborCostDetail
    ('prep_time_minutes', 'total_cost'). This function flattens and renames.
    """
    bd = raw.get("breakdown", {})

    labor: Optional[LaborCostDetail] = None
    labor_raw = bd.get("labor")
    if labor_raw and (labor_raw.get("cost") or Decimal("0")) > 0:
        labor = LaborCostDetail(
            prep_time_minutes=labor_raw.get("minutes", Decimal("0")),
            cost_per_minute=labor_raw.get("cost_per_minute", Decimal("0")),
            total_cost=labor_raw.get("cost", Decimal("0")),
        )

    ingredients = [
        IngredientCostDetail(
            name=item["name"],
            quantity=item["quantity"],
            unit=item["unit"],
            unit_cost=item["unit_cost"],
            total_cost=item["line_cost"],
        )
        for item in bd.get("ingredients", [])
    ]

    sub_recipes = [
        SubRecipeCostDetail(
            name=item["name"],
            quantity=item["quantity"],
            unit_cost=item["unit_cost"],
            total_cost=item["line_cost"],
        )
        for item in bd.get("sub_recipes", [])
    ]

    packaging = [
        PackagingCostDetail(
            name=item["name"],
            quantity=item["quantity"],
            total_cost=item["line_cost"],
        )
        for item in bd.get("packaging", [])
    ]

    totals = raw.get("totals", {})
    totals["total"] = raw.get("total_cost", Decimal("0"))

    return CostBreakdownResponse(
        product_id=raw["product_id"],
        product_name=raw["product_name"],
        size_id=raw.get("size_id"),
        size_name=raw.get("size_name"),
        store_id=raw.get("store_id"),
        store_name=raw.get("store_name"),
        ingredients=ingredients,
        sub_recipes=sub_recipes,
        packaging=packaging,
        labor=labor,
        totals=totals,
        total_cost=raw["total_cost"],
    )


def _handle_calculator_error(exc: Exception) -> HTTPException:
    """Convert ValueError / RecursionError from the calculator to HTTP errors."""
    if isinstance(exc, RecursionError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=str(exc),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/calculate", response_model=Dict[str, Decimal])
def calculate_cost(
    body: CostCalculationRequest, db: Session = Depends(get_db)
) -> Dict[str, Decimal]:
    """Return the total production cost for a product + size + store combination."""
    calc = CostCalculator(db)
    try:
        cost = calc.calculate_product_cost(
            product_id=body.product_id,
            size_id=body.size_id,
            store_id=body.store_id,
        )
    except (ValueError, RecursionError) as exc:
        raise _handle_calculator_error(exc)
    return {"cost": cost}


@router.post("/breakdown", response_model=CostBreakdownResponse)
def cost_breakdown(
    body: CostCalculationRequest, db: Session = Depends(get_db)
) -> CostBreakdownResponse:
    """Return a full cost breakdown for a product, including ingredient, sub-recipe, packaging, and labor lines."""
    calc = CostCalculator(db)
    try:
        raw = calc.get_cost_breakdown(
            product_id=body.product_id,
            size_id=body.size_id,
            store_id=body.store_id,
        )
    except (ValueError, RecursionError) as exc:
        raise _handle_calculator_error(exc)
    return _map_breakdown(raw)


@router.post("/calculate-all", response_model=List[Dict])
def calculate_all(
    body: BatchCostRequest, db: Session = Depends(get_db)
) -> List[Dict]:
    """Batch-calculate costs for a list of products across all their sizes.

    Returns one entry per product with a nested list of size/cost pairs.
    Products or sizes that fail calculation (missing prices, no recipe) are
    skipped rather than aborting the entire batch.
    """
    calc = CostCalculator(db)
    results = []

    products = catalog_queries.active_products_by_ids(db, body.product_ids)

    for product in products:
        sizes = catalog_queries.product_sizes(db, product.id)

        size_costs = []

        if sizes:
            for size in sizes:
                try:
                    cost = calc.calculate_product_cost(
                        product_id=product.id,
                        size_id=size.id,
                        store_id=body.store_id,
                    )
                    size_costs.append({
                        "size_id": size.id,
                        "size_name": size.size_name,
                        "cost": cost,
                    })
                except (ValueError, RecursionError):
                    continue
        else:
            try:
                cost = calc.calculate_product_cost(
                    product_id=product.id,
                    size_id=None,
                    store_id=body.store_id,
                )
                size_costs.append({"size_id": None, "size_name": None, "cost": cost})
            except (ValueError, RecursionError):
                pass

        results.append({
            "product_id": product.id,
            "product_name": product.name,
            "sizes": size_costs,
        })

    return results
