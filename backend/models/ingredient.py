from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
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

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    category: str | None = Column(String(100), index=True)

    # --- Purchase unit (how it arrives from the supplier) ---
    purchase_unit: str | None = Column(String(50))          # e.g.: "1L box"
    purchase_price: float | None = Column(Numeric(10, 2))   # price per purchase_unit

    # --- Recipe usage unit ---
    usage_unit: str | None = Column(String(50))             # e.g.: "ml"
    conversion_factor: float | None = Column(Numeric(10, 4))  # usage_units per purchase_unit

    # --- Waste and yield ---
    yield_percentage: float = Column(Numeric(5, 2), default=100.00)

    # --- Scraping ---
    source_url: str | None = Column(Text)
    last_scraped: object | None = Column(DateTime(timezone=True))

    canonical_unit: str | None = Column(String(100))

    # --- Control ---
    is_active: bool = Column(Boolean, default=True, index=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IngredientPriceHistory(Base):
    """Price change history for an ingredient.

    Each row records a point-in-time price together with its source: it can
    come from an automatic scraping, a manual edit, or a bulk upload. Allows
    auditing cost evolution over time and detecting supplier price variations.
    """

    __tablename__ = "ingredient_price_history"

    id: int = Column(Integer, primary_key=True, index=True)
    ingredient_id: int = Column(
        Integer, ForeignKey("ingredients.id"), nullable=False, index=True
    )
    price: float = Column(Numeric(10, 2), nullable=False)
    source: str | None = Column(String(50))  # 'scraping' | 'manual' | 'bulk_upload'
    changed_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )
