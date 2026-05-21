"""
Configurable generic scraper — the key adapter in the scraping system.

ConfigurableScraper contains zero site-specific logic.  All behaviour
(selectors, navigation paths, rate limits, browser settings) comes from a
validated ScraperConfig loaded from YAML.

Architecture
------------
  YAML config
      │
      ▼
  ConfigLoader ──► ScraperConfig
                       │
                       ▼
               ConfigurableScraper
                 ├── SelectorEngine    (DOM extraction)
                 ├── NavigationEngine  (URL building + navigation)
                 ├── RateLimiter       (throttling + backoff)
                 └── PriceCleaner      (price string normalisation)

Browser lifecycle
-----------------
The browser is managed by the BaseScraper lifecycle hooks:
  - setup()    → launches Playwright + Chromium, stored on self._browser
  - teardown() → closes browser + Playwright context

Use the context manager (``with`` statement) or call setup/teardown
explicitly.  Never call scrape_product / search_products without setup().

Usage::

    loader = ConfigLoader("/app/backend/services/scraping/config")
    config = loader.load_config("competitor_001")

    with ConfigurableScraper(config) as scraper:
        products = scraper.search_products("cappuccino", limit=20)
        for p in products:
            print(p.product_name, p.price)

    # Or a one-shot run:
    result = ConfigurableScraper(config).run_full_scrape("café molido")
    print(result.to_summary())
"""

import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

# Railway inyecta RAILWAY_ENVIRONMENT en tiempo de ejecución.
# En local el valor es None, por lo que la detección es segura sin config extra.
_IS_RAILWAY: bool = os.getenv("RAILWAY_ENVIRONMENT") is not None

# Args de Chromium necesarios en entornos containerizados/serverless donde no
# hay usuario root con privilegios completos ni dispositivos /dev/shm amplios.
_RAILWAY_BROWSER_ARGS: list = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]

from playwright.sync_api import Browser, ElementHandle, Page, Playwright, sync_playwright

from .base_scraper import BaseScraper
from .exceptions import (
    ExtractionError,
    NavigationError,
    ScrapingException,
    SelectorError,
    ValidationError,
)
from .models import ScrapedProduct, ScraperConfig
from .navigation_engine import NavigationEngine
from .selector_engine import SelectorEngine
from ..utils.price_cleaner import PriceCleaner
from ..utils.rate_limiter import RateLimiter


class ConfigurableScraper(BaseScraper):
    """
    Generic scraper that adapts to any site via YAML configuration.

    Features
    --------
    - Product page scraping (single URL).
    - Keyword search with automatic pagination.
    - Category browsing (when navigation config includes category paths).
    - Per-field metadata extraction.
    - Integrated rate limiting with exponential backoff.
    - Retry logic for failed navigations.
    - Detailed logging at every extraction step.

    All selector keys referenced below (``product_name``, ``result_name``,
    etc.) must exist in the ``selectors`` section of the YAML config.
    Mark optional fields with ``optional: true`` to avoid SelectorError.

    Expected selector keys
    ~~~~~~~~~~~~~~~~~~~~~~
    Product page (used by :meth:`scrape_product`):
      - ``product_name``        — main product title (required)
      - ``product_price``       — price string (required)
      - ``product_unit``        — weight / volume label (optional)
      - ``product_category``    — category breadcrumb (optional)
      - ``product_description`` — description text (optional)
      - ``product_image``       — image element with ``attribute: src`` (optional)
      - ``metadata.*``          — any nested keys under ``metadata:`` (optional)

    Search results (used by :meth:`search_products`):
      - ``search_results``  — container elements (one per product card)
      - ``result_name``     — product name inside a card
      - ``result_price``    — price text inside a card
      - ``result_link``     — anchor inside a card (configure ``attribute: href``)
      - ``result_image``    — image inside a card (configure ``attribute: src``, optional)
    """

    def __init__(self, config: ScraperConfig) -> None:
        """
        Initialise the configurable scraper.

        The Playwright browser is NOT launched here; it is launched in
        :meth:`setup`.

        Args:
            config: Validated :class:`~.models.ScraperConfig` from ConfigLoader.
        """
        super().__init__(config)

        self.selector_engine = SelectorEngine(
            config.selectors,
            logger=self.logger,
            enable_cache=False,  # DOM mutates between pages; never cache.
        )

        self.navigation_engine = NavigationEngine(
            config.navigation,
            config.base_url,
            logger=self.logger,
        )

        self.rate_limiter = RateLimiter(
            config.rate_limiting,
            logger=self.logger,
        )

        self.price_cleaner = PriceCleaner(
            default_currency=config.metadata.get("currency", "COP"),
        )

        # Browser config shortcuts.
        browser_cfg: Dict[str, Any] = config.browser if isinstance(config.browser, dict) else {}
        self._headless: bool = browser_cfg.get("headless", True)
        self._user_agent: Optional[str] = browser_cfg.get("user_agent")
        self._timeout_ms: int = browser_cfg.get("timeout_ms", 30_000)
        self._viewport: Dict[str, int] = {
            "width": browser_cfg.get("viewport_width", 1280),
            "height": browser_cfg.get("viewport_height", 800),
        }

        # En Railway, forzar headless y agregar args necesarios para contenedor.
        if _IS_RAILWAY:
            self._headless = True
            self._extra_browser_args: list = _RAILWAY_BROWSER_ARGS
        else:
            self._extra_browser_args = []

        # Playwright handle — set by setup(), cleared by teardown().
        self._playwright: Optional[Playwright] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """
        Launch the Playwright browser and record execution start time.

        Called automatically by ``__enter__`` and :meth:`run_full_scrape`.
        Do not call :meth:`scrape_product` or :meth:`search_products` without
        first calling setup (or using the context manager).
        """
        super().setup()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=self._extra_browser_args,
        )
        self.logger.info(
            "[%s] Browser launched (headless=%s)", self.scraper_id, self._headless
        )

    def teardown(self) -> None:
        """
        Close the browser and stop the Playwright instance.

        Calls ``super().teardown()`` which closes ``self._browser``.
        """
        super().teardown()  # closes self._browser
        if self._playwright:
            try:
                self._playwright.stop()
                self.logger.debug("[%s] Playwright stopped", self.scraper_id)
            except Exception as exc:
                self.logger.warning("[%s] Error stopping Playwright: %s", self.scraper_id, exc)
            finally:
                self._playwright = None

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    def scrape_product(self, url: str) -> ScrapedProduct:
        """
        Navigate to a product page and extract all configured fields.

        Steps:
          1. Apply rate limit.
          2. Navigate with retry.
          3. Extract all configured fields via SelectorEngine.
          4. Clean and validate extracted data.
          5. Return ScrapedProduct.

        Args:
            url: Absolute URL of the product page.

        Returns:
            Populated :class:`~.models.ScrapedProduct`.

        Raises:
            NavigationError: Browser cannot reach the URL after retries.
            SelectorError:   A required selector finds no element.
            ValidationError: A required field is missing after extraction.

        Examples:
            >>> with ConfigurableScraper(config) as s:
            ...     product = s.scrape_product("https://site.com/product/42")
            ...     print(product.product_name, product.price)
        """
        self.logger.info("[%s] Scraping product: %s", self.scraper_id, url)
        page = self._new_page()
        try:
            self._navigate(page, url)
            data = self._extract_product_data(page, url)
            self._validate_required_fields(data)
            product = self._build_scraped_product(**data)
            self.rate_limiter.mark_success()
            self.logger.info("[%s] Scraped: %s @ %s", self.scraper_id, product.product_name, product.price)
            return product
        except ScrapingException:
            self.rate_limiter.mark_error()
            raise
        except Exception as exc:
            self.rate_limiter.mark_error()
            raise ExtractionError(
                f"Unexpected error scraping '{url}': {exc}",
                code="UNEXPECTED_EXTRACTION_ERROR",
                details={"url": url},
            ) from exc
        finally:
            page.close()

    def search_products(self, query: str, limit: int = 10) -> List[ScrapedProduct]:
        """
        Search the site and return up to *limit* products.

        Steps:
          1. Build search URL from config.
          2. Navigate to search results.
          3. Extract product cards from the results page.
          4. If more products are needed, follow pagination URLs.
          5. Return accumulated products (≤ limit).

        Args:
            query: Search term (e.g. ``'cappuccino'``, ``'café molido 500g'``).
            limit: Maximum products to return.

        Returns:
            List of :class:`~.models.ScrapedProduct` (length ≤ *limit*).

        Raises:
            NavigationError: Search URL unreachable after retries.

        Examples:
            >>> with ConfigurableScraper(config) as s:
            ...     products = s.search_products("café", limit=30)
            ...     print(len(products))
        """
        self.logger.info(
            "[%s] Searching: query='%s', limit=%d", self.scraper_id, query, limit
        )
        products: List[ScrapedProduct] = []
        search_url = self.navigation_engine.build_search_url(query)
        pagination_urls = self.navigation_engine.get_pagination_urls(search_url)

        page = self._new_page()
        try:
            for page_url in pagination_urls:
                if len(products) >= limit:
                    break

                self._navigate(page, page_url)
                remaining = limit - len(products)
                batch = self._extract_search_results(page, limit=remaining)
                products.extend(batch)

                self.logger.info(
                    "[%s] Page '%s': +%d products (total %d/%d)",
                    self.scraper_id, page_url, len(batch), len(products), limit,
                )

                if not batch:
                    self.logger.debug(
                        "[%s] Empty page — stopping pagination early", self.scraper_id
                    )
                    break

            self.rate_limiter.mark_success()
            return products[:limit]

        except ScrapingException:
            self.rate_limiter.mark_error()
            raise
        except Exception as exc:
            self.rate_limiter.mark_error()
            raise NavigationError(
                f"Unexpected error searching '{query}': {exc}",
                code="SEARCH_UNEXPECTED_ERROR",
                details={"query": query},
            ) from exc
        finally:
            page.close()

    def scrape_category(self, category: str, limit: int = 50) -> List[ScrapedProduct]:
        """
        Browse a category page and return up to *limit* products.

        Requires ``navigation.category`` to be configured in the YAML.
        Falls back to an empty list if no category config exists.

        Args:
            category: Category slug as it appears in the site URL.
            limit:    Maximum products to return.

        Returns:
            List of :class:`~.models.ScrapedProduct`.
        """
        if not self.config.navigation.get("category"):
            self.logger.debug(
                "[%s] No category navigation config — skipping", self.scraper_id
            )
            return []

        self.logger.info(
            "[%s] Scraping category='%s', limit=%d", self.scraper_id, category, limit
        )
        products: List[ScrapedProduct] = []
        category_url = self.navigation_engine.build_category_url(category)
        pagination_urls = self.navigation_engine.get_pagination_urls(category_url)

        page = self._new_page()
        try:
            for page_url in pagination_urls:
                if len(products) >= limit:
                    break
                self._navigate(page, page_url)
                remaining = limit - len(products)
                batch = self._extract_search_results(page, limit=remaining)
                products.extend(batch)
                if not batch:
                    break
            self.rate_limiter.mark_success()
            return products[:limit]
        except ScrapingException:
            self.rate_limiter.mark_error()
            raise
        finally:
            page.close()

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_product_data(self, page: Page, url: str) -> Dict[str, Any]:
        """
        Extract all configured fields from a product detail page.

        Required selectors: ``product_name``, ``product_price``.
        Optional selectors: ``product_unit``, ``product_category``,
        ``product_description``, ``product_image``, ``metadata.*``.

        Args:
            page: Playwright Page positioned at the product URL.
            url:  Product URL stored in the result metadata.

        Returns:
            Dict suitable for unpacking into :class:`~.models.ScrapedProduct`.
        """
        data: Dict[str, Any] = {"url": url}

        # --- Required fields ---
        data["product_name"] = self.selector_engine.extract_text(page, "product_name")

        price_text = self.selector_engine.extract_text(page, "product_price")
        data["price"] = self._safe_clean_price(price_text, url)

        # --- Optional fields ---
        data["unit"] = self.selector_engine.extract_text(
            page, "product_unit", default=None
        )
        data["category"] = self.selector_engine.extract_text(
            page, "product_category", default=None
        )
        data["description"] = self.selector_engine.extract_text(
            page, "product_description", default=None
        )
        data["image_url"] = self.selector_engine.extract_attribute(
            page, "product_image", "src", default=None
        )
        data["metadata"] = self._extract_metadata(page)

        self.logger.debug(
            "[%s] Extracted product data: name=%r price=%s",
            self.scraper_id, data.get("product_name"), data.get("price"),
        )
        return data

    def _extract_search_results(
        self, page: Page, limit: int
    ) -> List[ScrapedProduct]:
        """
        Extract product cards from a search-results or category page.

        Args:
            page:  Playwright Page positioned at the results URL.
            limit: Maximum cards to extract from this page.

        Returns:
            List of :class:`~.models.ScrapedProduct`.
        """
        elements = self.selector_engine.extract_elements(
            page, "search_results", limit=limit
        )
        self.logger.debug(
            "[%s] %d result elements found", self.scraper_id, len(elements)
        )

        products: List[ScrapedProduct] = []
        for i, element in enumerate(elements):
            if len(products) >= limit:
                break
            try:
                data = self._extract_result_item(element)
                product = self._build_scraped_product(**data)
                products.append(product)
            except SelectorError as exc:
                self.logger.warning(
                    "[%s] Skipping result #%d — selector error: %s", self.scraper_id, i, exc
                )
            except ExtractionError as exc:
                self.logger.warning(
                    "[%s] Skipping result #%d — extraction error: %s", self.scraper_id, i, exc
                )
            except Exception as exc:
                self.logger.warning(
                    "[%s] Skipping result #%d — unexpected error: %s", self.scraper_id, i, exc
                )

        return products

    def _extract_result_item(self, element: ElementHandle) -> Dict[str, Any]:
        """
        Extract data from a single search-result card element.

        Uses :meth:`~.selector_engine.SelectorEngine.extract_from_element`
        so all queries are scoped to the card, not the full page.

        Args:
            element: Playwright ElementHandle for one product card.

        Returns:
            Dict with at minimum ``product_name`` and ``price``.
        """
        data: Dict[str, Any] = {}

        # Name.
        data["product_name"] = self.selector_engine.extract_from_element(
            element, "result_name", default="Unknown"
        )

        # Price.
        price_text = self.selector_engine.extract_from_element(
            element, "result_price", default="0"
        )
        data["price"] = self._safe_clean_price(price_text, context="result_item")

        # Product page URL — selector config should set attribute: href.
        href = self.selector_engine.extract_from_element(
            element, "result_link", default=None
        )
        if href:
            data["url"] = href if href.startswith("http") else urljoin(self.base_url + "/", href)

        # Image — selector config should set attribute: src.
        image_src = self.selector_engine.extract_from_element(
            element, "result_image", default=None
        )
        if image_src:
            data["image_url"] = (
                image_src if image_src.startswith("http")
                else urljoin(self.base_url + "/", image_src)
            )

        return data

    def _extract_metadata(self, page: Page) -> Dict[str, Any]:
        """
        Extract all ``metadata.*`` fields defined in the selector config.

        Any extraction failure on a metadata field is silently skipped —
        metadata is always considered optional.

        Args:
            page: Playwright Page.

        Returns:
            Dict of ``{key: extracted_value}`` for all metadata selectors.
        """
        metadata: Dict[str, Any] = {}
        meta_selectors = self.config.selectors.get("metadata", {})
        if not isinstance(meta_selectors, dict):
            return metadata

        for key in meta_selectors:
            try:
                value = self.selector_engine.extract_text(
                    page, f"metadata.{key}", default=None
                )
                if value is not None:
                    metadata[key] = value
            except Exception as exc:
                self.logger.debug(
                    "[%s] Metadata field '%s' skipped: %s", self.scraper_id, key, exc
                )

        return metadata

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    # Maps selector-config names to the actual key used in the extracted data dict.
    _FIELD_KEY_MAP: Dict[str, str] = {"product_price": "price"}

    def _validate_required_fields(self, data: Dict[str, Any]) -> None:
        """
        Verify that every field listed in ``config.required_fields`` is
        present and non-empty in *data*.

        Args:
            data: Extracted product data dict.

        Raises:
            ValidationError: The first missing required field found.
        """
        for field_name in self.config.required_fields:
            # Check the aliased model key first (e.g. 'price' for 'product_price'),
            # then fall back to the selector name itself.
            aliased_key = self._FIELD_KEY_MAP.get(field_name, field_name)
            value = data.get(aliased_key)
            if value is None:
                value = data.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValidationError(
                    f"Required field '{field_name}' is missing or empty",
                    code="REQUIRED_FIELD_MISSING",
                    details={"field": field_name, "scraper_id": self.scraper_id},
                )

    # ------------------------------------------------------------------
    # Internal browser helpers
    # ------------------------------------------------------------------

    def _new_page(self) -> Page:
        """
        Create a new Playwright Page from the managed browser.

        Applies viewport and user-agent from browser config.

        Returns:
            Configured Playwright Page.

        Raises:
            ScrapingException: Browser has not been launched (setup() not called).
        """
        if self._browser is None:
            raise ScrapingException(
                "Browser is not running — call setup() or use the context manager",
                code="BROWSER_NOT_INITIALIZED",
            )
        kwargs: Dict[str, Any] = {"viewport": self._viewport}
        if self._user_agent:
            kwargs["user_agent"] = self._user_agent

        page = self._browser.new_page(**kwargs)
        page.set_default_timeout(self._timeout_ms)
        return page

    def _navigate(self, page: Page, url: str) -> None:
        """
        Apply rate limit, navigate to *url* with retry, and increment the
        request counter.

        Args:
            page: Playwright Page.
            url:  Absolute URL to navigate to.

        Raises:
            NavigationError: Navigation fails after all retries.
        """
        self.rate_limiter.wait()
        self.navigation_engine.navigate_with_retry(
            page, url, wait_until="networkidle"
        )
        self._increment_request_count()

    def _safe_clean_price(
        self, price_text: Optional[str], context: str = ""
    ) -> Decimal:
        """
        Clean *price_text* via PriceCleaner, converting failures to
        ExtractionError instead of letting them propagate as bare exceptions.

        Args:
            price_text: Raw price string (may be None or empty).
            context:    URL or label for error details.

        Returns:
            Decimal price (Decimal('0') when text is empty and field is optional).

        Raises:
            ExtractionError: Price cannot be parsed.
        """
        if not price_text or not price_text.strip():
            return Decimal("0")
        try:
            return self.price_cleaner.clean(price_text)
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(
                f"Price parse failed: '{price_text}'",
                code="PRICE_PARSE_FAILED",
                details={"raw": price_text, "context": context},
            ) from exc
