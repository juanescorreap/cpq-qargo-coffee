"""
Exceptions for the scraping system.

Usage example:
    try:
        element = page.query_selector('.price')
        if not element:
            raise SelectorError(
                "Price element not found",
                code="SELECTOR_NOT_FOUND",
                details={'selector': '.price', 'url': url}
            )
    except SelectorError as e:
        logger.error(f"Scraping failed: {e.code} - {e.message}")
"""


class ScrapingException(Exception):
    """Base exception for the scraping system."""

    def __init__(self, message: str, code: str = None, details: dict = None):
        self.message = message
        self.code = code or "SCRAPING_ERROR"
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self) -> str:
        base = f"[{self.code}] {self.message}"
        if self.details:
            return f"{base} | details: {self.details}"
        return base

    def to_dict(self) -> dict:
        return {
            "exception": self.__class__.__name__,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(ScrapingException):
    """
    Raised when a scraper YAML config file is missing, malformed, or has
    invalid/missing required fields.

    Use when:
        - Config file not found on disk
        - YAML syntax is invalid
        - Required field (e.g. 'selectors', 'url') is absent
    """

    def __init__(self, message: str, code: str = None, details: dict = None):
        super().__init__(message, code or "CONFIG_ERROR", details)


class ScraperNotFoundError(ScrapingException):
    """
    Raised when a scraper is requested by name/ID but no matching
    implementation or config exists in the registry.

    Use when:
        - Caller requests scraper 'competitor_x' but it is not registered
        - Dynamic loading of a scraper class fails
    """

    def __init__(self, message: str, code: str = None, details: dict = None):
        super().__init__(message, code or "SCRAPER_NOT_FOUND", details)


class NavigationError(ScrapingException):
    """
    Raised when the browser fails to reach or load the target URL.

    Use when:
        - Network timeout or connection refused
        - HTTP error response (4xx / 5xx)
        - Page never reaches a usable state (e.g. infinite spinner)
    """

    def __init__(self, message: str, code: str = None, details: dict = None):
        super().__init__(message, code or "NAVIGATION_ERROR", details)


class SelectorError(ScrapingException):
    """
    Raised when a CSS or XPath selector finds no element on the page.

    Use when:
        - query_selector / xpath returns None for a required element
        - Expected number of elements does not match
        - Selector is valid syntax but targets nothing in the current DOM
    """

    def __init__(self, message: str, code: str = None, details: dict = None):
        super().__init__(message, code or "SELECTOR_NOT_FOUND", details)


class ExtractionError(ScrapingException):
    """
    Raised when an element is found but its data cannot be extracted or parsed.

    Use when:
        - Text content is present but cannot be cast to the expected type
        - An attribute (href, src, data-*) is missing from an existing element
        - Post-processing / regex match fails on extracted raw text
    """

    def __init__(self, message: str, code: str = None, details: dict = None):
        super().__init__(message, code or "EXTRACTION_ERROR", details)


class RateLimitError(ScrapingException):
    """
    Raised when the target site signals that the scraper is being throttled.

    Use when:
        - HTTP 429 response is received
        - Site returns a CAPTCHA or block page
        - Retry-After header indicates a cooldown period
    """

    def __init__(self, message: str, code: str = None, details: dict = None):
        super().__init__(message, code or "RATE_LIMIT_EXCEEDED", details)


class ValidationError(ScrapingException):
    """
    Raised when scraped data does not meet business-rule constraints after
    extraction (i.e. the data was extracted but is logically invalid).

    Use when:
        - Price is negative or non-numeric after parsing
        - Required field is empty string
        - Cross-field invariant is violated (e.g. sale_price > regular_price)
    """

    def __init__(self, message: str, code: str = None, details: dict = None):
        super().__init__(message, code or "VALIDATION_ERROR", details)
