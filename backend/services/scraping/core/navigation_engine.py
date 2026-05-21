"""
Web navigation engine for the scraping system.

Handles URL construction, search queries, pagination, and page navigation
with retry logic. All methods that touch the browser take a Playwright Page
object; pure URL-building methods are browser-free and fully testable.
"""

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import ParseResult, quote_plus, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import Page

from .exceptions import NavigationError


# Pagination strategy identifiers used in navigation_config['pagination']['type'].
_PAGINATION_QUERY_PARAM = "query_param"
_PAGINATION_PATH = "path"
_PAGINATION_OFFSET = "offset"


class NavigationEngine:
    """
    Web navigation engine.

    Builds URLs, handles searches, and coordinates pagination and retries.

    Expected navigation_config shape (all keys optional — engine degrades
    gracefully when a section is absent)::

        {
            "search": {
                "path": "/search",          # appended to base_url
                "method": "GET",            # GET | POST (POST → form submit via JS)
                "query_param": "q",         # used for GET method
                "path_template": None,      # e.g. "/search/{query}" for path method
            },
            "category": {
                "path": "/categoria",
                "path_template": "/categoria/{category}",
            },
            "product": {
                "path": "/producto",
                "path_template": "/producto/{product_id}",
            },
            "pagination": {
                "type": "query_param",      # query_param | path | offset
                "param": "page",            # query param or path segment name
                "start": 1,                 # first page number (default 1)
                "offset_step": 20,          # items per page for offset pagination
                "max_pages": 5,
            },
            "timeouts": {
                "navigation_ms": 30000,
                "retry_backoff_ms": 2000,
            },
        }
    """

    def __init__(
        self,
        navigation_config: Dict[str, Any],
        base_url: str,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            navigation_config: Navigation behaviour config (see class docstring).
            base_url:          Root URL of the site; trailing slash is stripped.
            logger:            Optional logger; a default one is created if omitted.
        """
        self.navigation_config = navigation_config
        self.base_url = base_url.rstrip("/")
        self.logger = logger or self._create_default_logger()

    # ------------------------------------------------------------------
    # URL construction (browser-free)
    # ------------------------------------------------------------------

    def build_search_url(self, query: str) -> str:
        """
        Build a search URL for the given query term.

        Supported search methods (from config):
          - ``GET``  with ``query_param`` → ``/search?q=term``
          - ``GET``  with ``path_template`` → ``/search/term``
          - ``POST`` → returns the form-action URL; the caller is responsible
            for submitting the form via Playwright.

        Args:
            query: Search term (will be URL-encoded).

        Returns:
            Absolute search URL.

        Raises:
            NavigationError: search config is missing or malformed.

        Examples:
            >>> engine.build_search_url("cappuccino")
            'https://example.com/search?q=cappuccino'

            >>> # With path_template: "/buscar/{query}"
            >>> engine.build_search_url("cappuccino")
            'https://example.com/buscar/cappuccino'
        """
        cfg = self.navigation_config.get("search", {})
        if not cfg:
            raise NavigationError(
                "No 'search' section in navigation config",
                code="CONFIG_MISSING_SEARCH",
                details={"base_url": self.base_url},
            )

        method = cfg.get("method", "GET").upper()
        path_template: Optional[str] = cfg.get("path_template")
        path: str = cfg.get("path", "")

        if path_template:
            encoded = self._encode_query_param(query)
            relative = path_template.replace("{query}", encoded)
            return self._normalize_url(self.base_url + relative)

        if method == "GET":
            param = cfg.get("query_param", "q")
            qs = urlencode({param: query})
            base = self.base_url + path
            separator = "&" if "?" in base else "?"
            return self._normalize_url(f"{base}{separator}{qs}")

        # POST — return the form-action URL; caller handles submission.
        return self._normalize_url(self.base_url + path)

    def build_category_url(self, category: str) -> str:
        """
        Build the URL for a product category page.

        Uses ``navigation_config['category']['path_template']`` when present
        (e.g. ``'/categoria/{category}'``), otherwise appends the category
        as a query parameter or plain path segment.

        Args:
            category: Category name or slug (will be URL-encoded).

        Returns:
            Absolute category URL.

        Examples:
            >>> engine.build_category_url("cafe-molido")
            'https://example.com/categoria/cafe-molido'
        """
        cfg = self.navigation_config.get("category", {})
        path_template: Optional[str] = cfg.get("path_template")
        path: str = cfg.get("path", "")
        param: str = cfg.get("query_param", "category")

        if path_template:
            encoded = self._encode_query_param(category)
            relative = path_template.replace("{category}", encoded)
            return self._normalize_url(self.base_url + relative)

        if path:
            encoded = self._encode_query_param(category)
            return self._normalize_url(f"{self.base_url}{path}/{encoded}")

        qs = urlencode({param: category})
        return self._normalize_url(f"{self.base_url}?{qs}")

    def build_product_url(self, product_id: str) -> str:
        """
        Build the URL for a single product page.

        Uses ``navigation_config['product']['path_template']`` when present
        (e.g. ``'/producto/{product_id}'``).

        Args:
            product_id: Product ID or slug (will be URL-encoded).

        Returns:
            Absolute product URL.

        Examples:
            >>> engine.build_product_url("cappuccino-grande-12oz")
            'https://example.com/producto/cappuccino-grande-12oz'
        """
        cfg = self.navigation_config.get("product", {})
        path_template: Optional[str] = cfg.get("path_template")
        path: str = cfg.get("path", "")
        param: str = cfg.get("query_param", "id")

        if path_template:
            encoded = self._encode_query_param(product_id)
            relative = path_template.replace("{product_id}", encoded)
            return self._normalize_url(self.base_url + relative)

        if path:
            encoded = self._encode_query_param(product_id)
            return self._normalize_url(f"{self.base_url}{path}/{encoded}")

        qs = urlencode({param: product_id})
        return self._normalize_url(f"{self.base_url}?{qs}")

    def get_pagination_urls(
        self,
        base_search_url: str,
        total_pages: Optional[int] = None,
    ) -> List[str]:
        """
        Generate a list of paginated URLs from a base search URL.

        Supported pagination types (from config):
          - ``query_param`` → appends ``?page=1``, ``?page=2``, …
          - ``path``        → appends ``/page/1``, ``/page/2``, …
          - ``offset``      → appends ``?offset=0``, ``?offset=20``, …

        The first URL in the list is always the first page (page index ``start``
        or offset 0), so callers can iterate the list directly.

        Args:
            base_search_url: URL already built by :meth:`build_search_url` or
                             similar; query string may already contain params.
            total_pages:     Override max pages from config.

        Returns:
            Ordered list of absolute URLs (one per page).

        Examples:
            >>> engine.get_pagination_urls("https://example.com/search?q=cafe")
            [
                'https://example.com/search?q=cafe&page=1',
                'https://example.com/search?q=cafe&page=2',
            ]
        """
        cfg = self.navigation_config.get("pagination", {})
        ptype: str = cfg.get("type", _PAGINATION_QUERY_PARAM)
        max_pages: int = total_pages or cfg.get("max_pages", 1)
        start: int = cfg.get("start", 1)
        param: str = cfg.get("param", "page")
        offset_step: int = cfg.get("offset_step", 20)

        urls: List[str] = []

        if ptype == _PAGINATION_QUERY_PARAM:
            for page_num in range(start, start + max_pages):
                url = self._append_query_param(base_search_url, param, str(page_num))
                urls.append(self._normalize_url(url))

        elif ptype == _PAGINATION_PATH:
            for page_num in range(start, start + max_pages):
                url = base_search_url.rstrip("/") + f"/{param}/{page_num}"
                urls.append(self._normalize_url(url))

        elif ptype == _PAGINATION_OFFSET:
            for i in range(max_pages):
                offset = i * offset_step
                url = self._append_query_param(base_search_url, param, str(offset))
                urls.append(self._normalize_url(url))

        else:
            self.logger.warning(
                "Unknown pagination type '%s'; returning base URL only", ptype
            )
            urls.append(self._normalize_url(base_search_url))

        self.logger.debug(
            "Generated %d pagination URLs (type=%s, max=%d)", len(urls), ptype, max_pages
        )
        return urls

    # ------------------------------------------------------------------
    # Browser navigation (requires Playwright Page)
    # ------------------------------------------------------------------

    def navigate_with_retry(
        self,
        page: Page,
        url: str,
        max_retries: int = 3,
        wait_until: str = "networkidle",
    ) -> bool:
        """
        Navigate to *url* with exponential-backoff retry logic.

        Args:
            page:        Playwright Page object.
            url:         Destination URL.
            max_retries: Maximum number of attempts before raising.
            wait_until:  Playwright wait condition:
                         ``'load'`` | ``'domcontentloaded'`` | ``'networkidle'``.

        Returns:
            True on success.

        Raises:
            NavigationError: All retries exhausted.

        Examples:
            >>> engine.navigate_with_retry(page, "https://example.com/search?q=cafe")
            True
        """
        cfg = self.navigation_config.get("timeouts", {})
        timeout_ms: int = cfg.get("navigation_ms", 30_000)
        backoff_ms: int = cfg.get("retry_backoff_ms", 2_000)

        url = self._normalize_url(url)
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                self.logger.info(
                    "Navigating to '%s' (attempt %d/%d)", url, attempt, max_retries
                )
                response = page.goto(url, wait_until=wait_until, timeout=timeout_ms)

                if response is None:
                    raise NavigationError(
                        f"Navigation returned no response for '{url}'",
                        code="NO_RESPONSE",
                        details={"url": url, "attempt": attempt},
                    )

                status = response.status
                if status >= 400:
                    if status == 429:
                        raise NavigationError(
                            f"Rate limited (HTTP 429) on '{url}'",
                            code="RATE_LIMIT_EXCEEDED",
                            details={"url": url, "status": status},
                        )
                    raise NavigationError(
                        f"HTTP {status} error for '{url}'",
                        code=f"HTTP_{status}",
                        details={"url": url, "status": status, "attempt": attempt},
                    )

                self.logger.info("Successfully navigated to '%s' (HTTP %d)", url, status)
                return True

            except NavigationError:
                raise  # Re-raise immediately — no retry for 4xx/5xx.
            except Exception as exc:
                last_error = exc
                self.logger.warning(
                    "Attempt %d/%d failed for '%s': %s", attempt, max_retries, url, exc
                )
                if attempt < max_retries:
                    sleep_s = (backoff_ms / 1000) * (2 ** (attempt - 1))
                    self.logger.debug("Backing off %.1fs before retry", sleep_s)
                    time.sleep(sleep_s)

        raise NavigationError(
            f"Navigation to '{url}' failed after {max_retries} attempts",
            code="MAX_RETRIES_EXCEEDED",
            details={"url": url, "max_retries": max_retries, "last_error": str(last_error)},
        )

    def wait_for_page_change(
        self,
        page: Page,
        timeout_ms: int = 5_000,
    ) -> bool:
        """
        Wait for the page URL or DOM to change (useful for infinite-scroll /
        SPA navigation without full page reloads).

        Captures the current URL before waiting and compares after. If the URL
        is the same, also checks whether the ``DOMContentLoaded`` event fires
        within *timeout_ms*.

        Args:
            page:       Playwright Page.
            timeout_ms: Maximum wait time in milliseconds.

        Returns:
            True if the page changed, False if timeout elapsed without change.

        Examples:
            >>> page.click(".load-more")
            >>> changed = engine.wait_for_page_change(page, timeout_ms=3000)
        """
        url_before = page.url
        try:
            # Wait for either a URL change or a network-idle signal.
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
            url_after = page.url
            changed = url_after != url_before
            self.logger.debug(
                "wait_for_page_change: %s → %s (%s)",
                url_before,
                url_after,
                "changed" if changed else "same URL",
            )
            return changed
        except Exception:
            self.logger.debug(
                "wait_for_page_change: timeout after %dms (url=%s)", timeout_ms, url_before
            )
            return False

    def scroll_to_bottom(
        self,
        page: Page,
        pause_ms: int = 1_000,
        max_scrolls: int = 10,
    ) -> int:
        """
        Scroll down repeatedly to trigger infinite-scroll content loading.

        Args:
            page:        Playwright Page.
            pause_ms:    Milliseconds to pause between scrolls.
            max_scrolls: Safety cap on scroll iterations.

        Returns:
            Number of scroll iterations performed.

        Examples:
            >>> n = engine.scroll_to_bottom(page, pause_ms=800, max_scrolls=5)
            >>> print(f"Scrolled {n} times")
        """
        prev_height: int = 0
        scrolls = 0

        for _ in range(max_scrolls):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(pause_ms / 1000)

            new_height: int = page.evaluate("document.body.scrollHeight")
            scrolls += 1

            if new_height == prev_height:
                self.logger.debug("Reached bottom of page after %d scrolls", scrolls)
                break

            prev_height = new_height
            self.logger.debug("Scroll %d: height %d → %d", scrolls, prev_height, new_height)

        return scrolls

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _normalize_url(self, url: str) -> str:
        """
        Ensure *url* is absolute and well-formed.

        - Adds ``https://`` when the URL has no scheme.
        - Resolves relative URLs against :attr:`base_url`.
        - Removes duplicate slashes in the path (except after ``://``).

        Args:
            url: Raw URL string.

        Returns:
            Cleaned absolute URL.
        """
        url = url.strip()

        if not url:
            return self.base_url

        # Resolve relative URLs.
        if not url.startswith(("http://", "https://")):
            if url.startswith("/"):
                url = self.base_url + url
            else:
                url = urljoin(self.base_url + "/", url)

        # Collapse duplicate slashes in the path only.
        parsed: ParseResult = urlparse(url)
        import re
        clean_path = re.sub(r"/{2,}", "/", parsed.path)
        return urlunparse(parsed._replace(path=clean_path))

    def _encode_query_param(self, value: str) -> str:
        """
        URL-encode a query parameter value using ``+`` for spaces
        (application/x-www-form-urlencoded format, compatible with most sites).

        Args:
            value: Raw parameter value.

        Returns:
            Percent-encoded string.

        Examples:
            >>> engine._encode_query_param("café molido")
            'caf%C3%A9+molido'
        """
        return quote_plus(value.strip())

    def _append_query_param(self, url: str, param: str, value: str) -> str:
        """
        Append a single query parameter to an existing URL, preserving any
        parameters already present.

        Args:
            url:   Base URL (may already contain a query string).
            param: Parameter name.
            value: Parameter value (not encoded — this method encodes it).

        Returns:
            URL with the parameter appended.
        """
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urlencode({param: value})}"

    @staticmethod
    def _create_default_logger() -> logging.Logger:
        logger = logging.getLogger("scraping.navigation_engine")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger
