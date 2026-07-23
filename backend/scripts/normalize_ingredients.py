"""Normalize ingredient `name` and `purchase_unit` from an exported CSV.

Applies the rules from ingredients_bulk_plan.md §Paso 2, in the documented
order:

  name:           Regla 1 Title Case -> Regla 2 remove brand prefix ->
                  Regla 3 remove size specs -> Regla 4 special chars.
  purchase_unit:  Regla 1 volume units -> Regla 2 weight units ->
                  Regla 3 pack counts (x -> x) -> Regla 4 case/pack words ->
                  Regla 5 strip/whitespace.

Numeric columns (conversion_factor, yield_percentage, purchase_price,
current_price) are copied through unchanged.

Run:
  python -m backend.scripts.normalize_ingredients data/exports/ingredients_export_2026-07-10.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import date
from pathlib import Path

COLUMNS = [
    "id", "name", "category", "purchase_price", "purchase_unit",
    "usage_unit", "conversion_factor", "yield_percentage", "current_price",
    "status",
]

OUTPUT_DIR = Path("data/exports")

MINOR_WORDS = {
    "and", "or", "of", "the", "a", "an", "with", "de", "di", "al", "la", "le",
}
ACRONYMS = {"RTB", "RTE", "PET", "USA", "NYC"}
BRAND_EXCEPTIONS = {"aiya", "pregel", "lotus", "califia"}


# ─────────────────────────────────────────────────────────────────
# Name normalization
# ─────────────────────────────────────────────────────────────────

def _title_case_word(word: str, is_first: bool) -> str:
    core = re.sub(r"[^A-Za-z]", "", word)
    if not core:
        return word
    if core.upper() in ACRONYMS:
        return "".join(c.upper() if c.isalpha() else c for c in word)
    if not is_first and core.lower() in MINOR_WORDS:
        return "".join(c.lower() if c.isalpha() else c for c in word)

    chars = list(word)
    seen_alpha = False
    for i, c in enumerate(chars):
        if c.isalpha():
            chars[i] = c.upper() if not seen_alpha else c.lower()
            seen_alpha = True
    return "".join(chars)


def _rule1_title_case(name: str) -> str:
    words = name.split(" ")
    return " ".join(
        _title_case_word(w, is_first=(i == 0)) for i, w in enumerate(words)
    )


_BRAND_PREFIX_RE = re.compile(r"^([^-–]+?)\s[-–]\s(.*)$")


def _rule2_remove_brand_prefix(name: str) -> str:
    m = _BRAND_PREFIX_RE.match(name)
    if not m:
        return name
    brand, rest = m.group(1), m.group(2)
    brand_core = re.sub(r"[^A-Za-z]", "", brand).lower()
    if brand_core in BRAND_EXCEPTIONS:
        return name
    return rest


_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
_TRAILING_SIZE_RE = re.compile(
    r"\s*[-–]?\s*\d+(?:\.\d+)?\s*(?:oz|ml|mL|l|L|lt|Lt|lb|lbs|kg|g)\.?\s*$",
    re.IGNORECASE,
)
_TRAILING_XCOUNT_RE = re.compile(r"\s*[-–]?\s*[xX]\s*\d+\s*$")


def _rule3_remove_size_specs(name: str) -> str:
    prev = None
    while prev != name:
        prev = name
        name = _TRAILING_PAREN_RE.sub("", name)
        name = _TRAILING_SIZE_RE.sub("", name)
        name = _TRAILING_XCOUNT_RE.sub("", name)
    return name


_DANGLING_SEP_RE = re.compile(r"^[\s\-–]+|[\s\-–]+$")
_DOUBLE_SPACE_RE = re.compile(r"\s{2,}")


def _rule4_special_chars(name: str) -> str:
    name = name.replace('"', "")
    name = name.replace("–", "-")
    name = _DOUBLE_SPACE_RE.sub(" ", name)
    name = _DANGLING_SEP_RE.sub("", name)
    return name.strip()


def normalize_name(name: str) -> str:
    name = _rule1_title_case(name)
    name = _rule2_remove_brand_prefix(name)
    name = _rule3_remove_size_specs(name)
    name = _rule4_special_chars(name)
    return name


# ─────────────────────────────────────────────────────────────────
# purchase_unit normalization
# ─────────────────────────────────────────────────────────────────

_ML_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m[lL]\b", re.IGNORECASE)
_LT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[lL][tT]\b", re.IGNORECASE)
_L_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[lL]\b", re.IGNORECASE)
_OZ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*oz\.?\b", re.IGNORECASE)
_LB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*lbs?\b", re.IGNORECASE)
_KG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*kg\b", re.IGNORECASE)

_XX_DIGIT_DIGIT_RE = re.compile(r"(\d)\s*[xX]\s*(\d)")
_XX_LEADING_RE = re.compile(r"(?<!\w)[xX]\s*(\d)")

_WORD_SUBS = [
    (re.compile(r"\bcases?\b", re.IGNORECASE), "case"),
    (re.compile(r"\bpieces?\b", re.IGNORECASE), "piece"),
    (re.compile(r"\b(?:ea|each)\b", re.IGNORECASE), "each"),
    (re.compile(r"\bbottle\b", re.IGNORECASE), "bottle"),
    (re.compile(r"\bbox\b", re.IGNORECASE), "box"),
]


def normalize_purchase_unit(unit: str) -> str:
    if "/" in unit:
        # Compound slash notation (e.g. "6/4ct / 11 oz") — left as-is per plan.
        return unit

    s = unit
    s = _ML_RE.sub(lambda m: f"{m.group(1)} ml", s)
    s = _LT_RE.sub(lambda m: f"{m.group(1)} L", s)
    s = _L_RE.sub(lambda m: f"{m.group(1)} L", s)
    s = _OZ_RE.sub(lambda m: f"{m.group(1)} oz", s)
    s = _LB_RE.sub(lambda m: f"{m.group(1)} lb", s)
    s = _KG_RE.sub(lambda m: f"{m.group(1)} kg", s)

    s = _XX_DIGIT_DIGIT_RE.sub(lambda m: f"{m.group(1)} × {m.group(2)}", s)
    s = _XX_LEADING_RE.sub(lambda m: f"× {m.group(1)}", s)

    for pattern, repl in _WORD_SUBS:
        s = pattern.sub(repl, s)

    s = _DOUBLE_SPACE_RE.sub(" ", s)
    return s.strip()


# ─────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────

def normalize_file(input_path: Path) -> tuple[Path, Path, int, int]:
    with input_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    normalized_path = OUTPUT_DIR / f"ingredients_normalized_{today}.csv"
    changes_path = OUTPUT_DIR / f"ingredients_changes_{today}.csv"

    changes = []
    normalized_rows = []

    for row in rows:
        name_original = row["name"]
        unit_original = row["purchase_unit"] or ""

        name_normalized = normalize_name(name_original)
        unit_normalized = (
            normalize_purchase_unit(unit_original) if unit_original else ""
        )

        out_row = dict(row)
        out_row["name"] = name_normalized
        out_row["purchase_unit"] = unit_normalized
        normalized_rows.append(out_row)

        if name_normalized != name_original or unit_normalized != unit_original:
            changes.append({
                "id": row["id"],
                "name_original": name_original,
                "name_normalized": name_normalized,
                "unit_original": unit_original,
                "unit_normalized": unit_normalized,
            })

    with normalized_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(normalized_rows)

    with changes_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id", "name_original", "name_normalized",
                "unit_original", "unit_normalized",
            ],
        )
        writer.writeheader()
        writer.writerows(changes)

    return normalized_path, changes_path, len(rows), len(changes)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    args = parser.parse_args()

    normalized_path, changes_path, total, changed = normalize_file(args.input_csv)
    print(f"Rows read: {total}")
    print(f"Rows changed: {changed}")
    print(f"Normalized CSV -> {normalized_path}")
    print(f"Changes CSV    -> {changes_path}")


if __name__ == "__main__":
    main()
