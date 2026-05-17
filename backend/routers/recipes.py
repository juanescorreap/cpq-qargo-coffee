from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, aliased

from backend.database import get_db
from backend.models.ingredient import Ingredient
from backend.models.product import Product, RecipeIngredient, RecipeSubRecipe
from backend.models.recipe_unit import RecipeUnit
from backend.schemas.product import ProductResponse
from backend.schemas.recipe import (
    RecipeFullResponse,
    RecipeIngredientCreate,
    RecipeIngredientResponse,
    RecipeSubRecipeCreate,
    RecipeSubRecipeResponse,
)

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_product_or_404(product_id: int, db: Session) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def _fetch_recipe_ingredient(ri_id: int, db: Session) -> RecipeIngredientResponse:
    """Return RecipeIngredientResponse for a single row, with joined names."""
    row = (
        db.query(
            RecipeIngredient,
            Ingredient.name.label("ingredient_name"),
            RecipeUnit.name.label("recipe_unit_name"),
        )
        .join(Ingredient, RecipeIngredient.ingredient_id == Ingredient.id)
        .outerjoin(RecipeUnit, RecipeIngredient.recipe_unit_id == RecipeUnit.id)
        .filter(RecipeIngredient.id == ri_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe ingredient not found")
    ri, ingredient_name, recipe_unit_name = row
    return _build_ri_response(ri, ingredient_name, recipe_unit_name)


def _fetch_recipe_sub_recipe(rsr_id: int, db: Session) -> RecipeSubRecipeResponse:
    """Return RecipeSubRecipeResponse for a single row, with joined sub-recipe name."""
    SubProduct = aliased(Product)
    row = (
        db.query(RecipeSubRecipe, SubProduct.name.label("sub_recipe_name"))
        .join(SubProduct, RecipeSubRecipe.sub_recipe_id == SubProduct.id)
        .filter(RecipeSubRecipe.id == rsr_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe sub-recipe not found")
    rsr, sub_recipe_name = row
    return _build_rsr_response(rsr, sub_recipe_name)


def _build_ri_response(
    ri: RecipeIngredient,
    ingredient_name: str | None,
    recipe_unit_name: str | None,
) -> RecipeIngredientResponse:
    return RecipeIngredientResponse.model_validate({
        "id": ri.id,
        "product_id": ri.product_id,
        "ingredient_id": ri.ingredient_id,
        "quantity": ri.quantity,
        "recipe_unit_id": ri.recipe_unit_id,
        "scales_with_size": ri.scales_with_size,
        "process_yield_loss": ri.process_yield_loss,
        "notes": ri.notes,
        "ingredient_name": ingredient_name,
        "recipe_unit_name": recipe_unit_name,
    })


def _build_rsr_response(
    rsr: RecipeSubRecipe,
    sub_recipe_name: str | None,
) -> RecipeSubRecipeResponse:
    return RecipeSubRecipeResponse.model_validate({
        "id": rsr.id,
        "parent_product_id": rsr.parent_product_id,
        "sub_recipe_id": rsr.sub_recipe_id,
        "quantity": rsr.quantity,
        "scales_with_size": rsr.scales_with_size,
        "sub_recipe_name": sub_recipe_name,
    })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{product_id}", response_model=RecipeFullResponse)
def get_recipe(product_id: int, db: Session = Depends(get_db)) -> RecipeFullResponse:
    """Return the full recipe for a product: its details, all ingredients, and sub-recipes."""
    product = _get_product_or_404(product_id, db)

    ri_rows = (
        db.query(
            RecipeIngredient,
            Ingredient.name.label("ingredient_name"),
            RecipeUnit.name.label("recipe_unit_name"),
        )
        .join(Ingredient, RecipeIngredient.ingredient_id == Ingredient.id)
        .outerjoin(RecipeUnit, RecipeIngredient.recipe_unit_id == RecipeUnit.id)
        .filter(RecipeIngredient.product_id == product_id)
        .order_by(RecipeIngredient.id)
        .all()
    )

    SubProduct = aliased(Product)
    rsr_rows = (
        db.query(RecipeSubRecipe, SubProduct.name.label("sub_recipe_name"))
        .join(SubProduct, RecipeSubRecipe.sub_recipe_id == SubProduct.id)
        .filter(RecipeSubRecipe.parent_product_id == product_id)
        .order_by(RecipeSubRecipe.id)
        .all()
    )

    return RecipeFullResponse(
        product=ProductResponse.model_validate(product),
        ingredients=[_build_ri_response(ri, ing, ru) for ri, ing, ru in ri_rows],
        sub_recipes=[_build_rsr_response(rsr, name) for rsr, name in rsr_rows],
    )


@router.post(
    "/{product_id}/ingredients",
    response_model=RecipeIngredientResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_ingredient(
    product_id: int, body: RecipeIngredientCreate, db: Session = Depends(get_db)
) -> RecipeIngredientResponse:
    """Add an ingredient line to a product's recipe."""
    _get_product_or_404(product_id, db)

    if db.get(Ingredient, body.ingredient_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")

    if body.recipe_unit_id is not None and db.get(RecipeUnit, body.recipe_unit_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RecipeUnit not found")

    ri = RecipeIngredient(product_id=product_id, **body.model_dump())
    db.add(ri)
    db.commit()
    return _fetch_recipe_ingredient(ri.id, db)


@router.put("/ingredients/{ingredient_line_id}", response_model=RecipeIngredientResponse)
def update_ingredient_line(
    ingredient_line_id: int, body: RecipeIngredientCreate, db: Session = Depends(get_db)
) -> RecipeIngredientResponse:
    """Replace all fields of a recipe ingredient line."""
    ri = db.get(RecipeIngredient, ingredient_line_id)
    if ri is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe ingredient not found")

    if db.get(Ingredient, body.ingredient_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")

    if body.recipe_unit_id is not None and db.get(RecipeUnit, body.recipe_unit_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RecipeUnit not found")

    for field, value in body.model_dump().items():
        setattr(ri, field, value)

    db.commit()
    return _fetch_recipe_ingredient(ri.id, db)


@router.delete("/ingredients/{ingredient_line_id}", status_code=status.HTTP_200_OK)
def remove_ingredient(ingredient_line_id: int, db: Session = Depends(get_db)) -> dict:
    """Remove an ingredient line from a recipe (hard delete)."""
    ri = db.get(RecipeIngredient, ingredient_line_id)
    if ri is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe ingredient not found")

    db.delete(ri)
    db.commit()
    return {"message": "Ingredient removed from recipe"}


@router.post(
    "/{product_id}/sub-recipes",
    response_model=RecipeSubRecipeResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_sub_recipe(
    product_id: int, body: RecipeSubRecipeCreate, db: Session = Depends(get_db)
) -> RecipeSubRecipeResponse:
    """Link a sub-recipe (batch component) to a product's recipe."""
    _get_product_or_404(product_id, db)

    sub = db.get(Product, body.sub_recipe_id)
    if sub is None or not sub.is_sub_recipe:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sub-recipe product not found or not marked as sub-recipe",
        )

    rsr = RecipeSubRecipe(parent_product_id=product_id, **body.model_dump())
    db.add(rsr)
    db.commit()
    return _fetch_recipe_sub_recipe(rsr.id, db)


@router.delete("/sub-recipes/{sub_recipe_line_id}", status_code=status.HTTP_200_OK)
def remove_sub_recipe(sub_recipe_line_id: int, db: Session = Depends(get_db)) -> dict:
    """Remove a sub-recipe link from a recipe (hard delete)."""
    rsr = db.get(RecipeSubRecipe, sub_recipe_line_id)
    if rsr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe sub-recipe not found")

    db.delete(rsr)
    db.commit()
    return {"message": "Sub-recipe removed from recipe"}
