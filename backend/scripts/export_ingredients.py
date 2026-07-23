"""Export all ingredients (active and inactive) to a review CSV.

Columns (exact order): id, name, category, purchase_price, purchase_unit,
usage_unit, conversion_factor, yield_percentage, current_price, status.

yield_percentage is exported ×100 (0.98 in DB -> 98.0 in CSV) so the user
edits it as a percentage in Excel. current_price is exported even when NULL
(empty cell).

Run:  python -m backend.scripts.export_ingredients
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from sqlalchemy import text

from backend.database import SessionLocal

COLUMNS = [
    "id", "name", "category", "purchase_price", "purchase_unit",
    "usage_unit", "conversion_factor", "yield_percentage", "current_price",
    "status",
]

OUTPUT_DIR = Path("data/exports")


def _fmt(value) -> str:
    """Empty string for NULL, plain decimal (dot, no trailing zeros noise) otherwise."""
    if value is None:
        return ""
    return str(value)


def export_ingredients(db) -> Path:
    rows = db.execute(
        text(
            """
            SELECT id, name, category, purchase_price, purchase_unit,
                   usage_unit, conversion_factor, yield_percentage,
                   current_price, is_active
            FROM ingredients
            ORDER BY id
            """
        )
    ).fetchall()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"ingredients_export_{date.today().isoformat()}.csv"

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for r in rows:
            (ingredient_id, name, category, purchase_price, purchase_unit,
             usage_unit, conversion_factor, yield_percentage,
             current_price, is_active) = r

            yield_pct = None
            if yield_percentage is not None:
                yield_pct = yield_percentage * 100

            writer.writerow([
                ingredient_id,
                name,
                _fmt(category),
                _fmt(purchase_price),
                _fmt(purchase_unit),
                _fmt(usage_unit),
                _fmt(conversion_factor),
                _fmt(yield_pct),
                _fmt(current_price),
                "active" if is_active else "inactive",
            ])

    return out_path


def main() -> None:
    db = SessionLocal()
    try:
        out_path = export_ingredients(db)
        total = sum(1 for _ in out_path.open(encoding="utf-8")) - 1
        print(f"Exported {total} ingredients -> {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
