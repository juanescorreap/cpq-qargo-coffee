import pytest
from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch, call

from backend.services.scraping.core.scraper_adapter import ConfigurableScraper
from backend.services.scraping.core.models import ScrapedProduct, ScraperConfig, ScrapingResult
from backend.services.scraping.core.exceptions import (
    ExtractionError,
    NavigationError,
    ScrapingException,
    SelectorError,
    ValidationError,
)


# ============================================
# HELPERS
# ============================================

def _text_elem(text: str) -> Mock:
    """ElementHandle whose inner_text() returns *text*."""
    e = Mock()
    e.inner_text.return_value = text
    return e


def _make_card(name: str, price: str, link: str) -> MagicMock:
    """Mock product card with child selectors for result extraction."""
    card = MagicMock()
    children = {
        ".card-title": _text_elem(name),
        ".card-price": _text_elem(price),
        "a.product-link": _text_elem(link),
    }
    card.query_selector.side_effect = lambda sel: children.get(sel)
    return card


# ============================================
# FIXTURES
# ============================================

@pytest.fixture
def sample_config():
    return ScraperConfig(
        scraper_id="test_scraper",
        business_name="Test Business",
        business_type="competitor",
        scraper_type="restaurant",
        base_url="https://example.com",
        selectors={
            "product_name": ".product-title",
            "product_price": ".price-value",
            # optional so empty search results → [] instead of SelectorError
            "search_results": {"selector": ".product-card", "type": "css", "optional": True},
            "result_name": ".card-title",
            "result_price": ".card-price",
            "result_link": "a.product-link",
        },
        navigation={"search": {"path": "/search", "query_param": "q"}},
        browser={"headless": True, "timeout_ms": 30_000},
        rate_limiting={"enabled": False},  # no real delay in tests
        required_fields=["product_name", "product_price"],
    )


@pytest.fixture
def scraper(sample_config):
    return ConfigurableScraper(sample_config)


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.set_default_timeout = Mock()
    page.close = Mock()
    return page


@pytest.fixture
def prepped(scraper, mock_page):
    """
    Scraper with all I/O components replaced by mocks:
      - _browser:          controls new_page() and close()
      - navigation_engine: no-op navigate_with_retry, returns canned URLs
      - rate_limiter:      no-op wait/mark_success/mark_error
    """
    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_nav = MagicMock()
    mock_nav.build_search_url.return_value = "https://example.com/search?q=test"
    mock_nav.get_pagination_urls.return_value = ["https://example.com/search?q=test"]
    mock_nav.navigate_with_retry = Mock()

    mock_rate = MagicMock()
    mock_rate.wait = Mock()
    mock_rate.mark_success = Mock()
    mock_rate.mark_error = Mock()

    scraper._browser = mock_browser
    scraper.navigation_engine = mock_nav
    scraper.rate_limiter = mock_rate

    return {
        "scraper": scraper,
        "page": mock_page,
        "browser": mock_browser,
        "nav": mock_nav,
        "rate": mock_rate,
    }


# ============================================
# Initialization
# ============================================

class TestInitialization:
    def test_scraper_attributes(self, scraper, sample_config):
        assert scraper.scraper_id == "test_scraper"
        assert scraper.business_name == "Test Business"
        assert scraper.base_url == "https://example.com"
        assert scraper.config is sample_config

    def test_components_created(self, scraper):
        assert scraper.selector_engine is not None
        assert scraper.navigation_engine is not None
        assert scraper.rate_limiter is not None
        assert scraper.price_cleaner is not None

    def test_browser_starts_as_none(self, scraper):
        assert scraper._browser is None

    def test_execution_start_is_none_before_setup(self, scraper):
        assert scraper._execution_start is None

    def test_request_count_starts_at_zero(self, scraper):
        assert scraper._request_count == 0

    def test_repr(self, scraper):
        r = repr(scraper)
        assert "test_scraper" in r
        assert "Test Business" in r


# ============================================
# Browser lifecycle
# ============================================

class TestLifecycle:
    def _playwright_patch(self):
        """Return a context manager that patches sync_playwright correctly."""
        return patch("backend.services.scraping.core.scraper_adapter.sync_playwright")

    def test_setup_sets_execution_start(self, scraper):
        with self._playwright_patch() as mock_sp:
            pw_mgr = MagicMock()
            playwright = MagicMock()
            browser = MagicMock()
            mock_sp.return_value = pw_mgr
            pw_mgr.start.return_value = playwright
            playwright.chromium.launch.return_value = browser

            scraper.setup()
            assert scraper._execution_start is not None
            scraper.teardown()

    def test_setup_launches_chromium(self, scraper):
        with self._playwright_patch() as mock_sp:
            pw_mgr = MagicMock()
            playwright = MagicMock()
            browser = MagicMock()
            mock_sp.return_value = pw_mgr
            pw_mgr.start.return_value = playwright
            playwright.chromium.launch.return_value = browser

            scraper.setup()
            playwright.chromium.launch.assert_called_once_with(headless=True)
            scraper.teardown()

    def test_context_manager_calls_setup_and_teardown(self, scraper):
        with self._playwright_patch() as mock_sp:
            pw_mgr = MagicMock()
            playwright = MagicMock()
            browser = MagicMock()
            mock_sp.return_value = pw_mgr
            pw_mgr.start.return_value = playwright
            playwright.chromium.launch.return_value = browser

            with scraper:
                assert scraper._execution_start is not None

            browser.close.assert_called_once()
            playwright.stop.assert_called_once()

    def test_teardown_without_setup_is_safe(self, scraper):
        """teardown() on a never-setup scraper must not raise."""
        scraper.teardown()  # _browser is None, should be a no-op

    def test_scrape_without_setup_raises(self, scraper):
        with pytest.raises(ScrapingException) as exc_info:
            scraper.scrape_product("https://example.com/product/1")
        assert exc_info.value.code == "BROWSER_NOT_INITIALIZED"

    def test_context_manager_teardown_on_exception(self, scraper):
        with self._playwright_patch() as mock_sp:
            pw_mgr = MagicMock()
            playwright = MagicMock()
            browser = MagicMock()
            mock_sp.return_value = pw_mgr
            pw_mgr.start.return_value = playwright
            playwright.chromium.launch.return_value = browser

            with pytest.raises(RuntimeError):
                with scraper:
                    raise RuntimeError("test error")

            browser.close.assert_called_once()


# ============================================
# scrape_product
# ============================================

class TestScrapeProduct:
    def _setup_page(self, mock_page, name="Test Product", price="$1,500"):
        """Configure mock_page.query_selector to return name and price elements."""
        elements = {
            ".product-title": _text_elem(name),
            ".price-value": _text_elem(price),
        }
        mock_page.query_selector.side_effect = lambda sel: elements.get(sel)

    def test_success_returns_scraped_product(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        self._setup_page(page)

        product = scraper.scrape_product("https://example.com/product/1")

        assert isinstance(product, ScrapedProduct)
        assert product.product_name == "Test Product"
        assert product.price == Decimal("1500")
        assert product.url == "https://example.com/product/1"
        assert product.source_id == "test_scraper"
        assert product.business_name == "Test Business"

    def test_rate_limiter_wait_called(self, prepped):
        scraper, page, rate = prepped["scraper"], prepped["page"], prepped["rate"]
        self._setup_page(page)
        scraper.scrape_product("https://example.com/product/1")
        rate.wait.assert_called_once()

    def test_mark_success_on_successful_scrape(self, prepped):
        scraper, page, rate = prepped["scraper"], prepped["page"], prepped["rate"]
        self._setup_page(page)
        scraper.scrape_product("https://example.com/product/1")
        rate.mark_success.assert_called_once()

    def test_navigation_engine_called(self, prepped):
        scraper, page, nav = prepped["scraper"], prepped["page"], prepped["nav"]
        self._setup_page(page)
        url = "https://example.com/product/1"
        scraper.scrape_product(url)
        nav.navigate_with_retry.assert_called_once_with(page, url, wait_until="networkidle")

    def test_page_closed_after_success(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        self._setup_page(page)
        scraper.scrape_product("https://example.com/product/1")
        page.close.assert_called_once()

    def test_page_closed_after_navigation_failure(self, prepped):
        scraper, page, nav = prepped["scraper"], prepped["page"], prepped["nav"]
        nav.navigate_with_retry.side_effect = NavigationError("timeout")

        with pytest.raises(NavigationError):
            scraper.scrape_product("https://example.com/product/1")

        page.close.assert_called_once()

    def test_navigation_failure_raises_navigation_error(self, prepped):
        scraper, nav = prepped["scraper"], prepped["nav"]
        nav.navigate_with_retry.side_effect = NavigationError("timeout")

        with pytest.raises(NavigationError):
            scraper.scrape_product("https://example.com/product/1")

    def test_mark_error_on_navigation_failure(self, prepped):
        scraper, nav, rate = prepped["scraper"], prepped["nav"], prepped["rate"]
        nav.navigate_with_retry.side_effect = NavigationError("timeout")

        with pytest.raises(NavigationError):
            scraper.scrape_product("https://example.com/product/1")

        rate.mark_error.assert_called_once()

    def test_missing_required_selector_raises(self, prepped):
        """product_price selector absent → SelectorError (required field)."""
        scraper, page = prepped["scraper"], prepped["page"]
        # Only product_name found; product_price selector returns None
        page.query_selector.side_effect = lambda sel: (
            _text_elem("Name") if sel == ".product-title" else None
        )
        with pytest.raises(SelectorError):
            scraper.scraper_product_url = "https://example.com/product/1"
            scraper.scrape_product("https://example.com/product/1")

    def test_request_count_incremented(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        self._setup_page(page)
        initial = scraper._request_count
        scraper.scrape_product("https://example.com/product/1")
        assert scraper._request_count == initial + 1

    def test_optional_fields_are_none_when_absent(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        self._setup_page(page)
        product = scraper.scrape_product("https://example.com/product/1")
        assert product.unit is None
        assert product.category is None
        assert product.description is None


# ============================================
# search_products
# ============================================

class TestSearchProducts:
    def test_returns_products_from_cards(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        cards = [_make_card("Cappuccino", "$5,000", "/product/1"),
                 _make_card("Latte", "$4,500", "/product/2")]
        page.query_selector_all.return_value = cards

        products = scraper.search_products("coffee", limit=10)

        assert len(products) == 2
        assert products[0].product_name == "Cappuccino"
        assert products[0].price == Decimal("5000")
        assert products[1].product_name == "Latte"
        assert products[1].price == Decimal("4500")

    def test_urls_resolved_to_absolute(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        page.query_selector_all.return_value = [_make_card("P", "$100", "/product/99")]

        products = scraper.search_products("coffee", limit=10)

        assert products[0].url == "https://example.com/product/99"

    def test_respects_limit(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        cards = [_make_card(f"P{i}", "$100", f"/p/{i}") for i in range(10)]
        page.query_selector_all.return_value = cards

        products = scraper.search_products("coffee", limit=3)

        assert len(products) == 3

    def test_empty_results_returns_empty_list(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        page.query_selector_all.return_value = []

        products = scraper.search_products("nonexistent")

        assert products == []

    def test_skips_failed_card_elements(self, prepped):
        """A broken element is logged and skipped; valid cards still returned."""
        scraper, page = prepped["scraper"], prepped["page"]

        good_card = _make_card("Good", "$100", "/good")
        bad_card = MagicMock()
        bad_card.query_selector.side_effect = RuntimeError("DOM broken")

        page.query_selector_all.return_value = [good_card, bad_card, good_card]
        products = scraper.search_products("test", limit=10)

        assert len(products) == 2
        assert all(p.product_name == "Good" for p in products)

    def test_rate_limiter_wait_called(self, prepped):
        scraper, page, rate = prepped["scraper"], prepped["page"], prepped["rate"]
        page.query_selector_all.return_value = []
        scraper.search_products("test")
        rate.wait.assert_called_once()

    def test_navigation_engine_build_search_url_called(self, prepped):
        scraper, page, nav = prepped["scraper"], prepped["page"], prepped["nav"]
        page.query_selector_all.return_value = []
        scraper.search_products("café")
        nav.build_search_url.assert_called_once_with("café")

    def test_mark_success_on_success(self, prepped):
        scraper, page, rate = prepped["scraper"], prepped["page"], prepped["rate"]
        page.query_selector_all.return_value = []
        scraper.search_products("test")
        rate.mark_success.assert_called_once()

    def test_page_closed_after_search(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        page.query_selector_all.return_value = []
        scraper.search_products("test")
        page.close.assert_called_once()

    def test_page_closed_after_navigation_failure(self, prepped):
        scraper, nav, page = prepped["scraper"], prepped["nav"], prepped["page"]
        nav.navigate_with_retry.side_effect = NavigationError("timeout")

        with pytest.raises(NavigationError):
            scraper.search_products("test")

        page.close.assert_called_once()

    def test_source_id_and_business_name_set(self, prepped):
        scraper, page = prepped["scraper"], prepped["page"]
        page.query_selector_all.return_value = [_make_card("P", "$100", "/p")]

        products = scraper.search_products("test")

        assert products[0].source_id == "test_scraper"
        assert products[0].business_name == "Test Business"


# ============================================
# scrape_category
# ============================================

class TestScrapeCategory:
    def test_no_category_config_returns_empty(self, prepped):
        scraper = prepped["scraper"]
        # config.navigation has no 'category' key
        result = scraper.scrape_category("cappuccino")
        assert result == []


# ============================================
# _validate_required_fields
# ============================================

class TestValidateRequiredFields:
    def test_passes_when_all_fields_present(self, scraper):
        data = {"product_name": "Cappuccino", "product_price": Decimal("5000")}
        scraper._validate_required_fields(data)  # should not raise

    def test_raises_when_field_missing(self, scraper):
        data = {"product_name": "Cappuccino"}
        with pytest.raises(ValidationError) as exc_info:
            scraper._validate_required_fields(data)
        assert exc_info.value.code == "REQUIRED_FIELD_MISSING"
        assert "product_price" in str(exc_info.value)

    def test_raises_when_field_empty_string(self, scraper):
        data = {"product_name": "", "product_price": Decimal("100")}
        with pytest.raises(ValidationError):
            scraper._validate_required_fields(data)

    def test_raises_when_field_is_none(self, scraper):
        data = {"product_name": None, "product_price": Decimal("100")}
        with pytest.raises(ValidationError):
            scraper._validate_required_fields(data)


# ============================================
# _safe_clean_price
# ============================================

class TestSafeCleanPrice:
    def test_valid_price(self, scraper):
        assert scraper._safe_clean_price("$1,500") == Decimal("1500")

    def test_none_returns_zero(self, scraper):
        assert scraper._safe_clean_price(None) == Decimal("0")

    def test_empty_string_returns_zero(self, scraper):
        assert scraper._safe_clean_price("") == Decimal("0")

    def test_whitespace_only_returns_zero(self, scraper):
        assert scraper._safe_clean_price("   ") == Decimal("0")

    def test_invalid_text_raises_extraction_error(self, scraper):
        with pytest.raises(ExtractionError):
            scraper._safe_clean_price("not a price")

    def test_colombian_format(self, scraper):
        assert scraper._safe_clean_price("$1,500.50") == Decimal("1500.50")


# ============================================
# get_metadata / get_stats
# ============================================

class TestMetadataAndStats:
    def test_get_metadata_fields(self, scraper):
        meta = scraper.get_metadata()
        assert meta["scraper_id"] == "test_scraper"
        assert meta["business_name"] == "Test Business"
        assert meta["base_url"] == "https://example.com"
        assert meta["scraper_type"] == "restaurant"
        assert meta["business_type"] == "competitor"

    def test_get_stats_initial(self, scraper):
        stats = scraper.get_stats()
        assert stats["request_count"] == 0
        assert stats["execution_time_ms"] is None

    def test_get_stats_request_count(self, scraper):
        scraper._request_count = 7
        assert scraper.get_stats()["request_count"] == 7

    def test_get_stats_execution_time_set_after_setup(self, scraper):
        import time
        scraper._execution_start = time.monotonic() - 1.0
        stats = scraper.get_stats()
        assert stats["execution_time_ms"] is not None
        assert stats["execution_time_ms"] > 0


# ============================================
# _build_scraped_product
# ============================================

class TestBuildScrapedProduct:
    def test_injects_source_id_and_business_name(self, scraper):
        product = scraper._build_scraped_product(
            product_name="Espresso", price=Decimal("3000")
        )
        assert product.source_id == "test_scraper"
        assert product.business_name == "Test Business"

    def test_caller_can_override_source_id(self, scraper):
        product = scraper._build_scraped_product(
            product_name="Espresso", price=Decimal("3000"), source_id="custom_id"
        )
        assert product.source_id == "custom_id"

    def test_passes_url(self, scraper):
        product = scraper._build_scraped_product(
            product_name="Latte", price=Decimal("5000"), url="https://example.com/latte"
        )
        assert product.url == "https://example.com/latte"


# ============================================
# run_full_scrape
# ============================================

class TestRunFullScrape:
    def test_success_wraps_in_scraping_result(self, scraper):
        products = [
            ScrapedProduct(
                source_id="test_scraper", business_name="Test Business",
                product_name="Café", price=Decimal("3000"),
            )
        ]
        scraper.setup = Mock()
        scraper.teardown = Mock()
        scraper.search_products = Mock(return_value=products)

        result = scraper.run_full_scrape("café")

        assert isinstance(result, ScrapingResult)
        assert result.success is True
        assert len(result.products) == 1
        assert result.execution_time_ms is not None

    def test_failure_returns_result_with_error(self, scraper):
        scraper.setup = Mock()
        scraper.teardown = Mock()
        scraper.search_products = Mock(
            side_effect=NavigationError("server down")
        )

        result = scraper.run_full_scrape("café")

        assert result.success is False
        assert result.error is not None

    def test_teardown_called_even_on_exception(self, scraper):
        scraper.setup = Mock()
        mock_teardown = Mock()
        scraper.teardown = mock_teardown
        scraper.search_products = Mock(side_effect=NavigationError("error"))

        scraper.run_full_scrape("café")

        mock_teardown.assert_called_once()

    def test_teardown_called_on_success(self, scraper):
        scraper.setup = Mock()
        mock_teardown = Mock()
        scraper.teardown = mock_teardown
        scraper.search_products = Mock(return_value=[])

        scraper.run_full_scrape("café")

        mock_teardown.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
