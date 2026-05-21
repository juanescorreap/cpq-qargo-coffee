"""
Abstract base class for all scrapers in the scraping system.

Every concrete scraper must inherit from BaseScraper and implement at minimum
:meth:`scrape_product` and :meth:`search_products`.  The base class handles
lifecycle management, request counting, execution timing, and provides
context-manager support so browser resources are always released.

Usage example::

    class MyCoffeeScraper(BaseScraper):
        def scrape_product(self, url: str) -> ScrapedProduct:
            ...
        def search_products(self, query: str, limit: int = 10) -> List[ScrapedProduct]:
            ...

    with MyCoffeeScraper(config) as scraper:
        products = scraper.search_products("cappuccino", limit=20)
        # teardown() is called automatically on exit
"""

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from playwright.sync_api import Browser, Page

from .exceptions import ScrapingException
from .models import ScrapedProduct, ScraperConfig, ScrapingResult


class BaseScraper(ABC):
    """
    Abstract base for all scrapers.

    Defines the common interface and provides shared utilities so concrete
    scrapers stay focused on site-specific extraction logic.

    Lifecycle
    ---------
    1. ``__init__``   — receives a validated :class:`~.models.ScraperConfig`.
    2. ``setup()``    — called once before scraping starts; override for
                        browser / session initialisation.
    3. ``scrape_*()``,``search_products()`` — the actual scraping operations.
    4. ``teardown()`` — called once after scraping ends (also called by the
                        context manager on any exception); override for cleanup.

    Context manager
    ---------------
    The preferred usage pattern is::

        with MyCoffeeScraper(config) as scraper:
            result = scraper.run_full_scrape("coffee")

    ``__enter__`` calls :meth:`setup` and ``__exit__`` calls :meth:`teardown`
    regardless of whether an exception was raised.

    Request counting
    ----------------
    Concrete scrapers should call :meth:`_increment_request_count` after each
    network request so that :meth:`get_stats` returns accurate numbers.
    """

    def __init__(self, config: ScraperConfig) -> None:
        """
        Initialise the scraper with a validated config.

        Args:
            config: Fully-validated :class:`~.models.ScraperConfig` as returned
                    by :class:`~..utils.config_loader.ConfigLoader`.

        Examples:
            >>> cfg = loader.load_config("competitor_a")
            >>> scraper = MyCoffeeScraper(cfg)
            >>> scraper.scraper_id
            'competitor_a'
        """
        self.config = config
        self.scraper_id: str = config.scraper_id
        self.business_name: str = config.business_name
        self.base_url: str = config.base_url

        # Internal state — managed by lifecycle methods.
        self._browser: Optional[Browser] = None
        self._execution_start: Optional[float] = None
        self._request_count: int = 0

        self.logger: logging.Logger = self._create_default_logger()

    # ------------------------------------------------------------------
    # Abstract interface — must be implemented by every scraper
    # ------------------------------------------------------------------

    @abstractmethod
    def scrape_product(self, url: str) -> ScrapedProduct:
        """
        Scrape a single product page and return a structured result.

        Args:
            url: Absolute URL of the product page.

        Returns:
            :class:`~.models.ScrapedProduct` with all available fields filled.

        Raises:
            NavigationError:  Browser cannot reach the URL.
            SelectorError:    A required element is not found on the page.
            ExtractionError:  An element is found but data cannot be parsed.
            ScrapingException: Any other scraping failure.

        Examples:
            >>> product = scraper.scrape_product("https://site.com/product/123")
            >>> product.price
            Decimal('15000')
        """

    @abstractmethod
    def search_products(self, query: str, limit: int = 10) -> List[ScrapedProduct]:
        """
        Search the site for *query* and return up to *limit* products.

        Args:
            query: Search term (e.g. ``'cappuccino'``, ``'café molido 500g'``).
            limit: Maximum number of products to return.  Implementations
                   should stop scraping additional pages once this count is
                   reached to avoid unnecessary requests.

        Returns:
            List of :class:`~.models.ScrapedProduct`.  May be shorter than
            *limit* if the site returns fewer results.

        Raises:
            ScrapingException: Search navigation or extraction failed.

        Examples:
            >>> products = scraper.search_products("coffee", limit=5)
            >>> len(products) <= 5
            True
        """

    # ------------------------------------------------------------------
    # Optional override — default implementation returns empty list
    # ------------------------------------------------------------------

    def scrape_category(self, category: str, limit: int = 50) -> List[ScrapedProduct]:
        """
        Scrape all products in a category.

        The default implementation returns an empty list.  Override when the
        site exposes browsable category pages.

        Args:
            category: Category name or slug as it appears in the site URL
                      (e.g. ``'cafe-molido'``, ``'bebidas-calientes'``).
            limit:    Maximum number of products to return.

        Returns:
            List of :class:`~.models.ScrapedProduct`.

        Examples:
            >>> products = scraper.scrape_category("cafe-molido", limit=30)
        """
        return []

    # ------------------------------------------------------------------
    # High-level orchestration helper
    # ------------------------------------------------------------------

    def run_full_scrape(
        self,
        query: str,
        limit: int = 50,
    ) -> ScrapingResult:
        """
        Execute a full search scrape and wrap the outcome in a
        :class:`~.models.ScrapingResult`.

        Calls :meth:`setup`, :meth:`search_products`, and :meth:`teardown`
        in the correct order, capturing timing and errors.

        .. note::
            Prefer using the context manager (``with`` statement) when you
            need fine-grained control.  Use this method for simple one-shot
            runs.

        Args:
            query: Search term passed to :meth:`search_products`.
            limit: Maximum products to scrape.

        Returns:
            :class:`~.models.ScrapingResult` with ``success=True`` on
            success or ``success=False`` with error details on failure.

        Examples:
            >>> result = scraper.run_full_scrape("cappuccino", limit=20)
            >>> result.success
            True
            >>> print(result.to_summary())
        """
        self.setup()
        start = time.monotonic()
        try:
            products = self.search_products(query, limit=limit)
            elapsed_ms = (time.monotonic() - start) * 1_000
            self.logger.info(
                "[%s] Scraped %d products in %.0fms",
                self.scraper_id,
                len(products),
                elapsed_ms,
            )
            return ScrapingResult(
                scraper_id=self.scraper_id,
                success=True,
                products=products,
                execution_time_ms=elapsed_ms,
                timestamp=datetime.now(),
            )
        except ScrapingException as exc:
            elapsed_ms = (time.monotonic() - start) * 1_000
            self.logger.error(
                "[%s] Scrape failed after %.0fms: %s", self.scraper_id, elapsed_ms, exc
            )
            return ScrapingResult(
                scraper_id=self.scraper_id,
                success=False,
                error=exc.message,
                error_code=exc.code,
                execution_time_ms=elapsed_ms,
                timestamp=datetime.now(),
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1_000
            self.logger.error(
                "[%s] Unexpected error after %.0fms: %s", self.scraper_id, elapsed_ms, exc
            )
            return ScrapingResult(
                scraper_id=self.scraper_id,
                success=False,
                error=str(exc),
                error_code="UNEXPECTED_ERROR",
                execution_time_ms=elapsed_ms,
                timestamp=datetime.now(),
            )
        finally:
            self.teardown()

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """
        Initialise resources before scraping begins.

        The default implementation only records the start time.  Override to
        launch a browser, authenticate, warm up sessions, etc.

        Called automatically by :meth:`__enter__` and :meth:`run_full_scrape`.

        Examples:
            >>> class MyBrowserScraper(BaseScraper):
            ...     def setup(self):
            ...         super().setup()
            ...         playwright = sync_playwright().start()
            ...         self._browser = playwright.chromium.launch(headless=True)
        """
        self._execution_start = time.monotonic()
        self.logger.debug("[%s] setup() called", self.scraper_id)

    def teardown(self) -> None:
        """
        Release resources after scraping ends.

        The default implementation closes the Playwright browser if one was
        assigned to ``self._browser``.  Call ``super().teardown()`` when
        overriding so the default browser cleanup still runs.

        Called automatically by :meth:`__exit__` and :meth:`run_full_scrape`.

        Examples:
            >>> class MyBrowserScraper(BaseScraper):
            ...     def teardown(self):
            ...         self._playwright_instance.stop()
            ...         super().teardown()  # closes self._browser
        """
        if self._browser:
            try:
                self._browser.close()
                self.logger.debug("[%s] Browser closed", self.scraper_id)
            except Exception as exc:
                self.logger.warning(
                    "[%s] Error closing browser: %s", self.scraper_id, exc
                )
            finally:
                self._browser = None

        self.logger.debug("[%s] teardown() complete", self.scraper_id)

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def get_metadata(self) -> Dict[str, Any]:
        """
        Return static metadata about this scraper.

        Returns:
            Dict with ``scraper_id``, ``business_name``, ``base_url``,
            ``scraper_type``, and ``business_type``.

        Examples:
            >>> scraper.get_metadata()
            {
                'scraper_id': 'competitor_a',
                'business_name': 'Competitor A',
                'base_url': 'https://competitor-a.com',
                'scraper_type': 'restaurant',
                'business_type': 'competitor',
            }
        """
        return {
            "scraper_id": self.scraper_id,
            "business_name": self.business_name,
            "base_url": self.base_url,
            "scraper_type": self.config.scraper_type,
            "business_type": self.config.business_type,
        }

    def get_stats(self) -> Dict[str, Any]:
        """
        Return runtime statistics for the current (or last) execution.

        Returns:
            Dict with ``request_count`` and ``execution_time_ms`` (None if
            :meth:`setup` has not been called yet).

        Examples:
            >>> with scraper:
            ...     scraper.search_products("coffee")
            >>> scraper.get_stats()
            {'request_count': 12, 'execution_time_ms': 4321.5}
        """
        execution_time_ms: Optional[float] = None
        if self._execution_start is not None:
            execution_time_ms = (time.monotonic() - self._execution_start) * 1_000

        return {
            "request_count": self._request_count,
            "execution_time_ms": execution_time_ms,
        }

    # ------------------------------------------------------------------
    # Protected utilities for use by subclasses
    # ------------------------------------------------------------------

    def _increment_request_count(self) -> None:
        """
        Increment the internal request counter by one.

        Concrete scrapers should call this after every network request
        (page navigation, API call, etc.) so :meth:`get_stats` is accurate.

        Examples:
            >>> def scrape_product(self, url):
            ...     self._navigate(page, url)
            ...     self._increment_request_count()
            ...     ...
        """
        self._request_count += 1

    def _build_scraped_product(self, **kwargs: Any) -> ScrapedProduct:
        """
        Convenience factory that pre-fills ``source_id`` and ``business_name``
        from the config before delegating to :class:`~.models.ScrapedProduct`.

        Args:
            **kwargs: Any additional :class:`~.models.ScrapedProduct` fields.

        Returns:
            :class:`~.models.ScrapedProduct` with ``source_id`` and
            ``business_name`` already set.

        Examples:
            >>> product = self._build_scraped_product(
            ...     product_name="Cappuccino",
            ...     price=Decimal("15000"),
            ... )
        """
        kwargs.setdefault("source_id", self.scraper_id)
        kwargs.setdefault("business_name", self.business_name)
        return ScrapedProduct(**kwargs)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "BaseScraper":
        """
        Enter the runtime context; calls :meth:`setup`.

        Returns:
            The scraper instance so ``with MyScraper(cfg) as s:`` works.
        """
        self.setup()
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> bool:
        """
        Exit the runtime context; calls :meth:`teardown` unconditionally.

        Returns:
            Always False — exceptions propagate to the caller.
        """
        self.teardown()
        return False

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"scraper_id={self.scraper_id!r}, "
            f"business_name={self.business_name!r})"
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _create_default_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"scraping.scrapers.{self.scraper_id}")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger
