import pytest
from decimal import Decimal
from unittest.mock import Mock

from backend.services.scraping.core.selector_engine import SelectorEngine
from backend.services.scraping.core.exceptions import ExtractionError, SelectorError


# ============================================
# HELPERS
# ============================================

def _elem(text: str) -> Mock:
    """Mock ElementHandle whose inner_text() returns *text*."""
    e = Mock()
    e.inner_text.return_value = text
    return e


# ============================================
# FIXTURES
# ============================================

@pytest.fixture
def mock_page():
    return Mock()


@pytest.fixture
def config():
    return {
        "product_name": ".title",
        "product_price": {
            "selector": ".price",
            "type": "css",
        },
        "optional_text": {
            "selector": ".badge",
            "type": "css",
            "optional": True,
        },
        "optional_price": {
            "selector": ".opt-price",
            "type": "css",
            "optional": True,
        },
        "product_link": {
            "selector": "a.product",
            "type": "css",
        },
        "optional_link": {
            "selector": "a.opt",
            "type": "css",
            "optional": True,
        },
        "product_list": {
            "selector": ".card",
            "type": "css",
            "optional": True,
        },
        "required_list": {
            "selector": ".item",
            "type": "css",
        },
        "metadata": {
            "rating": {
                "selector": ".rating-value",
                "type": "css",
            }
        },
    }


@pytest.fixture
def engine(config):
    return SelectorEngine(config)


# ============================================
# extract_text
# ============================================

class TestExtractText:
    def test_success_strips_whitespace(self, engine, mock_page):
        mock_page.query_selector.return_value = _elem("  Test Product  ")
        result = engine.extract_text(mock_page, "product_name")
        assert result == "Test Product"
        mock_page.query_selector.assert_called_once_with(".title")

    def test_normalizes_internal_whitespace(self, engine, mock_page):
        mock_page.query_selector.return_value = _elem("Hello   World")
        assert engine.extract_text(mock_page, "product_name") == "Hello World"

    def test_strips_nonbreaking_spaces(self, engine, mock_page):
        mock_page.query_selector.return_value = _elem("Price\xa0Value")
        assert engine.extract_text(mock_page, "product_name") == "Price Value"

    def test_required_not_found_raises_selector_error(self, engine, mock_page):
        mock_page.query_selector.return_value = None
        with pytest.raises(SelectorError):
            engine.extract_text(mock_page, "product_name")

    def test_required_not_found_with_default_returns_default(self, engine, mock_page):
        mock_page.query_selector.return_value = None
        assert engine.extract_text(mock_page, "product_name", default="fallback") == "fallback"

    def test_optional_not_found_returns_none(self, engine, mock_page):
        mock_page.query_selector.return_value = None
        assert engine.extract_text(mock_page, "optional_text") is None

    def test_optional_not_found_with_default(self, engine, mock_page):
        mock_page.query_selector.return_value = None
        assert engine.extract_text(mock_page, "optional_text", default="N/A") == "N/A"

    def test_empty_whitespace_returns_empty_string_not_default(self, engine, mock_page):
        # default is only used when element is *absent*; empty text → ""
        mock_page.query_selector.return_value = _elem("   ")
        result = engine.extract_text(mock_page, "product_name", default="fallback")
        assert result == ""

    def test_inner_text_exception_raises_extraction_error(self, engine, mock_page):
        elem = Mock()
        elem.inner_text.side_effect = Exception("DOM error")
        mock_page.query_selector.return_value = elem
        with pytest.raises(ExtractionError):
            engine.extract_text(mock_page, "product_name")

    def test_unknown_field_returns_default(self, engine, mock_page):
        assert engine.extract_text(mock_page, "nonexistent", default="x") == "x"

    def test_unknown_field_without_default_returns_none(self, engine, mock_page):
        assert engine.extract_text(mock_page, "nonexistent") is None


# ============================================
# extract_price
# ============================================

class TestExtractPrice:
    def test_us_format(self, engine, mock_page):
        mock_page.query_selector.return_value = _elem("$1,500.99")
        assert engine.extract_price(mock_page, "product_price") == Decimal("1500.99")

    def test_plain_number(self, engine, mock_page):
        mock_page.query_selector.return_value = _elem("1500")
        assert engine.extract_price(mock_page, "product_price") == Decimal("1500")

    def test_colombian_thousands_and_decimal(self, engine, mock_page):
        # Both separators present → comma is rightmost → ES decimal
        mock_page.query_selector.return_value = _elem("COP 1.500,99")
        assert engine.extract_price(mock_page, "product_price") == Decimal("1500.99")

    def test_cop_prefix_stripped(self, engine, mock_page):
        mock_page.query_selector.return_value = _elem("COP 2.000,00")
        assert engine.extract_price(mock_page, "product_price") == Decimal("2000.00")

    def test_optional_not_found_returns_none(self, engine, mock_page):
        mock_page.query_selector.return_value = None
        assert engine.extract_price(mock_page, "optional_price") is None

    def test_required_not_found_raises_selector_error(self, engine, mock_page):
        mock_page.query_selector.return_value = None
        with pytest.raises(SelectorError):
            engine.extract_price(mock_page, "product_price")

    def test_unparseable_text_raises_extraction_error(self, engine, mock_page):
        mock_page.query_selector.return_value = _elem("not-a-price")
        with pytest.raises(ExtractionError):
            engine.extract_price(mock_page, "product_price")

    def test_unknown_field_returns_none(self, engine, mock_page):
        assert engine.extract_price(mock_page, "nonexistent") is None


# ============================================
# extract_elements
# ============================================

class TestExtractElements:
    def test_returns_all_matching_elements(self, engine, mock_page):
        elems = [Mock(), Mock(), Mock()]
        mock_page.query_selector_all.return_value = elems
        assert len(engine.extract_elements(mock_page, "product_list")) == 3

    def test_limit_caps_results(self, engine, mock_page):
        mock_page.query_selector_all.return_value = [Mock() for _ in range(10)]
        result = engine.extract_elements(mock_page, "product_list", limit=4)
        assert len(result) == 4

    def test_optional_empty_returns_empty_list(self, engine, mock_page):
        mock_page.query_selector_all.return_value = []
        assert engine.extract_elements(mock_page, "product_list") == []

    def test_required_empty_raises_selector_error(self, engine, mock_page):
        mock_page.query_selector_all.return_value = []
        with pytest.raises(SelectorError):
            engine.extract_elements(mock_page, "required_list")

    def test_unknown_field_returns_empty_list(self, engine, mock_page):
        assert engine.extract_elements(mock_page, "nonexistent") == []

    def test_uses_query_selector_all(self, engine, mock_page):
        mock_page.query_selector_all.return_value = [Mock()]
        engine.extract_elements(mock_page, "product_list")
        mock_page.query_selector_all.assert_called_once_with(".card")


# ============================================
# extract_attribute
# ============================================

class TestExtractAttribute:
    def test_success(self, engine, mock_page):
        elem = Mock()
        elem.get_attribute.return_value = "https://example.com/product/1"
        mock_page.query_selector.return_value = elem
        result = engine.extract_attribute(mock_page, "product_link", "href")
        assert result == "https://example.com/product/1"
        elem.get_attribute.assert_called_once_with("href")

    def test_attribute_absent_returns_default(self, engine, mock_page):
        elem = Mock()
        elem.get_attribute.return_value = None
        mock_page.query_selector.return_value = elem
        assert engine.extract_attribute(mock_page, "product_link", "href", default="#") == "#"

    def test_element_not_found_optional_returns_default(self, engine, mock_page):
        mock_page.query_selector.return_value = None
        result = engine.extract_attribute(mock_page, "optional_link", "href", default="#")
        assert result == "#"

    def test_element_not_found_required_raises(self, engine, mock_page):
        mock_page.query_selector.return_value = None
        with pytest.raises(SelectorError):
            engine.extract_attribute(mock_page, "product_link", "href")

    def test_unknown_field_returns_default(self, engine, mock_page):
        assert engine.extract_attribute(mock_page, "nonexistent", "href", default="x") == "x"

    def test_get_attribute_exception_raises_extraction_error(self, engine, mock_page):
        elem = Mock()
        elem.get_attribute.side_effect = Exception("broken")
        mock_page.query_selector.return_value = elem
        with pytest.raises(ExtractionError):
            engine.extract_attribute(mock_page, "product_link", "href")


# ============================================
# extract_from_element
# ============================================

class TestExtractFromElement:
    def test_success(self, engine):
        parent = Mock()
        parent.query_selector.return_value = _elem("Cappuccino")
        assert engine.extract_from_element(parent, "product_name") == "Cappuccino"

    def test_child_not_found_optional_returns_default(self, engine):
        parent = Mock()
        parent.query_selector.return_value = None
        result = engine.extract_from_element(parent, "optional_text", default="N/A")
        assert result == "N/A"

    def test_child_not_found_required_raises(self, engine):
        parent = Mock()
        parent.query_selector.return_value = None
        with pytest.raises(SelectorError):
            engine.extract_from_element(parent, "product_name")

    def test_with_attribute_config(self, config):
        config["img"] = {"selector": "img", "type": "css", "attribute": "src"}
        engine = SelectorEngine(config)
        parent = Mock()
        child = Mock()
        child.get_attribute.return_value = "https://cdn.example.com/img.jpg"
        parent.query_selector.return_value = child
        assert engine.extract_from_element(parent, "img") == "https://cdn.example.com/img.jpg"

    def test_unknown_subfield_returns_default(self, engine):
        parent = Mock()
        result = engine.extract_from_element(parent, "nonexistent", default="x")
        assert result == "x"


# ============================================
# Dot notation (_get_selector_config)
# ============================================

class TestDotNotation:
    def test_nested_field_resolved(self, engine, mock_page):
        mock_page.query_selector.return_value = _elem("4.5")
        result = engine.extract_text(mock_page, "metadata.rating")
        assert result == "4.5"
        mock_page.query_selector.assert_called_once_with(".rating-value")

    def test_missing_nested_key_returns_default(self, engine, mock_page):
        result = engine.extract_text(mock_page, "metadata.nonexistent", default="?")
        assert result == "?"

    def test_fully_missing_path_returns_default(self, engine, mock_page):
        result = engine.extract_text(mock_page, "does.not.exist", default="fallback")
        assert result == "fallback"


# ============================================
# Element cache
# ============================================

class TestElementCache:
    def test_cache_hit_avoids_second_dom_query(self, mock_page):
        engine = SelectorEngine({"product_name": ".title"}, enable_cache=True)
        mock_page.query_selector.return_value = _elem("Name")
        engine.extract_text(mock_page, "product_name")
        engine.extract_text(mock_page, "product_name")
        mock_page.query_selector.assert_called_once_with(".title")

    def test_clear_cache_forces_requery(self, mock_page):
        engine = SelectorEngine({"product_name": ".title"}, enable_cache=True)
        mock_page.query_selector.return_value = _elem("Name")
        engine.extract_text(mock_page, "product_name")
        engine.clear_cache()
        engine.extract_text(mock_page, "product_name")
        assert mock_page.query_selector.call_count == 2

    def test_cache_disabled_always_queries(self, mock_page):
        engine = SelectorEngine({"product_name": ".title"}, enable_cache=False)
        mock_page.query_selector.return_value = _elem("Name")
        engine.extract_text(mock_page, "product_name")
        engine.extract_text(mock_page, "product_name")
        assert mock_page.query_selector.call_count == 2

    def test_multi_queries_never_cached(self, mock_page):
        engine = SelectorEngine(
            {"items": {"selector": ".item", "type": "css", "optional": True}},
            enable_cache=True,
        )
        mock_page.query_selector_all.return_value = [Mock()]
        engine.extract_elements(mock_page, "items")
        engine.extract_elements(mock_page, "items")
        assert mock_page.query_selector_all.call_count == 2


# ============================================
# XPath selector support
# ============================================

class TestXPathSelector:
    def test_xpath_prefix_added(self, mock_page):
        engine = SelectorEngine({
            "xpath_field": {
                "selector": "//span[@class='price']",
                "type": "xpath",
            }
        })
        mock_page.query_selector.return_value = _elem("$5.000")
        engine.extract_text(mock_page, "xpath_field")
        mock_page.query_selector.assert_called_once_with("xpath=//span[@class='price']")

    def test_xpath_multi_uses_query_selector_all(self, mock_page):
        engine = SelectorEngine({
            "rows": {
                "selector": "//tr",
                "type": "xpath",
                "optional": True,
            }
        })
        mock_page.query_selector_all.return_value = [Mock(), Mock()]
        engine.extract_elements(mock_page, "rows")
        mock_page.query_selector_all.assert_called_once_with("xpath=//tr")


# ============================================
# _clean_price (unit tests on private method)
# ============================================

class TestCleanPrice:
    @pytest.fixture
    def e(self):
        return SelectorEngine({})

    def test_us_format(self, e):
        assert e._clean_price("$1,500.99") == Decimal("1500.99")

    def test_cop_format_with_both_separators(self, e):
        assert e._clean_price("COP 1.500,99") == Decimal("1500.99")

    def test_plain_integer(self, e):
        assert e._clean_price("1500") == Decimal("1500")

    def test_two_decimal_places_comma(self, e):
        assert e._clean_price("15,99") == Decimal("15.99")

    def test_strips_currency_symbols(self, e):
        for symbol in ["$", "€", "£"]:
            assert e._clean_price(f"{symbol}100") == Decimal("100")

    def test_empty_string_raises(self, e):
        with pytest.raises(ValueError):
            e._clean_price("")

    def test_only_currency_raises(self, e):
        with pytest.raises(ValueError):
            e._clean_price("$")


# ============================================
# Playwright query failure (edge case)
# ============================================

class TestQueryFailure:
    def test_broken_selector_treated_as_not_found(self, engine, mock_page):
        mock_page.query_selector.side_effect = Exception("invalid selector")
        with pytest.raises(SelectorError):
            engine.extract_text(mock_page, "product_name")

    def test_broken_multi_selector_raises_for_required(self, engine, mock_page):
        mock_page.query_selector_all.side_effect = Exception("invalid selector")
        with pytest.raises(SelectorError):
            engine.extract_elements(mock_page, "required_list")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
