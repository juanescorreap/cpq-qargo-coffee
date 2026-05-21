from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
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

    Example:
        name="Juan Valdez", website_url="https://juanvaldezcafe.com"
        name="Starbucks",   website_url="https://starbucks.com.co"
    """

    __tablename__ = "competitors"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    website_url: str | None = Column(Text)
    is_active: bool = Column(Boolean, default=True)


class CompetitorProduct(Base):
    """Product scraped from a competitor's menu.

    Each row is a snapshot of a product as it appears published on the
    competitor's site at the time of scraping. It is not normalized or
    interpreted: product_name and size_description are stored exactly as they
    come from the source to preserve fidelity to the original data.

    scraped_at allows building price time series per competitor and detecting
    price changes between successive scrapes.

    source_url points to the exact page or endpoint where the data was found,
    making manual verification and scraper debugging easier.

    Example:
        competitor_id=1 (Juan Valdez), product_name="Cappuccino",
        size_description="12oz", price=12900.00,
        source_url="https://juanvaldezcafe.com/menu/hot-drinks"
    """

    __tablename__ = "competitor_products"

    id: int = Column(Integer, primary_key=True, index=True)
    competitor_id: int = Column(
        Integer, ForeignKey("competitors.id"), nullable=False, index=True
    )
    product_name: str | None = Column(String(200))
    category: str | None = Column(String(100))
    size_description: str | None = Column(String(100))
    price: float | None = Column(Numeric(10, 2))
    scraped_at: object = Column(DateTime(timezone=True), server_default=func.now())
    source_url: str | None = Column(Text)


class ProductCompetitorMatch(Base):
    """Manual correspondence between an own product and a competitor's product.

    This match is ALWAYS a human decision: no automated process inserts rows
    here. The user evaluates whether two products are comparable (size,
    preparation, target market) and records the match with their name and a
    justification in notes.

    The competitive analysis engine uses this table to calculate price gaps
    between own products and their competitor equivalents.

    The unique constraint on (our_product_id, our_size_id, competitor_product_id)
    prevents duplicating the same pair, but an own product can have multiple
    matches against different competitors.

    Example:
        our_product_id=5 (medium Cappuccino 12oz) ↔
        competitor_product_id=42 (Juan Valdez Cappuccino 12oz)
        matched_by="carlos", notes="Same size and standard preparation"
    """

    __tablename__ = "product_competitor_matches"

    __table_args__ = (
        UniqueConstraint(
            "our_product_id",
            "our_size_id",
            "competitor_product_id",
            name="uq_product_competitor_match",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    our_product_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False
    )
    our_size_id: int = Column(
        Integer, ForeignKey("product_sizes.id"), nullable=False
    )
    competitor_product_id: int = Column(
        Integer, ForeignKey("competitor_products.id"), nullable=False
    )
    matched_by: str | None = Column(String(100))
    matched_at: object = Column(DateTime(timezone=True), server_default=func.now())
    notes: str | None = Column(Text)
