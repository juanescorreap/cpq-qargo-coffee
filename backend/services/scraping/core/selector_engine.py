"""
Unified DOM selection engine for the scraping system.

Abstracts CSS / XPath complexity and provides a single interface for all
extractors. Every public method returns a clean Python value (str, Decimal,
list) so callers never have to touch Playwright handles directly.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Union

from playwright.sync_api import ElementHandle, Page

from .exceptions import ExtractionError, SelectorError
from .models import SelectorConfig, SelectorType


class SelectorEngine:
    """
    Selection engine for web pages.

    Abstracts CSS/XPath complexity and provides a unified interface.
    Supports per-selector advanced configuration and optional element caching.

    Selector config values may be:
      - A plain string: treated as a CSS selector with default options.
      - A dict: parsed into SelectorConfig (see models.py).

    Example config::

        {
            "product_name": ".title",
            "product_price": {
                "selector": ".price",
                "type": "css",
                "attribute": "data-price",
                "optional": False,
            },
            "rating": {
                "selector": "//span[@class='rating']",
                "type": "xpath",
                "optional": True,
            },
        }
    """

    def __init__(
        self,
        selectors_config: Dict[str, Any],
        logger: Optional[logging.Logger] = None,
        enable_cache: bool = False,
    ) -> None:
        """
        Args:
            selectors_config: Mapping of field name → selector definition.
            logger:           Optional logger; a default one is created if omitted.
            enable_cache:     Cache located ElementHandles to avoid repeated DOM
                              queries when multiple methods target the same field.
                              Disable for long-lived pages where the DOM mutates.
        """
        self.selectors_config = selectors_config
        self.logger = logger or self._create_default_logger()
        self.enable_cache = enable_cache
        self._element_cache: Dict[str, Optional[ElementHandle]] = {}

    # ------------------------------------------------------------------
    # Public extraction methods
    # ------------------------------------------------------------------

    def extract_text(
        self,
        page: Page,
        field: str,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """
        Extract and return the inner text of a DOM element.

        Args:
            page:    Playwright Page object.
            field:   Key in selectors_config to resolve.
            default: Value returned when element is not found and the selector
                     is marked optional. If None and element is missing, raises
                     SelectorError.

        Returns:
            Cleaned text string, or *default* if element is absent and optional.

        Raises:
            SelectorError:   Element not found and selector is not optional.
            ExtractionError: Element found but inner_text() fails.

        Examples:
            >>> engine.extract_text(page, "product_name")
            'Cappuccino Grande'

            >>> engine.extract_text(page, "optional_badge", default="")
            ''
        """
        config = self._get_selector_config(field)
        if config is None:
            self.logger.warning("No selector config for field '%s'", field)
            return default

        element = self._query_element(page, config)

        if element is None:
            if config.optional or default is not None:
                self.logger.debug("Optional field '%s' not found, using default", field)
                return default
            raise SelectorError(
                f"Required element not found for field '{field}'",
                code="SELECTOR_NOT_FOUND",
                details={"field": field, "selector": config.selector, "type": config.type},
            )

        if config.attribute:
            return self.extract_attribute(page, field, config.attribute, default)

        try:
            raw = element.inner_text()
            return self._clean_text(raw)
        except Exception as exc:
            raise ExtractionError(
                f"Failed to read text for field '{field}'",
                code="TEXT_EXTRACTION_FAILED",
                details={"field": field, "selector": config.selector},
            ) from exc

    def extract_price(
        self,
        page: Page,
        field: str,
        currency_symbol: str = "$",
    ) -> Optional[Decimal]:
        """
        Extract a price value and return it as a Decimal.

        Handles common Colombian / Latin-American formats:
          - ``$1.500``        → 1500
          - ``$1,500.99``     → 1500.99
          - ``COP 1.500,50``  → 1500.50
          - ``1500``          → 1500
          - ``1.500,00``      → 1500.00

        Args:
            page:            Playwright Page object.
            field:           Key in selectors_config.
            currency_symbol: Symbol or prefix to strip before parsing.

        Returns:
            Decimal price, or None if the field is optional and absent.

        Raises:
            SelectorError:   Required field not found.
            ExtractionError: Text found but cannot be parsed as a number.

        Examples:
            >>> engine.extract_price(page, "product_price")
            Decimal('1500.99')
        """
        config = self._get_selector_config(field)
        if config is None:
            self.logger.warning("No selector config for price field '%s'", field)
            return None

        raw_text = self.extract_text(page, field, default=None)
        if raw_text is None:
            if config.optional:
                return None
            raise SelectorError(
                f"Required price field '{field}' not found",
                code="SELECTOR_NOT_FOUND",
                details={"field": field, "selector": config.selector},
            )

        try:
            return self._clean_price(raw_text, currency_symbol)
        except (InvalidOperation, ValueError) as exc:
            raise ExtractionError(
                f"Cannot parse price for field '{field}': '{raw_text}'",
                code="PRICE_PARSE_FAILED",
                details={"field": field, "raw_text": raw_text},
            ) from exc

    def extract_elements(
        self,
        page: Page,
        field: str,
        limit: Optional[int] = None,
    ) -> List[ElementHandle]:
        """
        Return all DOM elements that match a selector (e.g. a product list).

        Args:
            page:  Playwright Page object.
            field: Key in selectors_config (should have ``multiple: true``).
            limit: Cap the result list to at most *limit* items.

        Returns:
            List of ElementHandle objects (may be empty for optional selectors).

        Raises:
            SelectorError: Required selector yields no elements.

        Examples:
            >>> products = engine.extract_elements(page, "product_list", limit=50)
            >>> for el in products:
            ...     name = engine.extract_from_element(el, "product_name")
        """
        config = self._get_selector_config(field)
        if config is None:
            self.logger.warning("No selector config for field '%s'", field)
            return []

        elements = self._query_element(page, config, multiple=True)

        if not elements:
            if config.optional:
                self.logger.debug("Optional list field '%s' returned no elements", field)
                return []
            raise SelectorError(
                f"Required list selector for field '{field}' returned no elements",
                code="SELECTOR_EMPTY_LIST",
                details={"field": field, "selector": config.selector},
            )

        self.logger.debug("Field '%s' matched %d elements", field, len(elements))

        if limit is not None:
            elements = elements[:limit]

        return elements

    def extract_from_element(
        self,
        element: ElementHandle,
        subfield: str,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """
        Extract a value from a child element within a parent ElementHandle.

        Useful when iterating over a list returned by :meth:`extract_elements`.

        Args:
            element:  Parent ElementHandle (e.g. a single product card).
            subfield: Key in selectors_config to locate the child.
            default:  Value returned when the child is not found and optional.

        Returns:
            Cleaned text string or *default*.

        Raises:
            SelectorError:   Child not found and selector is required.
            ExtractionError: Child found but text extraction fails.

        Examples:
            >>> for card in engine.extract_elements(page, "product_list"):
            ...     name = engine.extract_from_element(card, "product_name")
            ...     price_text = engine.extract_from_element(card, "product_price")
        """
        config = self._get_selector_config(subfield)
        if config is None:
            self.logger.warning("No selector config for subfield '%s'", subfield)
            return default

        try:
            if config.type == SelectorType.XPATH:
                child = element.query_selector(f"xpath={config.selector}")
            else:
                child = element.query_selector(config.selector)
        except Exception as exc:
            raise ExtractionError(
                f"Query failed for subfield '{subfield}' within element",
                code="SUBELEMENT_QUERY_FAILED",
                details={"subfield": subfield, "selector": config.selector},
            ) from exc

        if child is None:
            if config.optional or default is not None:
                return default
            raise SelectorError(
                f"Required sub-element not found for subfield '{subfield}'",
                code="SELECTOR_NOT_FOUND",
                details={"subfield": subfield, "selector": config.selector},
            )

        if config.attribute:
            value = child.get_attribute(config.attribute)
            return self._clean_text(value) if value is not None else default

        try:
            return self._clean_text(child.inner_text())
        except Exception as exc:
            raise ExtractionError(
                f"Failed to read text for subfield '{subfield}'",
                code="TEXT_EXTRACTION_FAILED",
                details={"subfield": subfield, "selector": config.selector},
            ) from exc

    def extract_attribute(
        self,
        page: Page,
        field: str,
        attribute: str,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """
        Extract an HTML attribute value from a DOM element.

        Args:
            page:      Playwright Page object.
            field:     Key in selectors_config.
            attribute: Attribute name to read (e.g. ``'href'``, ``'data-id'``).
            default:   Value returned when element or attribute is absent and
                       the selector is optional.

        Returns:
            Attribute value string or *default*.

        Raises:
            SelectorError:   Element not found and selector is required.
            ExtractionError: Element found but attribute retrieval fails.

        Examples:
            >>> url = engine.extract_attribute(page, "product_link", "href")
            '/products/cappuccino-grande'

            >>> img = engine.extract_attribute(page, "product_image", "src", default="")
            'https://cdn.example.com/img/cappuccino.jpg'
        """
        config = self._get_selector_config(field)
        if config is None:
            self.logger.warning("No selector config for field '%s'", field)
            return default

        element = self._query_element(page, config)

        if element is None:
            if config.optional or default is not None:
                return default
            raise SelectorError(
                f"Required element not found for field '{field}'",
                code="SELECTOR_NOT_FOUND",
                details={"field": field, "selector": config.selector, "attribute": attribute},
            )

        try:
            value = element.get_attribute(attribute)
        except Exception as exc:
            raise ExtractionError(
                f"Failed to get attribute '{attribute}' for field '{field}'",
                code="ATTRIBUTE_EXTRACTION_FAILED",
                details={"field": field, "selector": config.selector, "attribute": attribute},
            ) from exc

        if value is None:
            self.logger.debug(
                "Attribute '%s' not present on element for field '%s'", attribute, field
            )
            return default

        return self._clean_text(value)

    def clear_cache(self) -> None:
        """Discard all cached ElementHandles (call between page navigations)."""
        self._element_cache.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_selector_config(self, field: str) -> Optional[SelectorConfig]:
        """
        Resolve *field* to a SelectorConfig.

        Supports dot notation for nested configs: ``'metadata.rating'`` will
        look up ``selectors_config['metadata']['rating']``.

        Plain string values are treated as CSS selectors with default options.
        Dict values are passed to :class:`SelectorConfig`.

        Returns None (logged as a warning) if the field is not found.
        """
        parts = field.split(".")
        node: Any = self.selectors_config

        for part in parts:
            if not isinstance(node, dict) or part not in node:
                self.logger.warning(
                    "Selector field '%s' not found (missing key '%s')", field, part
                )
                return None
            node = node[part]

        if isinstance(node, str):
            return SelectorConfig(selector=node)

        if isinstance(node, dict):
            try:
                return SelectorConfig.from_dict(node)
            except (KeyError, ValueError) as exc:
                self.logger.error(
                    "Invalid selector config for field '%s': %s", field, exc
                )
                return None

        if isinstance(node, SelectorConfig):
            return node

        self.logger.error(
            "Unexpected selector config type for field '%s': %s", field, type(node)
        )
        return None

    def _query_element(
        self,
        page: Page,
        selector_config: SelectorConfig,
        multiple: bool = False,
    ) -> Union[ElementHandle, List[ElementHandle], None]:
        """
        Execute the selector against *page* and return the result.

        Uses the instance cache when :attr:`enable_cache` is True (single-element
        queries only; list queries are never cached).

        Args:
            page:            Playwright Page.
            selector_config: Resolved SelectorConfig.
            multiple:        When True, runs ``query_selector_all`` regardless of
                             ``selector_config.multiple``.

        Returns:
            - Single ElementHandle or None for single-element queries.
            - List[ElementHandle] (possibly empty) for multi-element queries.
        """
        use_multiple = multiple or selector_config.multiple
        selector = selector_config.selector
        sel_type = selector_config.type

        # Build the Playwright selector expression.
        if sel_type == SelectorType.XPATH:
            pw_selector = f"xpath={selector}"
        else:
            pw_selector = selector

        cache_key = f"{pw_selector}:{'multi' if use_multiple else 'single'}"

        if self.enable_cache and not use_multiple and cache_key in self._element_cache:
            self.logger.debug("Cache hit for selector '%s'", pw_selector)
            return self._element_cache[cache_key]

        try:
            if use_multiple:
                elements: List[ElementHandle] = page.query_selector_all(pw_selector)
                self.logger.debug(
                    "query_selector_all('%s') → %d elements", pw_selector, len(elements)
                )
                return elements
            else:
                element: Optional[ElementHandle] = page.query_selector(pw_selector)
                self.logger.debug(
                    "query_selector('%s') → %s",
                    pw_selector,
                    "found" if element else "not found",
                )
                if self.enable_cache:
                    self._element_cache[cache_key] = element
                return element

        except Exception as exc:
            self.logger.error(
                "Playwright query failed for selector '%s': %s", pw_selector, exc
            )
            # Treat a broken selector as "not found" so optional handling applies.
            return [] if use_multiple else None

    def _clean_text(self, text: str) -> str:
        """
        Normalize whitespace in extracted text.

        Collapses internal whitespace and strips leading/trailing spaces,
        including non-breaking spaces (``\\xa0``) common in price labels.
        """
        if not text:
            return ""
        text = text.replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()

    def _clean_price(self, price_text: str, currency_symbol: str = "$") -> Decimal:
        """
        Parse a price string into a Decimal, handling regional formats.

        Stripping steps:
          1. Remove currency symbols and common prefixes (COP, USD, EUR, …).
          2. Detect whether the separator convention is ``1.500,99`` (ES) or
             ``1,500.99`` (EN) and normalise to a plain decimal string.
          3. Remove any remaining non-numeric characters.

        Args:
            price_text:      Raw price string from the DOM.
            currency_symbol: Primary currency symbol to strip.

        Returns:
            Decimal representation of the price.

        Raises:
            ValueError: If no numeric content can be extracted.

        Examples:
            >>> engine._clean_price("$1.500", "$")
            Decimal('1500')

            >>> engine._clean_price("COP 1.500,99", "$")
            Decimal('1500.99')

            >>> engine._clean_price("1,500.99", "$")
            Decimal('1500.99')
        """
        # Step 1: strip currency labels and symbols.
        text = price_text.strip()
        for token in [currency_symbol, "COP", "USD", "EUR", "GBP", "$", "€", "£", "¥"]:
            text = text.replace(token, "")
        text = text.strip()

        if not text:
            raise ValueError(f"No numeric content after stripping currency from '{price_text}'")

        # Step 2: detect separator convention.
        # ES convention: thousands='.', decimal=','  → "1.500,99"
        # EN convention: thousands=',', decimal='.'  → "1,500.99"
        dot_pos = text.rfind(".")
        comma_pos = text.rfind(",")

        if comma_pos > dot_pos:
            # Comma is the decimal separator (ES convention): 1.500,99
            text = text.replace(".", "").replace(",", ".")
        elif dot_pos > comma_pos:
            # Dot is the decimal separator (EN convention): 1,500.99
            text = text.replace(",", "")
        else:
            # Only one separator type or none.
            # If there is a single comma with exactly 2 digits after → decimal.
            single_comma = re.fullmatch(r"\d+,\d{2}", text)
            if single_comma:
                text = text.replace(",", ".")
            else:
                # Treat as thousands separator: 1.500 → 1500
                text = text.replace(",", "").replace(".", "")

        # Step 3: remove any leftover non-numeric characters (except '.').
        text = re.sub(r"[^\d.]", "", text)

        if not text:
            raise ValueError(f"Cannot parse price from '{price_text}'")

        return Decimal(text)

    @staticmethod
    def _create_default_logger() -> logging.Logger:
        logger = logging.getLogger("scraping.selector_engine")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger
