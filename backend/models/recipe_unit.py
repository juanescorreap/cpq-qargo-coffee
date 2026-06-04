from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

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

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    name: str = Column(String(60), unique=True, nullable=False)  # e.g.: "pump"
    category: str | None = Column(String(60))   # 'volume' | 'weight' | 'count' | 'visual'
    description: str | None = Column(Text)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
            name="uq_iruc",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id"), nullable=False, index=True
    )
    recipe_unit_id: int = Column(
        BigInteger, ForeignKey("recipe_units.id"), nullable=False
    )
    usage_unit_quantity: float = Column(Numeric(14, 6), nullable=False)
    notes: str | None = Column(Text)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
