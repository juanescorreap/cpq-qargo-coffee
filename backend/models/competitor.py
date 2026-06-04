from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from backend.database import Base


class Competitor(Base):
    """Monitored competitor chain or business.

    Represents a competitor whose menu and prices are periodically tracked
    via scraping. is_active allows deactivating competitors without deleting
    their price history.
    """

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
    """Product scraped from a competitor's menu (append-mostly, partitioned).

    Each row is a snapshot of a product as it appears published on the
    competitor's site at the time of scraping. Range-partitioned on
    ``scraped_at`` in PostgreSQL, so the primary key is composite
    ``(id, scraped_at)``.
    """

    __tablename__ = "competitor_products"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    competitor_id: int = Column(
        BigInteger, ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_name: str | None = Column(String(180))
    category: str | None = Column(String(80))
    size_description: str | None = Column(String(80))
    price: float | None = Column(Numeric(14, 4))
    source_url: str | None = Column(Text)
    scraped_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )


class ProductCompetitorMatch(Base):
    """Manual correspondence between an own product and a competitor's product.

    This match is ALWAYS a human decision: no automated process inserts rows
    here. Because competitor_products is partitioned, the FK references its full
    composite PK (id, scraped_at), so this table carries
    competitor_product_scraped_at as part of the composite FK.
    """

    __tablename__ = "product_competitor_matches"

    __table_args__ = (
        UniqueConstraint(
            "our_product_id",
            "our_size_id",
            "competitor_product_id",
            name="uq_product_competitor_matches",
        ),
        ForeignKeyConstraint(
            ["competitor_product_id", "competitor_product_scraped_at"],
            ["competitor_products.id", "competitor_products.scraped_at"],
            name="fk_pcm_competitor_product",
            ondelete="CASCADE",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    our_product_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    our_size_id: int = Column(
        BigInteger, ForeignKey("product_sizes.id", ondelete="CASCADE"), nullable=False
    )
    competitor_product_id: int = Column(BigInteger, nullable=False)
    competitor_product_scraped_at: object = Column(
        DateTime(timezone=True), nullable=False
    )
    matched_by: str | None = Column(String(120))
    matched_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: str | None = Column(Text)
