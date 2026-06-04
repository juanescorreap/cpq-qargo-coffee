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
    UniqueConstraint,
)
from sqlalchemy.sql import func

from backend.database import Base


class Competitor(Base):
    """Monitored competitor chain or business."""

    __tablename__ = "competitors"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    name: str = Column(String(160), nullable=False)
    website_url: str | None = Column(Text)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompetitorProduct(Base):
    """Stable catalog entry for a competitor's product (V2 split).

    Identity is stable across scrapes; price history lives in
    ``competitor_price_observations``. Matches reference this id.
    """

    __tablename__ = "competitor_products"

    __table_args__ = (
        UniqueConstraint(
            "competitor_id", "product_name", "size_description",
            name="uq_competitor_products",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    competitor_id: int = Column(
        BigInteger, ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_ref: str | None = Column(String(120))
    product_name: str = Column(String(180), nullable=False)
    category: str | None = Column(String(80))
    size_description: str | None = Column(String(80))
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompetitorPriceObservation(Base):
    """Scrape log: one row per observed price (V2 split, partitioned by scraped_at).

    Range-partitioned on ``scraped_at``, so the primary key is composite
    ``(id, scraped_at)``.
    """

    __tablename__ = "competitor_price_observations"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    competitor_product_id: int = Column(
        BigInteger, ForeignKey("competitor_products.id", ondelete="CASCADE"), nullable=False
    )
    price: float | None = Column(Numeric(14, 4))
    currency_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
        server_default="COP",
    )
    source_url: str | None = Column(Text)
    scraped_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )


class ProductCompetitorMatch(Base):
    """Manual link between an own product size and a competitor catalog product.

    FK to the stable competitor_products catalog (V2), so matches survive
    re-scrapes.
    """

    __tablename__ = "product_competitor_matches"

    __table_args__ = (
        UniqueConstraint(
            "our_product_id",
            "our_size_id",
            "competitor_product_id",
            name="uq_product_competitor_matches",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    our_product_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    our_size_id: int = Column(
        BigInteger, ForeignKey("product_sizes.id", ondelete="CASCADE"), nullable=False
    )
    competitor_product_id: int = Column(
        BigInteger, ForeignKey("competitor_products.id", ondelete="CASCADE"), nullable=False
    )
    matched_by: str | None = Column(String(120))
    matched_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: str | None = Column(Text)
