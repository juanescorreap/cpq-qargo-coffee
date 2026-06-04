from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    String,
)
from sqlalchemy.sql import func

from backend.database import Base


class Store(Base):
    """Represents a store or point of sale of the cafeteria chain."""

    __tablename__ = "stores"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    code: str = Column(String(40), unique=True, nullable=False)  # e.g.: "BOG-ZONA-T"
    name: str = Column(String(160), nullable=False)
    city: str | None = Column(String(120))
    region_id: int | None = Column(
        BigInteger, ForeignKey("regions.id", ondelete="SET NULL"), nullable=True
    )
    default_currency_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
        server_default="COP",
    )
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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

    # V2 (migration 0012): temporal. The no-overlap EXCLUDE (no_overlap_sip) and
    # validity CHECK are defined in the migration. One valid local price per
    # (store, ingredient) at any given date.

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    store_id: int = Column(
        BigInteger, ForeignKey("stores.id"), nullable=False, index=True
    )
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id"), nullable=False
    )
    local_price: float = Column(Numeric(14, 4), nullable=False)
    currency_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
        server_default="COP",
    )
    local_supplier: str | None = Column(String(160))
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
