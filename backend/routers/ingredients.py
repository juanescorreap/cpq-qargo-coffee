from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.schemas.ingredient import IngredientCreate, IngredientResponse, IngredientUpdate

router = APIRouter(prefix="/api/ingredients", tags=["ingredients"])


@router.get("", response_model=List[IngredientResponse])
def list_ingredients(
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search in name (case-insensitive)"),
    is_active: bool = Query(True, description="Filter by active status"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    db: Session = Depends(get_db),
) -> List[IngredientResponse]:
    """Return a paginated list of ingredients with optional filters."""
    q = db.query(Ingredient).filter(Ingredient.is_active == is_active)

    if category:
        q = q.filter(Ingredient.category == category)

    if search:
        q = q.filter(Ingredient.name.ilike(f"%{search}%"))

    return q.order_by(Ingredient.name).offset(skip).limit(limit).all()


@router.get("/{ingredient_id}", response_model=IngredientResponse)
def get_ingredient(ingredient_id: int, db: Session = Depends(get_db)) -> IngredientResponse:
    """Return a single ingredient by ID. Raises 404 if not found."""
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")
    return ingredient


@router.post("", response_model=IngredientResponse, status_code=status.HTTP_201_CREATED)
def create_ingredient(body: IngredientCreate, db: Session = Depends(get_db)) -> IngredientResponse:
    """Create a new ingredient and return it."""
    ingredient = Ingredient(**body.model_dump())
    db.add(ingredient)
    db.commit()
    db.refresh(ingredient)
    return ingredient


@router.put("/{ingredient_id}", response_model=IngredientResponse)
def replace_ingredient(
    ingredient_id: int, body: IngredientUpdate, db: Session = Depends(get_db)
) -> IngredientResponse:
    """Replace all updatable fields of an ingredient. Raises 404 if not found."""
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")

    for field, value in body.model_dump().items():
        setattr(ingredient, field, value)

    db.commit()
    db.refresh(ingredient)
    return ingredient


@router.patch("/{ingredient_id}", response_model=IngredientResponse)
def update_ingredient(
    ingredient_id: int, body: IngredientUpdate, db: Session = Depends(get_db)
) -> IngredientResponse:
    """Partially update an ingredient — only provided fields are changed. Raises 404 if not found."""
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(ingredient, field, value)

    db.commit()
    db.refresh(ingredient)
    return ingredient


@router.delete("/{ingredient_id}", status_code=status.HTTP_200_OK)
def deactivate_ingredient(ingredient_id: int, db: Session = Depends(get_db)) -> dict:
    """Soft-delete an ingredient by marking it inactive. Raises 404 if not found."""
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")

    ingredient.is_active = False
    db.commit()
    return {"message": "Ingredient deactivated"}
