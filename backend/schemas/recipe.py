from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from backend.schemas.product import ProductResponse


# ---------------------------------------------------------------------------
# RecipeIngredient
# ---------------------------------------------------------------------------

class RecipeIngredientBase(BaseModel):
    ingredient_id: int
    quantity: Decimal
    recipe_unit_id: Optional[int] = None
    scales_with_size: bool = True
    process_yield_loss: Decimal = Decimal("0")
    notes: Optional[str] = None

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("quantity must be > 0")
        return v

    @field_validator("process_yield_loss")
    @classmethod
    def yield_loss_range(cls, v: Decimal) -> Decimal:
        if v < 0 or v > 100:
            raise ValueError("process_yield_loss must be between 0 and 100")
        return v


class RecipeIngredientCreate(RecipeIngredientBase):
    pass


class RecipeIngredientResponse(RecipeIngredientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    ingredient_name: Optional[str] = None
    recipe_unit_name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def extract_related_names(cls, data: Any) -> Any:
        """Pull display names from loaded ORM relationships when available."""
        if not hasattr(data, "__dict__"):
            return data

        # ingredient_name from relationship or injected attribute
        if not getattr(data, "ingredient_name", None):
            ingredient = getattr(data, "ingredient", None)
            if ingredient is not None:
                data.__dict__["ingredient_name"] = ingredient.name

        # recipe_unit_name from relationship or injected attribute
        if not getattr(data, "recipe_unit_name", None):
            recipe_unit = getattr(data, "recipe_unit", None)
            if recipe_unit is not None:
                data.__dict__["recipe_unit_name"] = recipe_unit.name

        return data


# ---------------------------------------------------------------------------
# RecipeSubRecipe
# ---------------------------------------------------------------------------

class RecipeSubRecipeBase(BaseModel):
    sub_recipe_id: int
    quantity: Decimal
    scales_with_size: bool = True

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("quantity must be > 0")
        return v


class RecipeSubRecipeCreate(RecipeSubRecipeBase):
    pass


class RecipeSubRecipeResponse(RecipeSubRecipeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_product_id: int
    sub_recipe_name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def extract_sub_recipe_name(cls, data: Any) -> Any:
        """Pull sub-recipe name from loaded ORM relationship when available."""
        if not hasattr(data, "__dict__"):
            return data

        if not getattr(data, "sub_recipe_name", None):
            sub_recipe = getattr(data, "sub_recipe", None)
            if sub_recipe is not None:
                data.__dict__["sub_recipe_name"] = sub_recipe.name

        return data


# ---------------------------------------------------------------------------
# RecipeFullResponse
# ---------------------------------------------------------------------------

class RecipeFullResponse(BaseModel):
    product: ProductResponse
    ingredients: List[RecipeIngredientResponse]
    sub_recipes: List[RecipeSubRecipeResponse]
