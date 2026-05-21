from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from backend.database import Base


class RecipeUnit(Base):
    """Unit of measure used in cafeteria recipes.

    Represents practically meaningful units for baristas that do not always
    correspond to standard volume or weight units:

        - 'pump'     → syrup pump dose
        - 'shot'     → espresso extraction
        - 'teaspoon' → teaspoon
        - 'scoop'    → standard powder scoop

    The actual conversion to the ingredient's usage_units is stored in
    IngredientRecipeUnitConversion, since it can vary per ingredient
    (e.g.: 1 pump of vanilla syrup ≠ 1 pump of caramel syrup).
    """

    __tablename__ = "recipe_units"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(50), unique=True, nullable=False)  # e.g.: "pump"
    category: str | None = Column(String(50))   # 'volume' | 'weight' | 'count' | 'visual'
    description: str | None = Column(Text)
    is_active: bool = Column(Boolean, default=True)


class IngredientRecipeUnitConversion(Base):
    """Conversion between a recipe_unit and the usage_unit of a specific ingredient.

    Allows the costing engine to translate recipe quantities into the
    ingredient's usage units to calculate the cost per drink.

    Examples:
        - 1 pump of vanilla syrup      = 30 ml  (usage_unit: ml)
        - 1 shot of espresso           = 30 ml  (usage_unit: ml)
        - 1 teaspoon of white sugar    = 5 g    (usage_unit: g)
        - 1 scoop of collagen protein  = 10 g   (usage_unit: g)

    The combination (ingredient_id, recipe_unit_id) is unique: an ingredient
    can only have one conversion defined per recipe_unit.
    """

    __tablename__ = "ingredient_recipe_unit_conversions"

    __table_args__ = (
        UniqueConstraint(
            "ingredient_id",
            "recipe_unit_id",
            name="uq_ingredient_recipe_unit",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    ingredient_id: int = Column(
        Integer, ForeignKey("ingredients.id"), nullable=False, index=True
    )
    recipe_unit_id: int = Column(
        Integer, ForeignKey("recipe_units.id"), nullable=False
    )
    usage_unit_quantity: float = Column(Numeric(10, 4), nullable=False)
    notes: str | None = Column(Text)
