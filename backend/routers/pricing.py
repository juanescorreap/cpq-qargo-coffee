from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import ProductPriceHistory, ProductPricing
from backend.models.product import Product, ProductSize
from backend.services.pricing_engine import PricingEngine
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/pricing", tags=["pricing"])


class PriceCalculationRequest(BaseModel):
    product_id: int
    size_id: Optional[int] = None
    store_id: Optional[int] = None
    markup_override: Optional[Decimal] = None


class SetPriceRequest(BaseModel):
    product_id: int
    size_id: Optional[int] = None
    store_id: Optional[int] = None
    final_price: Decimal = Field(gt=0, description="Final price (must be positive)")
    markup_override: Optional[Decimal] = None
    is_manual: bool = True


def _resolve_size(product_id: int, size_id: Optional[int], db: Session) -> int:
    """Return size_id as-is, or fall back to the first size for the product."""
    if size_id is not None:
        return size_id
    first = (
        db.query(ProductSize.id)
        .filter(ProductSize.product_id == product_id)
        .order_by(ProductSize.id)
        .first()
    )
    if first is None:
        raise HTTPException(
            status_code=422,
            detail=f"Product {product_id} has no sizes configured.",
        )
    return first[0]


@router.post("/calculate")
def calculate_price(
    request: PriceCalculationRequest,
    db: Session = Depends(get_db),
):
    """Calculates suggested price based on cost + markup."""
    size_id = _resolve_size(request.product_id, request.size_id, db)
    engine = PricingEngine(db)
    return engine.calculate_price(
        request.product_id,
        size_id,
        request.store_id,
        request.markup_override,
    )


@router.post("/set")
def set_price(
    request: SetPriceRequest,
    db: Session = Depends(get_db),
):
    """Sets the final price for a product."""
    size_id = _resolve_size(request.product_id, request.size_id, db)
    engine = PricingEngine(db)
    pricing = engine.save_pricing(
        request.product_id,
        size_id,
        request.store_id,
        request.final_price,
        request.markup_override,
        request.is_manual,
    )
    return {"message": "Price saved successfully", "pricing_id": pricing.id}


@router.post("/calculate-all")
def calculate_all_prices(
    store_id: Optional[int] = None,
    save_to_db: bool = False,
    db: Session = Depends(get_db),
):
    """Calculates prices for all products.

    Query params:
    - store_id: Calculate only for this store
    - save_to_db: Whether to save the calculated prices
    """
    engine = PricingEngine(db)
    return engine.calculate_all_prices(store_id, save_to_db)


@router.get("/history/{product_id}/{size_id}")
def get_price_history(
    product_id: int = Path(..., gt=0),
    size_id: int = Path(..., gt=0),
    store_id: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db),
):
    """Returns the price history for a product/size."""
    query = db.query(ProductPriceHistory).filter(
        ProductPriceHistory.product_id == product_id,
        ProductPriceHistory.size_id == size_id,
    )

    if store_id is not None:
        query = query.filter(ProductPriceHistory.store_id == store_id)

    history = query.order_by(ProductPriceHistory.changed_at.desc()).limit(50).all()

    return [
        {
            "date": h.changed_at,
            "cost": h.cost,
            "price": h.price,
            "markup": h.markup_used,
        }
        for h in history
    ]


@router.get("/table")
def get_pricing_table(
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Returns all current prices for the manager table."""
    query = (
        db.query(ProductPricing, Product, ProductSize)
        .join(Product, ProductPricing.product_id == Product.id)
        .join(ProductSize, ProductPricing.size_id == ProductSize.id)
        .order_by(ProductPricing.effective_date.desc())
    )

    if store_id is not None:
        query = query.filter(ProductPricing.store_id == store_id)

    rows = query.all()

    seen = set()
    result = []
    for pricing, product, size in rows:
        key = (pricing.product_id, pricing.size_id, pricing.store_id)
        if key in seen:
            continue
        seen.add(key)

        cost = float(pricing.calculated_cost)
        price = float(pricing.final_price)
        margin = (price / cost - 1) * 100 if cost else 0
        gross_margin = (price - cost) / price * 100 if price else 0

        result.append({
            "id":           pricing.id,
            "product_id":   pricing.product_id,
            "product_name": product.name,
            "size_id":      pricing.size_id,
            "size_name":    size.size_name,
            "store_id":     pricing.store_id,
            "cost":         cost,
            "price":        price,
            "margin":       round(margin, 2),
            "gross_margin": round(gross_margin, 2),
            "updated":      pricing.effective_date.isoformat() if pricing.effective_date else None,
            "is_manual":    pricing.is_manual_price,
        })

    return result


@router.get("/current/{product_id}/{size_id}")
def get_current_pricing(
    product_id: int = Path(..., gt=0),
    size_id: int = Path(..., gt=0),
    store_id: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db),
):
    """Returns the most recent current pricing for a product/size."""
    pricing = (
        db.query(ProductPricing)
        .filter(
            ProductPricing.product_id == product_id,
            ProductPricing.size_id == size_id,
            ProductPricing.store_id == store_id,
        )
        .order_by(ProductPricing.effective_date.desc())
        .first()
    )

    if not pricing:
        raise HTTPException(status_code=404, detail="No pricing found")

    return {
        "cost": pricing.calculated_cost,
        "price": pricing.final_price,
        "markup": pricing.markup_override,
        "is_manual": pricing.is_manual_price,
        "effective_date": pricing.effective_date,
    }
