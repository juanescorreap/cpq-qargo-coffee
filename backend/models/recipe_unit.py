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
    """Unidad de medida usada en recetas de cafetería.

    Representa unidades de sentido práctico para baristas que no siempre
    coinciden con unidades estándar de volumen o peso:

        - 'pump'     → dosis de bomba de jarabe
        - 'shot'     → extracción de espresso
        - 'teaspoon' → cucharadita
        - 'scoop'    → medidor estándar de polvo

    La conversión real a usage_units del ingrediente se almacena en
    IngredientRecipeUnitConversion, ya que puede variar por ingrediente
    (ej: 1 pump de jarabe de vainilla ≠ 1 pump de jarabe de caramelo).
    """

    __tablename__ = "recipe_units"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(50), unique=True, nullable=False)  # ej: "pump"
    category: str | None = Column(String(50))   # 'volume' | 'weight' | 'count' | 'visual'
    description: str | None = Column(Text)
    is_active: bool = Column(Boolean, default=True)


class IngredientRecipeUnitConversion(Base):
    """Conversión entre una recipe_unit y la usage_unit de un ingrediente específico.

    Permite que el motor de costeo traduzca cantidades de receta a unidades
    de uso del ingrediente para calcular el costo por bebida.

    Ejemplos:
        - 1 pump de jarabe de vainilla   = 30 ml  (usage_unit: ml)
        - 1 shot de espresso             = 30 ml  (usage_unit: ml)
        - 1 teaspoon de azúcar blanca    = 5 g    (usage_unit: g)
        - 1 scoop de proteína de colágeno = 10 g  (usage_unit: g)

    La combinación (ingredient_id, recipe_unit_id) es única: un ingrediente
    solo puede tener una conversión definida por recipe_unit.
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
