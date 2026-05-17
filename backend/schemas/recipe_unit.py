from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# ---------------------------------------------------------------------------
# RecipeUnit
# ---------------------------------------------------------------------------

class RecipeUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: Optional[str] = None
    description: Optional[str] = None
    is_active: bool


# ---------------------------------------------------------------------------
# IngredientRecipeUnitConversion
# ---------------------------------------------------------------------------

class IngredientRecipeUnitConversionBase(BaseModel):
    ingredient_id: int
    recipe_unit_id: int
    usage_unit_quantity: Decimal
    notes: Optional[str] = None

    @field_validator("usage_unit_quantity")
    @classmethod
    def quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("usage_unit_quantity must be > 0")
        return v


class IngredientRecipeUnitConversionCreate(IngredientRecipeUnitConversionBase):
    pass


class IngredientRecipeUnitConversionBody(BaseModel):
    """Body for POST /ingredients/{id}/conversions — ingredient_id comes from the path."""

    recipe_unit_id: int
    usage_unit_quantity: Decimal
    notes: Optional[str] = None

    @field_validator("usage_unit_quantity")
    @classmethod
    def quantity_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("usage_unit_quantity must be > 0")
        return v


class IngredientRecipeUnitConversionResponse(IngredientRecipeUnitConversionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient_name: Optional[str] = None
    recipe_unit_name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def extract_related_names(cls, data: Any) -> Any:
        """Pull display names from loaded ORM relationships when available."""
        if not hasattr(data, "__dict__"):
            return data
        if not getattr(data, "ingredient_name", None):
            ingredient = getattr(data, "ingredient", None)
            if ingredient is not None:
                data.__dict__["ingredient_name"] = ingredient.name
        if not getattr(data, "recipe_unit_name", None):
            recipe_unit = getattr(data, "recipe_unit", None)
            if recipe_unit is not None:
                data.__dict__["recipe_unit_name"] = recipe_unit.name
        return data
