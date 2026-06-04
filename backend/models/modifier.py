from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Identity,
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

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    name: str = Column(String(120), nullable=False)
    type: str | None = Column(String(60))
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ModifierIngredientEffect(Base):
    """Ingredient-level effect of applying a modifier.

    quantity_change uses the ingredient's usage_unit. Negative values subtract,
    positive values add (signed, so the non-negative quantity_amount domain is
    not used here).
    """

    __tablename__ = "modifier_ingredient_effects"

    __table_args__ = (
        UniqueConstraint(
            "modifier_id", "ingredient_id", name="uq_modifier_ingredient_effects"
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    modifier_id: int = Column(
        BigInteger, ForeignKey("modifiers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    quantity_change: float = Column(Numeric(14, 6), nullable=False)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
# NOTE: ProductModifierCost removed in V2 (migration 0011). The derived modifier
# cost now lives in the materialized view ``mv_product_modifier_cost`` (refreshed
# on price/effect changes) instead of a table that silently went stale.
