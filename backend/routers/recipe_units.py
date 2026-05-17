from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.recipe_unit import IngredientRecipeUnitConversion, RecipeUnit
from backend.schemas.recipe_unit import (
    IngredientRecipeUnitConversionBody,
    IngredientRecipeUnitConversionCreate,
    IngredientRecipeUnitConversionResponse,
    RecipeUnitResponse,
)

router = APIRouter(prefix="/api", tags=["recipe_units"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_ingredient_or_404(ingredient_id: int, db: Session) -> Ingredient:
    ing = db.get(Ingredient, ingredient_id)
    if ing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")
    return ing


def _fetch_conversion_response(conv_id: int, db: Session) -> IngredientRecipeUnitConversionResponse:
    """Return IngredientRecipeUnitConversionResponse with names populated via join."""
    row = (
        db.query(
            IngredientRecipeUnitConversion,
            Ingredient.name.label("ingredient_name"),
            RecipeUnit.name.label("recipe_unit_name"),
        )
        .join(Ingredient, IngredientRecipeUnitConversion.ingredient_id == Ingredient.id)
        .join(RecipeUnit, IngredientRecipeUnitConversion.recipe_unit_id == RecipeUnit.id)
        .filter(IngredientRecipeUnitConversion.id == conv_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversion not found")
    conv, ingredient_name, recipe_unit_name = row
    return IngredientRecipeUnitConversionResponse.model_validate({
        "id": conv.id,
        "ingredient_id": conv.ingredient_id,
        "recipe_unit_id": conv.recipe_unit_id,
        "usage_unit_quantity": conv.usage_unit_quantity,
        "notes": conv.notes,
        "ingredient_name": ingredient_name,
        "recipe_unit_name": recipe_unit_name,
    })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/recipe-units", response_model=List[RecipeUnitResponse])
def list_recipe_units(db: Session = Depends(get_db)) -> List[RecipeUnitResponse]:
    """Return all active recipe units available for use in recipes."""
    return (
        db.query(RecipeUnit)
        .filter(RecipeUnit.is_active == True)
        .order_by(RecipeUnit.category, RecipeUnit.name)
        .all()
    )


@router.get(
    "/ingredients/{ingredient_id}/conversions",
    response_model=List[IngredientRecipeUnitConversionResponse],
)
def list_conversions(
    ingredient_id: int, db: Session = Depends(get_db)
) -> List[IngredientRecipeUnitConversionResponse]:
    """Return all recipe-unit conversions defined for a specific ingredient."""
    _get_ingredient_or_404(ingredient_id, db)
    rows = (
        db.query(
            IngredientRecipeUnitConversion,
            Ingredient.name.label("ingredient_name"),
            RecipeUnit.name.label("recipe_unit_name"),
        )
        .join(Ingredient, IngredientRecipeUnitConversion.ingredient_id == Ingredient.id)
        .join(RecipeUnit, IngredientRecipeUnitConversion.recipe_unit_id == RecipeUnit.id)
        .filter(IngredientRecipeUnitConversion.ingredient_id == ingredient_id)
        .order_by(RecipeUnit.name)
        .all()
    )
    return [
        IngredientRecipeUnitConversionResponse.model_validate({
            "id": conv.id,
            "ingredient_id": conv.ingredient_id,
            "recipe_unit_id": conv.recipe_unit_id,
            "usage_unit_quantity": conv.usage_unit_quantity,
            "notes": conv.notes,
            "ingredient_name": ingredient_name,
            "recipe_unit_name": recipe_unit_name,
        })
        for conv, ingredient_name, recipe_unit_name in rows
    ]


@router.post(
    "/ingredients/{ingredient_id}/conversions",
    response_model=IngredientRecipeUnitConversionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversion(
    ingredient_id: int,
    body: IngredientRecipeUnitConversionBody,
    db: Session = Depends(get_db),
) -> IngredientRecipeUnitConversionResponse:
    """Create a recipe-unit conversion for an ingredient. ingredient_id is taken from the URL path."""
    _get_ingredient_or_404(ingredient_id, db)

    if db.get(RecipeUnit, body.recipe_unit_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RecipeUnit not found")

    duplicate = (
        db.query(IngredientRecipeUnitConversion)
        .filter(
            IngredientRecipeUnitConversion.ingredient_id == ingredient_id,
            IngredientRecipeUnitConversion.recipe_unit_id == body.recipe_unit_id,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversion for this (ingredient, recipe_unit) pair already exists",
        )

    conv = IngredientRecipeUnitConversion(
        ingredient_id=ingredient_id,
        recipe_unit_id=body.recipe_unit_id,
        usage_unit_quantity=body.usage_unit_quantity,
        notes=body.notes,
    )
    db.add(conv)
    db.commit()
    return _fetch_conversion_response(conv.id, db)


@router.put(
    "/conversions/{conversion_id}",
    response_model=IngredientRecipeUnitConversionResponse,
)
def update_conversion(
    conversion_id: int,
    body: IngredientRecipeUnitConversionCreate,
    db: Session = Depends(get_db),
) -> IngredientRecipeUnitConversionResponse:
    """Replace all fields of a recipe-unit conversion. Raises 404 if not found."""
    conv = db.get(IngredientRecipeUnitConversion, conversion_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversion not found")

    if db.get(Ingredient, body.ingredient_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")

    if db.get(RecipeUnit, body.recipe_unit_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RecipeUnit not found")

    for field, value in body.model_dump().items():
        setattr(conv, field, value)

    db.commit()
    return _fetch_conversion_response(conv.id, db)


@router.delete("/conversions/{conversion_id}", status_code=status.HTTP_200_OK)
def delete_conversion(conversion_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete a recipe-unit conversion (hard delete)."""
    conv = db.get(IngredientRecipeUnitConversion, conversion_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversion not found")
    db.delete(conv)
    db.commit()
    return {"message": "Conversion deleted"}
