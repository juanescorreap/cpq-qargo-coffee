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
)
from sqlalchemy.sql import func

from backend.database import Base


class Ingredient(Base):
    """Represents an ingredient or supply from the purchasing catalog.

    Stores both purchase information (unit and price at which it is acquired
    from the supplier) and recipe usage information (unit and conversion
    factor), allowing the real cost per portion to be calculated.

    The yield_percentage field captures waste: an ingredient with 80% yield
    increases its effective cost by 25%.
    """

    __tablename__ = "ingredients"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    name: str = Column(String(180), nullable=False)
    category: str | None = Column(String(80), index=True)

    # --- Purchase unit (how it arrives from the supplier) ---
    purchase_unit: str | None = Column(String(40))          # e.g.: "1L box"
    purchase_price: float | None = Column(Numeric(14, 4))   # price per purchase_unit (price_amount)

    # --- Recipe usage unit ---
    usage_unit: str | None = Column(String(40))             # e.g.: "ml"
    conversion_factor: float | None = Column(Numeric(14, 6))  # usage_units per purchase_unit (quantity_amount)

    # --- Waste and yield ---
    yield_percentage: float | None = Column(Numeric(6, 3))   # pct_amount

    canonical_unit: str | None = Column(String(40))

    # --- Scraping ---
    source_url: str | None = Column(Text)
    last_scraped: object | None = Column(DateTime(timezone=True))

    # --- Control ---
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IngredientPriceHistory(Base):
    """Price change history for an ingredient (append-only, partitioned by month).

    The table is range-partitioned on ``changed_at`` in PostgreSQL, so the
    primary key is composite ``(id, changed_at)``.
    """

    __tablename__ = "ingredient_price_history"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id"), nullable=False, index=True
    )
    price: float = Column(Numeric(14, 4), nullable=False)
    source: str | None = Column(String(120))  # 'scraping' | 'manual' | 'bulk_upload'
    changed_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )
