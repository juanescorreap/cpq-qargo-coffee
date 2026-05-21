"""
Dataclasses for structuring scraped data across the scraping system.

These models are the canonical data transfer objects (DTOs) between scrapers,
extractors, validators, and the rest of the application.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BusinessType(str, Enum):
    COMPETITOR = "competitor"
    SUPPLIER = "supplier"


class ScraperType(str, Enum):
    RESTAURANT = "restaurant"
    RETAIL = "retail"
    MARKETPLACE = "marketplace"
    CUSTOM = "custom"


class SelectorType(str, Enum):
    CSS = "css"
    XPATH = "xpath"


# ---------------------------------------------------------------------------
# SelectorConfig
# ---------------------------------------------------------------------------

@dataclass
class SelectorConfig:
    """
    Defines how to locate and extract a single piece of data from the DOM.

    Attributes:
        selector:  CSS selector string or XPath expression.
        type:      Whether `selector` is a CSS selector or XPath expression.
        attribute: If set, extract this HTML attribute instead of inner text
                   (e.g. 'href', 'src', 'data-price').
        optional:  When True, missing elements do not raise SelectorError.
        multiple:  When True, returns a list of all matching elements instead
                   of the first one.
    """

    selector: str
    type: str = SelectorType.CSS
    attribute: Optional[str] = None
    optional: bool = False
    multiple: bool = False

    def __post_init__(self) -> None:
        if not self.selector or not self.selector.strip():
            raise ValueError("selector cannot be empty")
        valid_types = {t.value for t in SelectorType}
        if self.type not in valid_types:
            raise ValueError(
                f"type must be one of {sorted(valid_types)}, got '{self.type}'"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selector": self.selector,
            "type": self.type,
            "attribute": self.attribute,
            "optional": self.optional,
            "multiple": self.multiple,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelectorConfig":
        return cls(
            selector=data["selector"],
            type=data.get("type", SelectorType.CSS),
            attribute=data.get("attribute"),
            optional=data.get("optional", False),
            multiple=data.get("multiple", False),
        )


# ---------------------------------------------------------------------------
# ScraperConfig
# ---------------------------------------------------------------------------

@dataclass
class ScraperConfig:
    """
    Full configuration for a single scraper, typically loaded from a YAML file.

    Attributes:
        scraper_id:      Unique identifier used to look up this scraper.
        business_name:   Human-readable name of the business being scraped.
        business_type:   'competitor' or 'supplier'.
        scraper_type:    Broad category of the site being scraped.
        base_url:        Root URL; scrapers build absolute URLs from this.
        selectors:       Mapping of field name → raw selector dict
                         (converted to SelectorConfig at runtime).
        navigation:      Instructions for multi-step navigation (pagination,
                         category traversal, login flows, etc.).
        browser:         Playwright / browser launch options (headless, timeout,
                         viewport, user_agent, …).
        rate_limiting:   Throttling parameters (delay_ms, max_retries,
                         retry_backoff_ms, …).
        required_fields: Fields that must be present for a product to be valid.
        enabled:         Set to False to skip this scraper in scheduled runs.
        metadata:        Arbitrary extra data (tags, owner, last_review, …).
    """

    scraper_id: str
    business_name: str
    business_type: str
    scraper_type: str
    base_url: str
    selectors: Dict[str, Any]
    navigation: Dict[str, Any]
    browser: Dict[str, Any]
    rate_limiting: Dict[str, Any]
    required_fields: List[str] = field(default_factory=list)
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.scraper_id or not self.scraper_id.strip():
            raise ValueError("scraper_id cannot be empty")
        if not self.business_name or not self.business_name.strip():
            raise ValueError("business_name cannot be empty")
        if not self.base_url or not self.base_url.strip():
            raise ValueError("base_url cannot be empty")

        valid_business = {t.value for t in BusinessType}
        if self.business_type not in valid_business:
            raise ValueError(
                f"business_type must be one of {sorted(valid_business)}, "
                f"got '{self.business_type}'"
            )

        valid_scraper = {t.value for t in ScraperType}
        if self.scraper_type not in valid_scraper:
            raise ValueError(
                f"scraper_type must be one of {sorted(valid_scraper)}, "
                f"got '{self.scraper_type}'"
            )

    def validate(self) -> List[str]:
        """
        Verify that all required_fields have a corresponding selector entry.

        Returns a list of missing field names (empty list means valid).
        """
        return [f for f in self.required_fields if f not in self.selectors]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scraper_id": self.scraper_id,
            "business_name": self.business_name,
            "business_type": self.business_type,
            "scraper_type": self.scraper_type,
            "base_url": self.base_url,
            "selectors": self.selectors,
            "navigation": self.navigation,
            "browser": self.browser,
            "rate_limiting": self.rate_limiting,
            "required_fields": self.required_fields,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScraperConfig":
        return cls(
            scraper_id=data["scraper_id"],
            business_name=data["business_name"],
            business_type=data["business_type"],
            scraper_type=data["scraper_type"],
            base_url=data["base_url"],
            selectors=data.get("selectors", {}),
            navigation=data.get("navigation", {}),
            browser=data.get("browser", {}),
            rate_limiting=data.get("rate_limiting", {}),
            required_fields=data.get("required_fields", []),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# ScrapedProduct
# ---------------------------------------------------------------------------

@dataclass
class ScrapedProduct:
    """
    A single product extracted from a scraping run.

    Attributes:
        source_id:     Scraper ID that produced this record.
        business_name: Name of the scraped business.
        product_name:  Display name of the product.
        price:         Decimal price (never negative).
        currency:      ISO 4217 currency code; defaults to 'COP'.
        unit:          Package or serving size label ('12oz', '500g', …).
        category:      Product category as reported by the source site.
        url:           Canonical product page URL.
        image_url:     Primary product image URL.
        description:   Raw product description text.
        availability:  False when the site marks the item as out of stock.
        metadata:      Scraper-specific extra fields (promotions, ratings, …).
        scraped_at:    UTC timestamp of extraction.
    """

    source_id: str
    business_name: str
    product_name: str
    price: Decimal
    currency: str = "COP"
    unit: Optional[str] = None
    category: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    description: Optional[str] = None
    availability: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    scraped_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise ValueError("source_id cannot be empty")
        if not self.product_name or not self.product_name.strip():
            raise ValueError("product_name cannot be empty")

        # Coerce str/float → Decimal so callers can pass raw parsed values.
        if not isinstance(self.price, Decimal):
            try:
                self.price = Decimal(str(self.price))
            except InvalidOperation as exc:
                raise ValueError(
                    f"price must be a valid number, got '{self.price}'"
                ) from exc

        if self.price < Decimal("0"):
            raise ValueError(f"price must be >= 0, got {self.price}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "business_name": self.business_name,
            "product_name": self.product_name,
            "price": str(self.price),
            "currency": self.currency,
            "unit": self.unit,
            "category": self.category,
            "url": self.url,
            "image_url": self.image_url,
            "description": self.description,
            "availability": self.availability,
            "metadata": self.metadata,
            "scraped_at": self.scraped_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScrapedProduct":
        return cls(
            source_id=data["source_id"],
            business_name=data["business_name"],
            product_name=data["product_name"],
            price=Decimal(data["price"]),
            currency=data.get("currency", "COP"),
            unit=data.get("unit"),
            category=data.get("category"),
            url=data.get("url"),
            image_url=data.get("image_url"),
            description=data.get("description"),
            availability=data.get("availability", True),
            metadata=data.get("metadata", {}),
            scraped_at=datetime.fromisoformat(data["scraped_at"])
            if "scraped_at" in data
            else datetime.now(),
        )


# ---------------------------------------------------------------------------
# ScrapingResult
# ---------------------------------------------------------------------------

@dataclass
class ScrapingResult:
    """
    Outcome of a single scraper execution.

    Attributes:
        scraper_id:        ID of the scraper that ran.
        success:           True if the run completed without fatal errors.
        products:          All products extracted during the run.
        error:             Human-readable error message on failure.
        error_code:        Machine-readable code from ScrapingException.
        execution_time_ms: Wall-clock duration of the run in milliseconds.
        timestamp:         UTC time when the run was recorded.
    """

    scraper_id: str
    success: bool
    products: List[ScrapedProduct] = field(default_factory=list)
    error: Optional[str] = None
    error_code: Optional[str] = None
    execution_time_ms: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_summary(self) -> Dict[str, Any]:
        """Return a lightweight statistics dict suitable for logging/dashboards."""
        prices = [p.price for p in self.products if p.availability]
        return {
            "scraper_id": self.scraper_id,
            "success": self.success,
            "total_products": len(self.products),
            "available_products": len(prices),
            "avg_price": str(sum(prices) / len(prices)) if prices else None,
            "min_price": str(min(prices)) if prices else None,
            "max_price": str(max(prices)) if prices else None,
            "error_code": self.error_code,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scraper_id": self.scraper_id,
            "success": self.success,
            "products": [p.to_dict() for p in self.products],
            "error": self.error,
            "error_code": self.error_code,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScrapingResult":
        return cls(
            scraper_id=data["scraper_id"],
            success=data["success"],
            products=[ScrapedProduct.from_dict(p) for p in data.get("products", [])],
            error=data.get("error"),
            error_code=data.get("error_code"),
            execution_time_ms=data.get("execution_time_ms"),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.now(),
        )
