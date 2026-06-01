from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from backend.database import Base


class Modifier(Base):
    """Named modification applicable to a product (e.g. almond milk swap, extra shot).

    Effects on ingredients are stored in ModifierIngredientEffect, allowing one
    modifier to affect multiple ingredients. A substitution like "almond milk instead
    of regular milk" has two effects: remove regular milk (-240 ml) and add almond
    milk (+240 ml).

    Supported types:
        - 'substitution': replaces one ingredient with another (two effects).
        - 'addition':     adds an extra ingredient to the base recipe.
        - 'extra_shot':   semantic shortcut for additional espresso shots.
    """

    __tablename__ = "modifiers"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    type: str | None = Column(String(50))
    is_active: bool = Column(Boolean, default=True)


class ModifierIngredientEffect(Base):
    """Ingredient-level effect of applying a modifier.

    quantity_change uses the ingredient's usage_unit. Negative values subtract,
    positive values add.

    Examples:
        "Almond milk instead of regular" → two rows for the same modifier_id:
            ingredient_id=regular_milk,  quantity_change=-240
            ingredient_id=almond_milk,   quantity_change=+240

        "Extra espresso shot" → one row:
            ingredient_id=espresso, quantity_change=+1
    """

    __tablename__ = "modifier_ingredient_effects"

    __table_args__ = (
        UniqueConstraint("modifier_id", "ingredient_id", name="uq_modifier_ingredient"),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    modifier_id: int = Column(
        Integer, ForeignKey("modifiers.id"), nullable=False, index=True
    )
    ingredient_id: int = Column(
        Integer, ForeignKey("ingredients.id"), nullable=False
    )
    quantity_change: float = Column(Numeric(10, 4), nullable=False)


class ProductModifierCost(Base):
    """Pre-calculated cost delta of applying a modifier to a specific product.

    calculated_at records the last computation, making stale records detectable
    after an ingredient price update.
    """

    __tablename__ = "product_modifier_costs"

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(Integer, ForeignKey("products.id"), nullable=False)
    modifier_id: int = Column(Integer, ForeignKey("modifiers.id"), nullable=False)
    cost_impact: float = Column(Numeric(10, 2), nullable=False)
    calculated_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )
