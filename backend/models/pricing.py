from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
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
    the slug of a row in ``categories``.
    """

    __tablename__ = "category_margins"

    __table_args__ = (
        UniqueConstraint("category", name="uq_category_margins_category"),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    category: str = Column(
        String(80),
        ForeignKey("categories.slug", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )
    markup_percentage: float = Column(Numeric(6, 3), nullable=False)  # pct_amount
    notes: str | None = Column(Text)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductPricing(Base):
    """Current effective price of a product per (size, store, currency).

    A nullable store_id allows defining a chain-wide default price (NULL) that
    applies to all stores, or a store-specific price that overrides it.

    Uniqueness is enforced by ``uq_product_pricing_current`` over
    (product_id, size_id, COALESCE(store_id, 0), currency_code).
    """

    __tablename__ = "product_pricing"

    __table_args__ = (
        Index(
            "uq_product_pricing_current",
            "product_id",
            "size_id",
            text("COALESCE(store_id, 0)"),
            "currency_code",
            unique=True,
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    product_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    size_id: int = Column(
        BigInteger, ForeignKey("product_sizes.id", ondelete="CASCADE"), nullable=False
    )
    store_id: int | None = Column(
        BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), nullable=True, index=True
    )
    calculated_cost: float = Column(Numeric(14, 4), nullable=False)
    markup_override: float | None = Column(Numeric(6, 3), nullable=True)
    final_price: float = Column(Numeric(14, 4), nullable=False)
    is_manual_price: bool = Column(Boolean, nullable=False, default=False)
    effective_date: object = Column(
        Date, nullable=False, server_default=func.current_date()
    )
    currency_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
        server_default="COP",
    )
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductPriceHistory(Base):
    """Price change history for analysis and auditing (append-only, partitioned).

    Range-partitioned on ``changed_at`` in PostgreSQL, so the primary key is
    composite ``(id, changed_at)``.

    markup_used records the markup that was actually applied (either the
    override or the CategoryMargin value) so that the history is self-contained.
    """

    __tablename__ = "product_price_history"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    product_id: int = Column(BigInteger, ForeignKey("products.id"), nullable=False)
    size_id: int = Column(BigInteger, ForeignKey("product_sizes.id"), nullable=False)
    store_id: int | None = Column(
        BigInteger, ForeignKey("stores.id", ondelete="SET NULL"), nullable=True
    )
    cost: float = Column(Numeric(14, 4), nullable=False)
    price: float = Column(Numeric(14, 4), nullable=False)
    markup_used: float = Column(Numeric(6, 3), nullable=False)
    currency_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
        server_default="COP",
    )
    changed_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )
