from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.sql import func

from backend.database import Base


class Modifier(Base):
    """Modification applicable to a product that affects cost and ingredients.

    Each row represents an atomic change to a specific ingredient.
    A complex substitution (e.g.: replacing regular milk with almond milk)
    requires TWO records: one that subtracts the regular milk and another that
    adds the almond milk.

    Supported types:
        - 'substitution': replaces one ingredient with another (pair of records).
        - 'addition':     adds an extra ingredient to the base recipe.
        - 'extra_shot':   semantic shortcut for additional espresso shots.

    quantity_change uses the same unit as the ingredient's usage_unit.
    Negative values subtract, positive values add.

    Examples:
        "Almond milk instead of regular" → two records:
            affects_ingredient_id=regular_milk,  quantity_change=-240
            affects_ingredient_id=almond_milk,   quantity_change=+240

        "Extra espresso shot" →
            affects_ingredient_id=espresso, quantity_change=+1,
            type='extra_shot'
    """

    __tablename__ = "modifiers"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    type: str | None = Column(String(50))
    affects_ingredient_id: int | None = Column(
        Integer, ForeignKey("ingredients.id"), nullable=True
    )
    quantity_change: float | None = Column(Numeric(10, 4))
    is_active: bool = Column(Boolean, default=True)


class ProductModifierCost(Base):
    """Cost impact of applying a modifier to a specific product.

    The costing engine pre-calculates the cost delta for each modifier per
    product and stores it here. This avoids recalculating at query time and
    allows auditing how the impact changes when ingredient prices vary.

    calculated_at records when the last calculation was made, making it easy
    to detect stale records after a price update.
    """

    __tablename__ = "product_modifier_costs"

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(Integer, ForeignKey("products.id"), nullable=False)
    modifier_id: int = Column(Integer, ForeignKey("modifiers.id"), nullable=False)
    cost_impact: float = Column(Numeric(10, 2), nullable=False)
    calculated_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )
