"""
Advanced price cleaning utility for the scraping system.

Handles every price string format encountered across Colombian competitors and
international suppliers, including:
  - Colombian / Latin-American: $1.500  |  $1.500.000  |  COP 1.500,50
  - US / English:               $1,500  |  $1,500.99   |  USD 1500
  - European:                   1.500€  |  1.500,99€   |  EUR 1.500,00
  - Bare integers:              1500    |  1500.0
  - Price ranges:               $100 - $200  |  $50–$75
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple, Union

from ..core.exceptions import ExtractionError


# ---------------------------------------------------------------------------
# Currency symbol / code tables
# ---------------------------------------------------------------------------

# Maps textual codes (case-insensitive) to ISO 4217 codes.
_CODE_MAP: Dict[str, str] = {
    "cop": "COP",
    "usd": "USD",
    "eur": "EUR",
    "gbp": "GBP",
    "mxn": "MXN",
    "brl": "BRL",
    "ars": "ARS",
    "clp": "CLP",
    "pen": "PEN",
    "cad": "CAD",
}

# Maps symbol characters to ISO 4217 codes (in priority order).
_SYMBOL_MAP: Dict[str, str] = {
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₩": "KRW",
    "₿": "BTC",
    # "$" is ambiguous — resolved by context (default_currency).
}

# All known currency tokens to strip (longest first to avoid partial removal).
_ALL_CURRENCY_TOKENS: List[str] = sorted(
    list(_CODE_MAP.keys())
    + [k.upper() for k in _CODE_MAP]
    + list(_SYMBOL_MAP.keys())
    + ["$"],
    key=len,
    reverse=True,
)

# Regex that matches any range separator between two price strings.
_RANGE_SEP_RE = re.compile(r"\s*[-–—/]\s*", re.UNICODE)


class PriceCleaner:
    """
    Advanced price string parser.

    Handles international formats, auto-detects separator conventions,
    extracts embedded currency codes, and parses price ranges.

    Args:
        default_currency:  ISO 4217 code assumed when no currency symbol is
                           found in the text.  Defaults to ``'COP'``.
        decimal_separator: ``'auto'`` (detect from context), ``'.'``
                           (English convention), or ``','`` (European /
                           Colombian convention).

    Test suite (all verified by :meth:`clean`):

        >>> c = PriceCleaner("COP")

        # --- Colombian / Latin-American ---
        >>> c.clean("$1.500")
        Decimal('1500')
        >>> c.clean("$1.500.000")
        Decimal('1500000')
        >>> c.clean("COP 1.500")
        Decimal('1500')
        >>> c.clean("COP 1.500,50")
        Decimal('1500.50')
        >>> c.clean("$ 2.300,99")
        Decimal('2300.99')
        >>> c.clean("1.500")          # bare, COP context → thousands dot
        Decimal('1500')

        # --- US / English ---
        >>> c2 = PriceCleaner("USD")
        >>> c2.clean("$1,500")
        Decimal('1500')
        >>> c2.clean("$1,500.99")
        Decimal('1500.99')
        >>> c2.clean("USD 1500")
        Decimal('1500')
        >>> c2.clean("1500.99")
        Decimal('1500.99')

        # --- European ---
        >>> c3 = PriceCleaner("EUR")
        >>> c3.clean("1.500€")
        Decimal('1500')
        >>> c3.clean("1.500,99€")
        Decimal('1500.99')
        >>> c3.clean("€1.500,50")
        Decimal('1500.50')
        >>> c3.clean("EUR 1.500,00")
        Decimal('1500.00')

        # --- Currency extraction ---
        >>> c.clean("USD 1,500.99", return_currency=True)
        (Decimal('1500.99'), 'USD')
        >>> c.clean("€1.500,50", return_currency=True)
        (Decimal('1500.50'), 'EUR')
        >>> c.clean("£12.99", return_currency=True)
        (Decimal('12.99'), 'GBP')

        # --- Edge cases ---
        >>> c.clean("  $  1.500  ")
        Decimal('1500')
        >>> c.clean("Precio: $3.200")
        Decimal('3200')
        >>> c.clean("$0")
        Decimal('0')
        >>> c.clean("1500")
        Decimal('1500')
        >>> c.clean("Gratis")            # raises ExtractionError

        # --- Ranges ---
        >>> c.clean_range("$100 - $200")
        (Decimal('100'), Decimal('200'))
        >>> c.clean_range("$1.500–$2.000")
        (Decimal('1500'), Decimal('2000'))
        >>> c.clean_range("COP 5.000 / COP 8.000")
        (Decimal('5000'), Decimal('8000'))
    """

    def __init__(
        self,
        default_currency: str = "COP",
        decimal_separator: str = "auto",
    ) -> None:
        self.default_currency = default_currency.upper()
        self.decimal_separator = decimal_separator  # 'auto' | '.' | ','

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clean(
        self,
        price_text: str,
        return_currency: bool = False,
    ) -> Union[Decimal, Tuple[Decimal, str]]:
        """
        Parse *price_text* into a Decimal, optionally returning the currency.

        Args:
            price_text:      Raw price string from the DOM.
            return_currency: When True, returns ``(Decimal, currency_code)``.

        Returns:
            :class:`Decimal` price, or ``(Decimal, str)`` when
            *return_currency* is True.

        Raises:
            ExtractionError: Text cannot be parsed as a price.
        """
        if not price_text or not price_text.strip():
            raise ExtractionError(
                "Empty price text",
                code="PRICE_EMPTY",
                details={"raw": price_text},
            )

        text = price_text.strip()
        currency = self.extract_currency(text)
        fmt = self._detect_format(text)
        numeric = self._remove_currency_symbols(text)
        numeric = self._remove_non_price_text(numeric)
        if not re.search(r"\d", numeric):
            raise ExtractionError(
                f"No numeric content in '{price_text}'",
                code="PRICE_NO_NUMERIC",
                details={"raw": price_text},
            )
        numeric = self._normalize_separators(
            numeric,
            thousands_sep=fmt["thousands_sep"],
            decimal_sep=fmt["decimal_sep"],
        )

        try:
            value = Decimal(numeric)
        except InvalidOperation as exc:
            raise ExtractionError(
                f"Cannot parse price from '{price_text}' (normalized: '{numeric}')",
                code="PRICE_PARSE_FAILED",
                details={"raw": price_text, "normalized": numeric},
            ) from exc

        if value < 0:
            raise ExtractionError(
                f"Negative price in '{price_text}'",
                code="PRICE_NEGATIVE",
                details={"raw": price_text, "value": str(value)},
            )

        return (value, currency) if return_currency else value

    def clean_range(self, price_text: str) -> Tuple[Decimal, Decimal]:
        """
        Parse a price range string and return ``(min_price, max_price)``.

        Accepted separators: ``-``, ``–``, ``—``, ``/``.

        Args:
            price_text: Range string such as ``'$100 - $200'``.

        Returns:
            Tuple of ``(min_price, max_price)`` as Decimals.

        Raises:
            ExtractionError: Text is not a valid range or prices are invalid.

        Examples:
            >>> cleaner.clean_range("$1.500–$2.000")
            (Decimal('1500'), Decimal('2000'))
        """
        parts = _RANGE_SEP_RE.split(price_text.strip(), maxsplit=1)
        if len(parts) != 2:
            raise ExtractionError(
                f"'{price_text}' is not a valid price range",
                code="PRICE_RANGE_INVALID",
                details={"raw": price_text},
            )

        try:
            lo = self.clean(parts[0].strip())
            hi = self.clean(parts[1].strip())
        except ExtractionError as exc:
            raise ExtractionError(
                f"Failed to parse range parts in '{price_text}'",
                code="PRICE_RANGE_PARSE_FAILED",
                details={"raw": price_text},
            ) from exc

        if lo > hi:
            lo, hi = hi, lo  # tolerate reversed order
        return lo, hi

    def extract_currency(self, price_text: str) -> str:
        """
        Detect and return the ISO 4217 currency code embedded in *price_text*.

        Detection order:
          1. Three-letter code (COP, USD, EUR, …).
          2. Unambiguous symbol (€, £, ¥, …).
          3. Dollar sign ``$`` — resolved to :attr:`default_currency`.
          4. Falls back to :attr:`default_currency`.

        Args:
            price_text: Raw price string.

        Returns:
            ISO 4217 currency code (e.g. ``'COP'``, ``'USD'``).

        Examples:
            >>> cleaner.extract_currency("COP 1.500")
            'COP'
            >>> cleaner.extract_currency("€1.500,50")
            'EUR'
            >>> cleaner.extract_currency("$1.500")  # default_currency='COP'
            'COP'
        """
        upper = price_text.upper()

        # Three-letter code match (case-insensitive: search uppercase pattern in uppercased text).
        for code, iso in _CODE_MAP.items():
            if re.search(rf"\b{code.upper()}\b", upper):
                return iso

        # Unambiguous symbol.
        for symbol, iso in _SYMBOL_MAP.items():
            if symbol in price_text:
                return iso

        # Dollar sign → contextual default.
        if "$" in price_text:
            return self.default_currency

        return self.default_currency

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_format(self, price_text: str) -> Dict[str, str]:
        """
        Infer the thousands and decimal separator convention from *price_text*.

        Algorithm:
          1. If :attr:`decimal_separator` is explicit (``'.'`` or ``','``),
             use it directly.
          2. Otherwise auto-detect using the following heuristics:

             a. Comma appears after the last dot with exactly 2 digits after
                → decimal separator is comma (Colombian / European).
             b. Dot appears after the last comma with 1 or 2 digits after
                → decimal separator is dot (English).
             c. Only commas present and the last comma has exactly 2 digits
                after → decimal separator is comma.
             d. Only dots present and the last dot has exactly 2 digits after
                → decimal separator is dot (could be English decimal).
             e. Only dots and each group has exactly 3 digits → dot is
                thousands separator (Colombian), no decimal part.
             f. Default to the currency-driven convention:
                COP / EUR / most Latin currencies → comma decimal.
                USD / GBP / most English currencies → dot decimal.

        Returns:
            ``{'thousands_sep': str, 'decimal_sep': str,
               'currency_symbol': str, 'currency_position': str}``
        """
        numeric = self._remove_currency_symbols(price_text)
        numeric = self._remove_non_price_text(numeric).strip()

        currency_symbol = ""
        for sym in list(_SYMBOL_MAP.keys()) + ["$"]:
            if sym in price_text:
                currency_symbol = sym
                break

        currency_position = "prefix"
        stripped = price_text.strip()
        if stripped and stripped[-1] in _SYMBOL_MAP:
            currency_position = "suffix"

        # Explicit separator override.
        if self.decimal_separator == ",":
            return {
                "thousands_sep": ".",
                "decimal_sep": ",",
                "currency_symbol": currency_symbol,
                "currency_position": currency_position,
            }
        if self.decimal_separator == ".":
            return {
                "thousands_sep": ",",
                "decimal_sep": ".",
                "currency_symbol": currency_symbol,
                "currency_position": currency_position,
            }

        # Auto-detect from digit/separator patterns.
        dot_pos = numeric.rfind(".")
        comma_pos = numeric.rfind(",")
        has_dot = dot_pos >= 0
        has_comma = comma_pos >= 0

        if has_dot and has_comma:
            # Whichever comes last is the decimal separator.
            if comma_pos > dot_pos:
                thousands_sep, decimal_sep = ".", ","
            else:
                thousands_sep, decimal_sep = ",", "."

        elif has_comma and not has_dot:
            after_comma = numeric[comma_pos + 1:]
            # Comma with exactly 1 or 2 digits after → decimal separator.
            if re.fullmatch(r"\d{1,2}", after_comma):
                thousands_sep, decimal_sep = ".", ","
            else:
                # Comma as thousands separator only (no decimal part).
                thousands_sep, decimal_sep = ",", "."

        elif has_dot and not has_comma:
            after_dot = numeric[dot_pos + 1:]
            before_dot = numeric[:dot_pos]
            # Dot with exactly 3 digits and >=1 digit before → thousands sep.
            if re.fullmatch(r"\d{3}", after_dot) and len(re.sub(r"\D", "", before_dot)) >= 1:
                thousands_sep, decimal_sep = ".", ","
            else:
                # Dot as decimal separator.
                thousands_sep, decimal_sep = ",", "."

        else:
            # No separators — fall back to currency convention.
            thousands_sep, decimal_sep = self._currency_convention()

        return {
            "thousands_sep": thousands_sep,
            "decimal_sep": decimal_sep,
            "currency_symbol": currency_symbol,
            "currency_position": currency_position,
        }

    def _currency_convention(self) -> Tuple[str, str]:
        """
        Return ``(thousands_sep, decimal_sep)`` based on :attr:`default_currency`.

        Colombian / most Latin-American / European currencies use comma as
        decimal separator; USD/GBP use dot.
        """
        dot_decimal_currencies = {"USD", "GBP", "CAD", "AUD", "NZD", "HKD", "SGD"}
        if self.default_currency in dot_decimal_currencies:
            return ",", "."
        return ".", ","

    def _remove_currency_symbols(self, text: str) -> str:
        """
        Strip all known currency codes and symbols from *text*.

        Tokens are removed longest-first to prevent partial matches (e.g.
        ``'COP'`` before ``'CO'`` if that were a token).
        """
        result = text
        for token in _ALL_CURRENCY_TOKENS:
            # Use word boundaries for alphabetic tokens to avoid false removals.
            if token.isalpha():
                result = re.sub(rf"(?i)\b{re.escape(token)}\b", "", result)
            else:
                result = result.replace(token, "")
        return result.strip()

    def _remove_non_price_text(self, text: str) -> str:
        """
        Remove any remaining non-numeric prefix/suffix text
        (e.g. ``'Precio: '``, ``'c/u'``, ``'aprox'``).

        Keeps only digits, dots, commas, and a leading minus sign.
        """
        # Extract the first number-like token from the string.
        match = re.search(r"-?\d[\d.,]*", text)
        if match:
            return match.group(0)
        return text

    def _normalize_separators(
        self,
        text: str,
        thousands_sep: str,
        decimal_sep: str,
    ) -> str:
        """
        Convert *text* to a plain Python decimal string (dot decimal, no thousands).

        Steps:
          1. Remove thousands separators.
          2. Replace decimal separator with ``'.'``.
          3. Strip any residual non-numeric characters except the dot.

        Args:
            text:          Numeric string still containing separators.
            thousands_sep: Character used as thousands grouping (``'.'`` or ``','``).
            decimal_sep:   Character used as decimal mark (``'.'`` or ``','``).

        Returns:
            String suitable for ``Decimal(...)`` construction.

        Examples:
            >>> self._normalize_separators("1.500,99", ".", ",")
            '1500.99'
            >>> self._normalize_separators("1,500.99", ",", ".")
            '1500.99'
            >>> self._normalize_separators("1500", ",", ".")
            '1500'
        """
        result = text.strip()

        if thousands_sep and decimal_sep and thousands_sep != decimal_sep:
            # Remove thousands separator first, then convert decimal.
            result = result.replace(thousands_sep, "")
            result = result.replace(decimal_sep, ".")
        elif decimal_sep and decimal_sep != ".":
            result = result.replace(decimal_sep, ".")

        # Strip anything that isn't a digit, dot, or leading minus.
        result = re.sub(r"[^\d.\-]", "", result)

        # Guard against multiple dots (shouldn't happen after normalization).
        parts = result.split(".")
        if len(parts) > 2:
            # Keep last part as decimal, join the rest as integer.
            result = "".join(parts[:-1]) + "." + parts[-1]

        return result or "0"
