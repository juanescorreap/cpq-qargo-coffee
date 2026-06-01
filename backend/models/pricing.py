from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.sql import func

from backend.database import Base


class CategoryMargin(Base):
    """Default markup for each product category.

    The pricing engine queries this table when a product has no
    markup_override in ProductPricing. The category must match exactly
    the category field of Product.

    Example:
        category="bebidas_calientes", markup_percentage=65.00
        category="bebidas_frias",     markup_percentage=70.00
        category="alimentos",         markup_percentage=55.00
    """

    __tablename__ = "category_margins"

    id: int = Column(Integer, primary_key=True, index=True)
    category: str = Column(String(100), ForeignKey("categories.slug"), unique=True, nullable=False)
    markup_percentage: float = Column(Numeric(5, 2), nullable=False)
    notes: str | None = Column(Text)


class ProductPricing(Base):
    """Current price of a product for a given size and store.

    A nullable store_id allows defining a global price (NULL) that applies to
    all stores, or a store-specific price that overrides it.

    The engine calculates calculated_cost from the recipe and stores it here
    for auditing. The final price is determined as follows:
        - If is_manual_price=True: final_price is used as entered.
        - If markup_override IS NOT NULL: final_price = calculated_cost * (1 + markup_override/100).
        - If markup_override IS NULL: the markup from CategoryMargin for the category is used.

    effective_date allows scheduling future price changes: the engine always
    takes the record with the most recent effective_date <= today.

    Example:
        product_id=5 (Cappuccino), size_id=2 (medium), store_id=NULL,
        calculated_cost=12.50, markup_override=NULL, final_price=28.00
    """

    __tablename__ = "product_pricing"

    __table_args__ = (
        # Partial unique: store-specific prices — NULL-safe because store_id IS NOT NULL
        Index(
            "uq_product_pricing_store",
            "product_id", "size_id", "store_id", "effective_date",
            unique=True,
            postgresql_where=text("store_id IS NOT NULL"),
        ),
        # Partial unique: global prices — excludes store_id from index to handle NULLs
        Index(
            "uq_product_pricing_global",
            "product_id", "size_id", "effective_date",
            unique=True,
            postgresql_where=text("store_id IS NULL"),
        ),
        # Composite lookup index for "most recent price <= today" queries
        Index(
            "ix_product_pricing_lookup",
            "product_id", "size_id", "store_id", "effective_date",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False, index=True
    )
    size_id: int = Column(Integer, ForeignKey("product_sizes.id"), nullable=False)
    store_id: int | None = Column(
        Integer, ForeignKey("stores.id"), nullable=True, index=True
    )
    calculated_cost: float = Column(Numeric(10, 2), nullable=False)
    markup_override: float | None = Column(Numeric(5, 2), nullable=True)
    final_price: float = Column(Numeric(10, 2), nullable=False)
    is_manual_price: bool = Column(Boolean, default=False)
    effective_date: object = Column(
        Date, nullable=False, server_default=func.current_date()
    )
    currency_code: str = Column(String(3), nullable=False, server_default="COP")


class ProductPriceHistory(Base):
    """Price change history for analysis and auditing.

    Each time ProductPricing is updated, the engine inserts a row here with
    the complete snapshot. Allows reconstructing the evolution of costs and
    margins over time for profitability reports.

    markup_used records the markup that was actually applied (either the
    override or the CategoryMargin value) so that the history is self-contained
    without needing to recalculate.

    Usage example:
        Analyze how the cost of a Cappuccino rose during a quarter due to a
        coffee price increase, and when the final price was adjusted.
    """

    __tablename__ = "product_price_history"

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(Integer, ForeignKey("products.id"), nullable=False)
    size_id: int = Column(Integer, ForeignKey("product_sizes.id"), nullable=False)
    store_id: int | None = Column(
        Integer, ForeignKey("stores.id"), nullable=True
    )
    cost: float = Column(Numeric(10, 2), nullable=False)
    price: float = Column(Numeric(10, 2), nullable=False)
    markup_used: float = Column(Numeric(5, 2), nullable=False)
    currency_code: str = Column(String(3), nullable=False, server_default="COP")
    changed_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )
