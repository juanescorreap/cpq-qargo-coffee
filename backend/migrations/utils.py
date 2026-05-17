"""Funciones auxiliares reutilizables para scripts de migración e importación de datos.

Uso típico:
    from backend.migrations.utils import parse_quantity_with_unit, safe_decimal, normalize_text
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Normalización de plurales simples en inglés (unidades de receta conocidas)
# ---------------------------------------------------------------------------

_PLURAL_TO_SINGULAR: dict[str, str] = {
    # Plurales → singular
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
    # Abreviaturas comunes
    "tsp":         "teaspoon",
    "tbsp":        "tablespoon",
    "tbs":         "tablespoon",
    "gr":          "g",
    "pumo":        "pump",   # typo frecuente en el Excel de origen
}

# Regex: número + unidad opcional de una o varias palabras ("2 pumps", "2 Standard Shot").
# El grupo <unit> captura siempre la ÚLTIMA palabra, ignorando calificadores intermedios.
_QTY_RE = re.compile(
    r"^\s*(?P<number>[0-9]+(?:[.,][0-9]+)?)"
    r"(?:\s+(?:[a-zA-Z]+\s+)*(?P<unit>[a-zA-Z]+))?"
    r"\s*$"
)


def parse_quantity_with_unit(qty_string: str) -> tuple[float | None, str | None]:
    """Parsea un string de cantidad con unidad opcional.

    Ejemplos:
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
        qty_string: String con formato "<número> [unidad]".

    Returns:
        Tupla (cantidad: float, unidad: str | None).
        Retorna (None, None) si el string no puede parsearse.
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
    """Convierte un valor arbitrario a Decimal de forma segura.

    Ejemplos:
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
        value: Valor a convertir (None, str, int, float, Decimal).

    Returns:
        Decimal equivalente, o Decimal("0") ante cualquier error.
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
        # Eliminar caracteres no numéricos salvo punto, coma y signo negativo
        cleaned = re.sub(r"[^\d.,-]", "", value.strip())
        # Detectar formato: posición relativa de coma y punto determina el rol de cada uno.
        # US/estándar  "2,500.75" → coma antes del punto → coma = miles, punto = decimal
        # Europeo      "1.234,56" → punto antes de coma  → punto = miles, coma = decimal
        if "," in cleaned and "." in cleaned:
            last_comma = cleaned.rfind(",")
            last_dot = cleaned.rfind(".")
            if last_comma < last_dot:
                # Formato US: quitar comas de miles, mantener punto decimal
                cleaned = cleaned.replace(",", "")
            else:
                # Formato europeo: quitar puntos de miles, convertir coma a punto
                cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", ".")

        if not cleaned:
            return Decimal("0")

        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return Decimal("0")

    # Fallback para tipos inesperados
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("0")


# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Limpia y normaliza un string de texto.

    Aplica:
        1. Strip de espacios al inicio y final.
        2. Conversión a minúsculas.
        3. Normalización de tildes/diacríticos a ASCII (opcional vía parámetro
           en la versión extendida; aquí se mantienen para preservar nombres
           de productos en español).

    Ejemplos:
        >>> normalize_text("  Café Latte  ")
        'café latte'
        >>> normalize_text("ESPRESSO")
        'espresso'
        >>> normalize_text("")
        ''
        >>> normalize_text(None)
        ''

    Args:
        text: String a normalizar.

    Returns:
        String limpio y en minúsculas. Retorna '' ante None o error.
    """
    if not text or not isinstance(text, str):
        return ""

    try:
        return text.strip().lower()
    except Exception:
        return ""


def normalize_text_ascii(text: str) -> str:
    """Igual que normalize_text pero además convierte diacríticos a ASCII.

    Útil para comparaciones y búsquedas insensibles a tildes.

    Ejemplos:
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
