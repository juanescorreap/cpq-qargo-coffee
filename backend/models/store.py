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


class Store(Base):
    """Represents a store or point of sale of the cafeteria chain."""

    __tablename__ = "stores"

    id: int = Column(Integer, primary_key=True, index=True)
    code: str = Column(String(20), unique=True, nullable=False)  # e.g.: "BOG-ZONA-T"
    name: str = Column(String(200), nullable=False)
    city: str | None = Column(String(100))
    is_active: bool = Column(Boolean, default=True)


class StoreIngredientPrice(Base):
    """Local price of an ingredient for a specific store.

    Enables three things:
    - Different prices per store: each location can have a different price
      for the same ingredient depending on its regional supplier.
    - Local supplier tracking: records who supplies the ingredient at that
      store, independently of the base supplier.
    - Base price override: when a record exists here, the costing engine uses
      local_price instead of the ingredient's global purchase_price.
    """

    __tablename__ = "store_ingredient_prices"

    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "ingredient_id",
            name="uq_store_ingredient",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    store_id: int = Column(
        Integer, ForeignKey("stores.id"), nullable=False, index=True
    )
    ingredient_id: int = Column(
        Integer, ForeignKey("ingredients.id"), nullable=False
    )
    local_price: float | None = Column(Numeric(10, 2))
    local_supplier: str | None = Column(String(200))
    updated_at: object = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
