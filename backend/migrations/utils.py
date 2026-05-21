"""Reusable helper functions for migration and data import scripts.

Typical usage:
    from backend.migrations.utils import parse_quantity_with_unit, safe_decimal, normalize_text
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Simple English plural normalisation (known recipe units)
# ---------------------------------------------------------------------------

_PLURAL_TO_SINGULAR: dict[str, str] = {
    # Plurals → singular
    "pumps":       "pump",
    "shots":       "shot",
    "scoops":      "scoop",
    "splashes":    "splash",
    "teaspoons":   "teaspoon",
    "tablespoons": "tablespoon",
    "cups":        "cup",
    "ounces":      "oz",
    "grams":       "g",
    "kilograms":   "kg",
    "liters":      "l",
    "litres":      "l",
    "milliliters": "ml",
    "millilitres": "ml",
    "pieces":      "piece",
    "units":       "unit",
    "slices":      "slice",
    "leaves":      "leaf",
    "drops":       "drop",
    "handfuls":    "handful",
    "rosettes":    "rosette",
    "servings":    "serving",
    # Common abbreviations
    "tsp":         "teaspoon",
    "tbsp":        "tablespoon",
    "tbs":         "tablespoon",
    "gr":          "g",
    "pumo":        "pump",   # frequent typo in the source Excel
}

# Regex: number + optional unit of one or more words ("2 pumps", "2 Standard Shot").
# The <unit> group always captures the LAST word, ignoring intermediate qualifiers.
_QTY_RE = re.compile(
    r"^\s*(?P<number>[0-9]+(?:[.,][0-9]+)?)"
    r"(?:\s+(?:[a-zA-Z]+\s+)*(?P<unit>[a-zA-Z]+))?"
    r"\s*$"
)


def parse_quantity_with_unit(qty_string: str) -> tuple[float | None, str | None]:
    """Parses a quantity string with an optional unit.

    Examples:
        >>> parse_quantity_with_unit("2 pumps")
        (2.0, 'pump')
        >>> parse_quantity_with_unit("240 ml")
        (240.0, 'ml')
        >>> parse_quantity_with_unit("1.5 oz")
        (1.5, 'oz')
        >>> parse_quantity_with_unit("60")
        (60.0, None)
        >>> parse_quantity_with_unit("2 Standard Shot")
        (2.0, 'shot')
        >>> parse_quantity_with_unit("4 Pump")
        (4.0, 'pump')
        >>> parse_quantity_with_unit("")
        (None, None)
        >>> parse_quantity_with_unit("invalid")
        (None, None)

    Args:
        qty_string: String with format "<number> [unit]".

    Returns:
        Tuple (quantity: float, unit: str | None).
        Returns (None, None) if the string cannot be parsed.
    """
    if not qty_string or not isinstance(qty_string, str):
        return (None, None)

    match = _QTY_RE.match(qty_string.strip())
    if not match:
        return (None, None)

    try:
        number = float(match.group("number").replace(",", "."))
    except ValueError:
        return (None, None)

    raw_unit_match = match.group("unit")
    raw_unit = raw_unit_match.strip().lower() if raw_unit_match else None

    if raw_unit:
        unit = _PLURAL_TO_SINGULAR.get(raw_unit, raw_unit)
    else:
        unit = None

    return (number, unit)


# ---------------------------------------------------------------------------

def safe_decimal(value: object) -> Decimal:
    """Converts an arbitrary value to Decimal safely.

    Examples:
        >>> safe_decimal(None)
        Decimal('0')
        >>> safe_decimal(1.5)
        Decimal('1.5')
        >>> safe_decimal("  2,500.75 ")
        Decimal('2500.75')
        >>> safe_decimal("$3.99")
        Decimal('3.99')
        >>> safe_decimal("not-a-number")
        Decimal('0')

    Args:
        value: Value to convert (None, str, int, float, Decimal).

    Returns:
        Equivalent Decimal, or Decimal("0") on any error.
    """
    if value is None:
        return Decimal("0")

    if isinstance(value, Decimal):
        return value

    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return Decimal("0")

    if isinstance(value, str):
        # Remove non-numeric characters except period, comma and negative sign
        cleaned = re.sub(r"[^\d.,-]", "", value.strip())
        # Detect format: relative position of comma and period determines the role of each.
        # US/standard  "2,500.75" → comma before period → comma = thousands, period = decimal
        # European     "1.234,56" → period before comma → period = thousands, comma = decimal
        if "," in cleaned and "." in cleaned:
            last_comma = cleaned.rfind(",")
            last_dot = cleaned.rfind(".")
            if last_comma < last_dot:
                # US format: remove thousands commas, keep decimal point
                cleaned = cleaned.replace(",", "")
            else:
                # European format: remove thousands periods, convert comma to point
                cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", ".")

        if not cleaned:
            return Decimal("0")

        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return Decimal("0")

    # Fallback for unexpected types
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("0")


# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Cleans and normalises a text string.

    Applies:
        1. Strip of leading and trailing spaces.
        2. Conversion to lowercase.
        3. Normalisation of accents/diacritics to ASCII (optional via parameter
           in the extended version; here they are kept to preserve product
           names in Spanish).

    Examples:
        >>> normalize_text("  Café Latte  ")
        'café latte'
        >>> normalize_text("ESPRESSO")
        'espresso'
        >>> normalize_text("")
        ''
        >>> normalize_text(None)
        ''

    Args:
        text: String to normalise.

    Returns:
        Clean lowercase string. Returns '' for None or on error.
    """
    if not text or not isinstance(text, str):
        return ""

    try:
        return text.strip().lower()
    except Exception:
        return ""


def normalize_text_ascii(text: str) -> str:
    """Same as normalize_text but also converts diacritics to ASCII.

    Useful for accent-insensitive comparisons and searches.

    Examples:
        >>> normalize_text_ascii("Café Latte")
        'cafe latte'
        >>> normalize_text_ascii("Açaí")
        'acai'
    """
    if not text or not isinstance(text, str):
        return ""

    try:
        normalized = unicodedata.normalize("NFD", text.strip().lower())
        return "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    except Exception:
        return ""
