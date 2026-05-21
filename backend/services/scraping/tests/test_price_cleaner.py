import pytest
from decimal import Decimal

from backend.services.scraping.utils.price_cleaner import PriceCleaner
from backend.services.scraping.core.exceptions import ExtractionError


# ============================================
# FIXTURES
# ============================================

@pytest.fixture
def cop():
    """COP cleaner (default currency for this project)."""
    return PriceCleaner(default_currency="COP", decimal_separator="auto")


@pytest.fixture
def usd():
    return PriceCleaner(default_currency="USD", decimal_separator="auto")


@pytest.fixture
def eur():
    return PriceCleaner(default_currency="EUR", decimal_separator="auto")


# ============================================
# COLOMBIAN / LATIN AMERICAN FORMAT
# ============================================

class TestColombianFormat:
    def test_simple_thousands(self, cop):
        assert cop.clean("$1.500") == Decimal("1500")

    def test_millions(self, cop):
        assert cop.clean("$1.500.000") == Decimal("1500000")

    def test_thousands_with_decimal(self, cop):
        assert cop.clean("$1.500,50") == Decimal("1500.50")

    def test_bare_thousands_no_symbol(self, cop):
        assert cop.clean("1.500") == Decimal("1500")

    def test_cop_prefix(self, cop):
        assert cop.clean("COP 1.500") == Decimal("1500")

    def test_cop_suffix(self, cop):
        assert cop.clean("1.500 COP") == Decimal("1500")

    def test_cop_with_decimal(self, cop):
        assert cop.clean("COP 1.500,50") == Decimal("1500.50")

    def test_large_amount_with_decimals(self, cop):
        assert cop.clean("$ 2.300,99") == Decimal("2300.99")

    def test_dollar_sign_resolves_to_default_currency(self, cop):
        _, currency = cop.clean("$1.500", return_currency=True)
        assert currency == "COP"


# ============================================
# US / ENGLISH FORMAT
# ============================================

class TestUSFormat:
    def test_thousands_comma(self, usd):
        assert usd.clean("$1,500") == Decimal("1500")

    def test_thousands_with_cents(self, usd):
        assert usd.clean("$1,500.99") == Decimal("1500.99")

    def test_millions(self, usd):
        assert usd.clean("$1,500,000.50") == Decimal("1500000.50")

    def test_usd_code_prefix(self, usd):
        assert usd.clean("USD 1,500.99") == Decimal("1500.99")

    def test_bare_decimal(self, usd):
        assert usd.clean("1500.99") == Decimal("1500.99")

    def test_usd_extracts_currency(self, cop):
        _, currency = cop.clean("USD 1,500.99", return_currency=True)
        assert currency == "USD"


# ============================================
# EUROPEAN FORMAT
# ============================================

class TestEuropeanFormat:
    def test_euro_suffix(self, eur):
        assert eur.clean("1.500,99€") == Decimal("1500.99")

    def test_euro_prefix(self, eur):
        assert eur.clean("€1.500,50") == Decimal("1500.50")

    def test_eur_code(self, eur):
        assert eur.clean("EUR 1.500,99") == Decimal("1500.99")

    def test_eur_code_no_decimal(self, eur):
        assert eur.clean("EUR 1.500,00") == Decimal("1500.00")

    def test_euro_extracts_currency(self, cop):
        _, currency = cop.clean("€1.500,50", return_currency=True)
        assert currency == "EUR"

    def test_gbp_extracts_currency(self, cop):
        _, currency = cop.clean("£12.99", return_currency=True)
        assert currency == "GBP"


# ============================================
# PLAIN NUMBERS / NO FORMAT
# ============================================

class TestPlainNumbers:
    def test_plain_integer(self, cop):
        assert cop.clean("1500") == Decimal("1500")

    def test_plain_decimal_dot(self, cop):
        assert cop.clean("1500.99") == Decimal("1500.99")

    def test_plain_decimal_comma(self, cop):
        assert cop.clean("1500,99") == Decimal("1500.99")

    def test_zero(self, cop):
        assert cop.clean("$0") == Decimal("0")

    def test_small_decimal(self, cop):
        assert cop.clean("$0.50") == Decimal("0.50")

    def test_leading_trailing_spaces(self, cop):
        assert cop.clean("  $  1.500  ") == Decimal("1500")


# ============================================
# EXPLICIT SEPARATOR (override)
# ============================================

class TestExplicitSeparator:
    def test_explicit_dot_decimal(self):
        c = PriceCleaner(default_currency="USD", decimal_separator=".")
        assert c.clean("1,500.99") == Decimal("1500.99")
        assert c.clean("1500.99") == Decimal("1500.99")

    def test_explicit_comma_decimal(self):
        c = PriceCleaner(default_currency="COP", decimal_separator=",")
        assert c.clean("1.500,50") == Decimal("1500.50")
        assert c.clean("1.500") == Decimal("1500")


# ============================================
# ADDITIONAL TEXT (non-numeric prefixes/suffixes)
# ============================================

class TestTextAffixes:
    def test_text_prefix(self, cop):
        assert cop.clean("Precio: $3.200") == Decimal("3200")

    def test_text_suffix(self, cop):
        assert cop.clean("$1.500 c/u") == Decimal("1500")

    def test_whitespace_around_dollar(self, cop):
        assert cop.clean("  $  1.500  ") == Decimal("1500")


# ============================================
# PRICE RANGES
# ============================================

class TestPriceRanges:
    def test_dash_separator(self, cop):
        lo, hi = cop.clean_range("$100 - $200")
        assert lo == Decimal("100")
        assert hi == Decimal("200")

    def test_en_dash_separator(self, cop):
        lo, hi = cop.clean_range("$1.500–$2.000")
        assert lo == Decimal("1500")
        assert hi == Decimal("2000")

    def test_slash_separator(self, cop):
        lo, hi = cop.clean_range("COP 5.000 / COP 8.000")
        assert lo == Decimal("5000")
        assert hi == Decimal("8000")

    def test_em_dash_separator(self, cop):
        lo, hi = cop.clean_range("$500—$1.000")
        assert lo == Decimal("500")
        assert hi == Decimal("1000")

    def test_reversed_range_auto_sorted(self, cop):
        lo, hi = cop.clean_range("$200 - $100")
        assert lo == Decimal("100")
        assert hi == Decimal("200")

    def test_invalid_range_raises(self, cop):
        with pytest.raises(ExtractionError):
            cop.clean_range("$100")

    def test_invalid_price_in_range_raises(self, cop):
        with pytest.raises(ExtractionError):
            cop.clean_range("no price - also no price")


# ============================================
# CURRENCY EXTRACTION
# ============================================

class TestExtractCurrency:
    def test_cop_code(self, cop):
        assert cop.extract_currency("COP 1.500") == "COP"

    def test_usd_code(self, cop):
        assert cop.extract_currency("USD 1,500") == "USD"

    def test_eur_code(self, cop):
        assert cop.extract_currency("EUR 1.500") == "EUR"

    def test_dollar_sign_returns_default(self, cop):
        assert cop.extract_currency("$1.500") == "COP"

    def test_dollar_sign_usd_default(self, usd):
        assert usd.extract_currency("$1,500") == "USD"

    def test_euro_symbol(self, cop):
        assert cop.extract_currency("€1.500") == "EUR"

    def test_gbp_symbol(self, cop):
        assert cop.extract_currency("£12.99") == "GBP"

    def test_no_symbol_returns_default(self, cop):
        assert cop.extract_currency("1500") == "COP"

    def test_code_takes_priority_over_symbol(self, cop):
        # "USD" code should take priority
        assert cop.extract_currency("USD 1,500") == "USD"

    def test_case_insensitive_code(self, cop):
        assert cop.extract_currency("cop 1.500") == "COP"
        assert cop.extract_currency("Usd 1,500") == "USD"


# ============================================
# return_currency=True
# ============================================

class TestReturnCurrency:
    def test_cop_default(self, cop):
        price, currency = cop.clean("$1.500", return_currency=True)
        assert price == Decimal("1500")
        assert currency == "COP"

    def test_usd_explicit(self, cop):
        price, currency = cop.clean("USD 1,500.99", return_currency=True)
        assert price == Decimal("1500.99")
        assert currency == "USD"

    def test_eur_symbol(self, cop):
        price, currency = cop.clean("€1.500,50", return_currency=True)
        assert price == Decimal("1500.50")
        assert currency == "EUR"

    def test_gbp_symbol(self, cop):
        price, currency = cop.clean("£12.99", return_currency=True)
        assert price == Decimal("12.99")
        assert currency == "GBP"


# ============================================
# ERROR HANDLING
# ============================================

class TestErrors:
    def test_empty_string_raises(self, cop):
        with pytest.raises(ExtractionError) as exc_info:
            cop.clean("")
        assert exc_info.value.code == "PRICE_EMPTY"

    def test_whitespace_only_raises(self, cop):
        with pytest.raises(ExtractionError):
            cop.clean("   ")

    def test_pure_text_raises(self, cop):
        with pytest.raises(ExtractionError):
            cop.clean("Gratis")

    def test_letters_only_raises(self, cop):
        with pytest.raises(ExtractionError):
            cop.clean("N/A")

    def test_error_contains_raw_text(self, cop):
        with pytest.raises(ExtractionError) as exc_info:
            cop.clean("no-price-here")
        assert "no-price-here" in str(exc_info.value) or exc_info.value.details.get("raw")

    def test_extraction_error_has_code(self, cop):
        with pytest.raises(ExtractionError) as exc_info:
            cop.clean("")
        assert exc_info.value.code is not None


# ============================================
# _normalize_separators (unit)
# ============================================

class TestNormalizeSeparators:
    @pytest.fixture
    def c(self):
        return PriceCleaner()

    def test_dot_thousands_comma_decimal(self, c):
        assert c._normalize_separators("1.500,99", ".", ",") == "1500.99"

    def test_comma_thousands_dot_decimal(self, c):
        assert c._normalize_separators("1,500.99", ",", ".") == "1500.99"

    def test_no_separators(self, c):
        assert c._normalize_separators("1500", ",", ".") == "1500"

    def test_multiple_thousand_groups(self, c):
        assert c._normalize_separators("1.500.000", ".", ",") == "1500000"

    def test_returns_zero_for_empty(self, c):
        assert c._normalize_separators("", ",", ".") == "0"


# ============================================
# Convention by currency (_currency_convention)
# ============================================

class TestCurrencyConvention:
    def test_cop_uses_comma_decimal(self):
        c = PriceCleaner("COP")
        assert c._currency_convention() == (".", ",")

    def test_usd_uses_dot_decimal(self):
        c = PriceCleaner("USD")
        assert c._currency_convention() == (",", ".")

    def test_gbp_uses_dot_decimal(self):
        c = PriceCleaner("GBP")
        assert c._currency_convention() == (",", ".")

    def test_eur_uses_comma_decimal(self):
        c = PriceCleaner("EUR")
        assert c._currency_convention() == (".", ",")

    def test_mxn_uses_comma_decimal(self):
        c = PriceCleaner("MXN")
        assert c._currency_convention() == (".", ",")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
