"""
Pydantic v2 schemas for scraping API endpoints.

Organization:
  Enums       — ScraperBusinessType, ScraperType
  Requests    — ScrapeIngredientRequest, ScrapeIngredientsBatchRequest,
                ScrapeCompetitorMenuRequest, TestScraperRequest
  Responses   — ScraperInfoResponse, ScrapedProductResponse,
                ScrapeIngredientResponse, ScrapeIngredientsBatchResponse,
                ScrapeCompetitorMenuResponse, TestScraperResponse,
                ScraperStatusResponse
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================
# ENUMS
# ============================================

class ScraperBusinessType(str, Enum):
    """Business type of the scraper."""
    COMPETITOR = "competitor"
    SUPPLIER = "supplier"


class ScraperType(str, Enum):
    """Technical category of the scraped site."""
    RESTAURANT = "restaurant"
    RETAIL = "retail"
    MARKETPLACE = "marketplace"
    CUSTOM = "custom"


# ============================================
# REQUEST SCHEMAS
# ============================================

class ScrapeIngredientRequest(BaseModel):
    """
    Requests the scraping of the current price of an ingredient.

    If ``update_db`` is True and the price changed, the system updates
    ``ingredient.purchase_price`` and adds a row to
    ``ingredient_price_history``.
    """

    ingredient_id: int = Field(
        ...,
        gt=0,
        description="ID of the ingredient in the database.",
    )
    update_db: bool = Field(
        True,
        description="If True, persists the new price in DB when it changes.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ingredient_id": 1,
                "update_db": True,
            }
        }
    )


class ScrapeIngredientsBatchRequest(BaseModel):
    """
    Requests the simultaneous scraping of multiple ingredients.

    Maximum 100 IDs per request to avoid endpoint timeouts.
    The system processes ingredients sequentially (not in parallel).
    """

    ingredient_ids: List[int] = Field(
        ...,
        description="IDs of the ingredients to scrape. Between 1 and 100.",
    )
    update_db: bool = Field(
        True,
        description="If True, persists the updated prices in DB.",
    )
    supplier_only: bool = Field(
        False,
        description=(
            "If True, skips ingredients whose scraper is of type 'competitor'. "
            "Useful for updating only supply costs without touching competitive analysis."
        ),
    )

    @field_validator("ingredient_ids")
    @classmethod
    def validate_ids(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("ingredient_ids cannot be empty")
        if len(v) > 100:
            raise ValueError("Maximum 100 ingredients per batch")
        if any(i <= 0 for i in v):
            raise ValueError("All IDs must be > 0")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ingredient_ids": [1, 2, 3, 4, 5],
                "update_db": True,
                "supplier_only": False,
            }
        }
    )


class ScrapeCompetitorMenuRequest(BaseModel):
    """
    Requests the scraping of a competitor's menu.

    The system searches for products using each term in ``search_queries``
    and persists the results in ``competitor_products``.  If ``search_queries``
    is None, the system's default terms are used (coffee, latte, etc.).
    """

    competitor_id: int = Field(
        ...,
        gt=0,
        description="ID of the competitor in the database.",
    )
    search_queries: Optional[List[str]] = Field(
        None,
        description=(
            "Search terms. If None, uses the system's default searches "
            "(cappuccino, latte, americano, …)."
        ),
    )
    limit_per_query: int = Field(
        10,
        ge=1,
        le=50,
        description="Maximum number of products to extract per search term.",
    )

    @field_validator("search_queries")
    @classmethod
    def validate_queries(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            if not v:
                raise ValueError("search_queries cannot be an empty list; use None for defaults")
            if any(not q.strip() for q in v):
                raise ValueError("No search term can be empty")
            if len(v) > 20:
                raise ValueError("Maximum 20 search terms per request")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "competitor_id": 1,
                "search_queries": ["cappuccino", "latte", "americano"],
                "limit_per_query": 10,
            }
        }
    )


class TestScraperRequest(BaseModel):
    """
    Runs a scraper in test mode without writing to the database.

    Useful for validating that the CSS/XPath selectors of a newly configured
    scraper work correctly before activating it in production.
    Returns a sample of products without persisting them.
    """

    scraper_id: str = Field(
        ...,
        min_length=1,
        description="ID of the scraper to test (e.g.: 'competitor_001').",
    )
    search_query: str = Field(
        "coffee",
        min_length=1,
        description="Test search term.",
    )
    limit: int = Field(
        3,
        ge=1,
        le=10,
        description="Maximum number of products to return in the test.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "scraper_id": "competitor_001",
                "search_query": "coffee",
                "limit": 3,
            }
        }
    )


# ============================================
# RESPONSE SCHEMAS
# ============================================

class ScraperInfoResponse(BaseModel):
    """Metadata for a scraper configured in the system."""

    id: str = Field(description="Unique scraper identifier (YAML filename without extension).")
    name: str = Field(description="Human-readable name of the scraped business.")
    type: ScraperBusinessType = Field(description="'competitor' or 'supplier'.")
    scraper_type: ScraperType = Field(description="Technical category of the site.")
    base_url: str = Field(description="Root URL of the scraped site.")
    enabled: bool = Field(description="Whether the scraper is active.")
    priority: Optional[int] = Field(None, description="Execution order in batch (lower = first).")
    schedule: Optional[str] = Field(None, description="Suggested frequency: 'daily', 'weekly', 'monthly'.")
    notes: Optional[str] = Field(None, description="Operator notes about this scraper.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "competitor_001",
                "name": "Competitor Cafeteria A",
                "type": "competitor",
                "scraper_type": "restaurant",
                "base_url": "https://example-competitor.com",
                "enabled": True,
                "priority": 1,
                "schedule": "weekly",
                "notes": "Update selectors if the site changes layout",
            }
        }
    )


class ScrapedProductResponse(BaseModel):
    """An individual product extracted during a scraping run."""

    product_name: str = Field(description="Product name as it appears on the site.")
    price: Decimal = Field(description="Price in the indicated currency.")
    currency: str = Field(default="COP", description="ISO 4217 currency code.")
    unit: Optional[str] = Field(None, description="Unit / size (e.g.: '500g', '12oz').")
    category: Optional[str] = Field(None, description="Category reported by the site.")
    url: Optional[str] = Field(None, description="URL of the product page.")
    image_url: Optional[str] = Field(None, description="URL of the product image.")
    availability: bool = Field(default=True, description="False if the site indicates out of stock.")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Scraper-specific extra fields (rating, reviews, etc.).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "product_name": "Cappuccino Grande",
                "price": "8500.00",
                "currency": "COP",
                "unit": "16oz",
                "category": "Hot Drinks",
                "url": "https://example-competitor.com/menu/cappuccino",
                "image_url": "https://cdn.example.com/img/cappuccino.jpg",
                "availability": True,
                "metadata": {"rating": "4.5", "reviews_count": "128"},
            }
        }
    )


class ScrapeIngredientResponse(BaseModel):
    """
    Result of scraping an individual ingredient.

    ``success=False`` does not raise an HTTP error; the error is in the
    ``error`` field so that a batch can report mixed results.
    """

    success: bool
    ingredient_id: Optional[int] = None
    ingredient_name: Optional[str] = None
    old_price: Optional[Decimal] = Field(None, description="Price before scraping.")
    new_price: Optional[Decimal] = Field(None, description="Price found on the site.")
    price_change: Optional[Decimal] = Field(None, description="new_price − old_price.")
    price_change_pct: Optional[float] = Field(
        None, description="Percentage change relative to the previous price."
    )
    scraper_id: Optional[str] = None
    business_name: Optional[str] = None
    scraped_at: Optional[datetime] = None
    updated_db: bool = Field(default=False, description="True if the price was persisted in DB.")
    error: Optional[str] = Field(None, description="Error message if success=False.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "ingredient_id": 1,
                "ingredient_name": "Whole milk Alpina 1L",
                "old_price": "4500.00",
                "new_price": "4800.00",
                "price_change": "300.00",
                "price_change_pct": 6.67,
                "scraper_id": "supplier_001",
                "business_name": "Retail Supplier A",
                "scraped_at": "2025-05-20T10:30:00",
                "updated_db": True,
                "error": None,
            }
        }
    )


class ScrapeIngredientsBatchResponse(BaseModel):
    """Aggregated result of an ingredient scraping batch."""

    total: int = Field(description="Total ingredients processed.")
    success: int = Field(description="Successful scrapers.")
    failed: int = Field(description="Failed scrapers.")
    skipped: int = Field(description="Skipped ingredients (e.g.: supplier_only filter).")
    results: List[ScrapeIngredientResponse] = Field(
        description="Individual result per ingredient."
    )
    execution_time_ms: float = Field(description="Total execution time in milliseconds.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total": 5,
                "success": 4,
                "failed": 1,
                "skipped": 0,
                "results": [],
                "execution_time_ms": 22345.6,
            }
        }
    )


class ScrapeCompetitorMenuResponse(BaseModel):
    """Result of scraping a competitor's menu."""

    success: bool
    competitor_id: Optional[int] = None
    competitor_name: Optional[str] = None
    scraper_id: Optional[str] = None
    business_name: Optional[str] = None
    total_products_found: int = Field(
        default=0,
        description="Products extracted from the site in this run.",
    )
    new_products: int = Field(
        default=0,
        description="Products inserted for the first time into competitor_products.",
    )
    updated_products: int = Field(
        default=0,
        description="Products whose price was updated.",
    )
    errors: List[str] = Field(
        default_factory=list,
        description="Query or DB errors (non-fatal).",
    )
    execution_time_ms: float = Field(default=0.0)
    error: Optional[str] = Field(None, description="Fatal error if success=False.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "competitor_id": 1,
                "competitor_name": "Competitor Cafeteria A",
                "scraper_id": "competitor_001",
                "business_name": "Competitor Cafeteria A",
                "total_products_found": 25,
                "new_products": 3,
                "updated_products": 22,
                "errors": [],
                "execution_time_ms": 15234.5,
                "error": None,
            }
        }
    )


class TestScraperResponse(BaseModel):
    """
    Result of a scraper test without writing to DB.

    Includes the full list of extracted products (up to ``limit``)
    so that the operator can verify the data is correct.
    """

    success: bool
    scraper_id: str
    business_name: Optional[str] = None
    products_found: int = Field(default=0)
    products: List[ScrapedProductResponse] = Field(default_factory=list)
    execution_time_ms: float = Field(default=0.0)
    error: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "scraper_id": "competitor_001",
                "business_name": "Competitor Cafeteria A",
                "products_found": 3,
                "products": [
                    {
                        "product_name": "Cappuccino Grande",
                        "price": "8500.00",
                        "currency": "COP",
                        "unit": "16oz",
                        "category": "Hot Drinks",
                        "url": None,
                        "image_url": None,
                        "availability": True,
                        "metadata": {},
                    }
                ],
                "execution_time_ms": 3456.7,
                "error": None,
            }
        }
    )


class ScraperStatusResponse(BaseModel):
    """
    Historical execution status of a scraper.

    This schema is for a future monitoring endpoint; data comes from
    logs / audit tables, not from the live scraper.
    """

    scraper_id: str
    enabled: bool
    last_execution: Optional[datetime] = Field(
        None, description="Timestamp of the last completed execution."
    )
    total_executions: int = Field(default=0, description="Total executions recorded.")
    success_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Percentage of successful executions (0–100).",
    )
    average_execution_time_ms: Optional[float] = Field(
        None, description="Average duration of successful executions."
    )
    last_error: Optional[str] = Field(
        None, description="Message of the last recorded error, or None if there is none."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "scraper_id": "competitor_001",
                "enabled": True,
                "last_execution": "2025-05-20T09:15:00",
                "total_executions": 145,
                "success_rate": 94.5,
                "average_execution_time_ms": 8234.6,
                "last_error": None,
            }
        }
    )
