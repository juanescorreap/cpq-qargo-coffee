from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.store import Store, StoreIngredientPrice
from backend.schemas.store import (
    StoreCreate,
    StoreIngredientPriceCreate,
    StoreIngredientPriceResponse,
    StoreResponse,
    StoreUpdate,
)

router = APIRouter(prefix="/api/stores", tags=["stores"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_store_or_404(store_id: int, db: Session) -> Store:
    store = db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    return store


def _fetch_price_response(store_id: int, ingredient_id: int, db: Session) -> StoreIngredientPriceResponse:
    """Return StoreIngredientPriceResponse with ingredient_name populated via join."""
    row = (
        db.query(StoreIngredientPrice, Ingredient.name.label("ingredient_name"))
        .join(Ingredient, StoreIngredientPrice.ingredient_id == Ingredient.id)
        .filter(
            StoreIngredientPrice.store_id == store_id,
            StoreIngredientPrice.ingredient_id == ingredient_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Price override not found")
    price, ingredient_name = row
    return StoreIngredientPriceResponse.model_validate({
        "id": price.id,
        "store_id": price.store_id,
        "ingredient_id": price.ingredient_id,
        "local_price": price.local_price,
        "local_supplier": price.local_supplier,
        "updated_at": price.updated_at,
        "ingredient_name": ingredient_name,
    })


# ---------------------------------------------------------------------------
# Store endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=List[StoreResponse])
def list_stores(
    is_active: bool = Query(True, description="Filter by active status"),
    city: Optional[str] = Query(None, description="Filter by city"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[StoreResponse]:
    """Return a paginated list of stores with optional filters."""
    q = db.query(Store).filter(Store.is_active == is_active)
    if city:
        q = q.filter(func.lower(Store.city) == city.lower())
    return q.order_by(Store.city, Store.name).offset(skip).limit(limit).all()


@router.get("/{store_id}", response_model=StoreResponse)
def get_store(store_id: int, db: Session = Depends(get_db)) -> StoreResponse:
    """Return a single store by ID. Raises 404 if not found."""
    return _get_store_or_404(store_id, db)


@router.post("", response_model=StoreResponse, status_code=status.HTTP_201_CREATED)
def create_store(body: StoreCreate, db: Session = Depends(get_db)) -> StoreResponse:
    """Create a new store. code is automatically uppercased."""
    existing = db.query(Store).filter(func.lower(Store.code) == body.code.lower()).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Store with code '{body.code}' already exists",
        )
    store = Store(**body.model_dump())
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@router.put("/{store_id}", response_model=StoreResponse)
def update_store(
    store_id: int, body: StoreUpdate, db: Session = Depends(get_db)
) -> StoreResponse:
    """Update store fields. Only provided fields are changed. Raises 404 if not found."""
    store = _get_store_or_404(store_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(store, field, value)
    db.commit()
    db.refresh(store)
    return store


@router.delete("/{store_id}", status_code=status.HTTP_200_OK)
def deactivate_store(store_id: int, db: Session = Depends(get_db)) -> dict:
    """Soft-delete a store by marking it inactive. Raises 404 if not found."""
    store = _get_store_or_404(store_id, db)
    store.is_active = False
    db.commit()
    return {"message": "Store deactivated"}


# ---------------------------------------------------------------------------
# StoreIngredientPrice endpoints
# ---------------------------------------------------------------------------

@router.get("/{store_id}/ingredient-prices", response_model=List[StoreIngredientPriceResponse])
def list_ingredient_prices(
    store_id: int, db: Session = Depends(get_db)
) -> List[StoreIngredientPriceResponse]:
    """Return all local price overrides configured for a store."""
    _get_store_or_404(store_id, db)
    rows = (
        db.query(StoreIngredientPrice, Ingredient.name.label("ingredient_name"))
        .join(Ingredient, StoreIngredientPrice.ingredient_id == Ingredient.id)
        .filter(StoreIngredientPrice.store_id == store_id)
        .order_by(Ingredient.name)
        .all()
    )
    return [
        StoreIngredientPriceResponse.model_validate({
            "id": p.id,
            "store_id": p.store_id,
            "ingredient_id": p.ingredient_id,
            "local_price": p.local_price,
            "local_supplier": p.local_supplier,
            "updated_at": p.updated_at,
            "ingredient_name": ingredient_name,
        })
        for p, ingredient_name in rows
    ]


@router.post(
    "/{store_id}/ingredient-prices",
    response_model=StoreIngredientPriceResponse,
    status_code=status.HTTP_201_CREATED,
)
def upsert_ingredient_price(
    store_id: int,
    body: StoreIngredientPriceCreate,
    db: Session = Depends(get_db),
) -> StoreIngredientPriceResponse:
    """Create or update the local price override for an ingredient in a store.

    If a price already exists for this (store, ingredient) pair, it is updated
    in place rather than creating a duplicate.
    """
    _get_store_or_404(store_id, db)

    if db.get(Ingredient, body.ingredient_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")

    existing = (
        db.query(StoreIngredientPrice)
        .filter(
            StoreIngredientPrice.store_id == store_id,
            StoreIngredientPrice.ingredient_id == body.ingredient_id,
        )
        .first()
    )

    if existing:
        existing.local_price = body.local_price
        existing.local_supplier = body.local_supplier
        db.commit()
    else:
        price = StoreIngredientPrice(store_id=store_id, **body.model_dump())
        db.add(price)
        db.commit()

    return _fetch_price_response(store_id, body.ingredient_id, db)


@router.delete(
    "/{store_id}/ingredient-prices/{ingredient_id}",
    status_code=status.HTTP_200_OK,
)
def delete_ingredient_price(
    store_id: int, ingredient_id: int, db: Session = Depends(get_db)
) -> dict:
    """Remove the local price override for an ingredient — falls back to base price."""
    _get_store_or_404(store_id, db)
    price = (
        db.query(StoreIngredientPrice)
        .filter(
            StoreIngredientPrice.store_id == store_id,
            StoreIngredientPrice.ingredient_id == ingredient_id,
        )
        .first()
    )
    if price is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Price override not found")
    db.delete(price)
    db.commit()
    return {"message": "Price override removed — ingredient will use base price"}
