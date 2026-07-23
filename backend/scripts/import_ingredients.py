"""Bulk-load a normalized ingredients CSV back into the database.

Compares each row against the current DB row and only touches fields that
actually changed:

- current_price: NEVER updated directly. Changes are written as a new
  ingredient_price_history row; trg_iph_sync_current_price denormalizes it
  back onto ingredients.current_price.
- All other editable fields (name, purchase_price, purchase_unit,
  usage_unit, conversion_factor, yield_percentage): a single UPDATE with
  only the changed columns in the SET clause.

Empty CSV cells mean "keep the current DB value" — never write NULL over
existing data. Inactive ingredients (is_active = false) are never touched.
category/status columns are present in the CSV for context but ignored on
load; id is the only matching key (never name).

Run:
  # Preview only, no DB writes:
  python -m backend.scripts.import_ingredients data/exports/ingredients_normalized_2026-07-10.csv --dry-run

  # Real load:
  python -m backend.scripts.import_ingredients data/exports/ingredients_normalized_2026-07-10.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import text

from backend.database import SessionLocal

OUTPUT_DIR = Path("data/exports")

EDITABLE_NUMERIC_FIELDS = [
    "purchase_price", "purchase_unit", "usage_unit",
    "conversion_factor", "yield_percentage",
]


def _dec(value: str) -> Decimal | None:
    value = (value or "").strip()
    if not value:
        return None
    return Decimal(value)


class RowResult:
    def __init__(self):
        self.skipped_inactive = False
        self.skipped_warning: str | None = None
        self.error: str | None = None
        self.fields_changed: list[str] = []
        self.price_changed = False


def _load_db_row(db, ingredient_id: int) -> dict | None:
    row = db.execute(
        text(
            """
            SELECT id, name, purchase_price, purchase_unit, usage_unit,
                   conversion_factor, yield_percentage, current_price,
                   is_active
            FROM ingredients
            WHERE id = :id
            """
        ),
        {"id": ingredient_id},
    ).mappings().first()
    return dict(row) if row else None


def _name_taken_by_other(db, name: str, ingredient_id: int) -> int | None:
    row = db.execute(
        text(
            """
            SELECT id FROM ingredients
            WHERE lower(name) = lower(:name)
              AND is_active = true
              AND id != :id
            LIMIT 1
            """
        ),
        {"name": name, "id": ingredient_id},
    ).first()
    return row[0] if row else None


def process_row(db, raw_row: dict, dry_run: bool, counters: Counter) -> RowResult:
    result = RowResult()

    try:
        ingredient_id = int(raw_row["id"])
    except (KeyError, ValueError, TypeError):
        result.skipped_warning = f"row id={raw_row.get('id')!r}: invalid id -> SKIPPED"
        return result

    db_row = _load_db_row(db, ingredient_id)
    if db_row is None:
        result.skipped_warning = f"id={ingredient_id}: id not found in DB -> SKIPPED"
        return result

    if not db_row["is_active"]:
        result.skipped_inactive = True
        return result

    name = (raw_row.get("name") or "").strip()
    if not name:
        result.skipped_warning = f"id={ingredient_id}: name is empty -> SKIPPED"
        return result

    try:
        conversion_factor = _dec(raw_row.get("conversion_factor", ""))
        yield_pct = _dec(raw_row.get("yield_percentage", ""))
        purchase_price = _dec(raw_row.get("purchase_price", ""))
        current_price = _dec(raw_row.get("current_price", ""))
    except InvalidOperation:
        result.skipped_warning = f"id={ingredient_id}: unparseable numeric field -> SKIPPED"
        return result

    if conversion_factor is not None and conversion_factor <= 0:
        result.skipped_warning = (
            f"id={ingredient_id}: conversion_factor {conversion_factor} <= 0 -> SKIPPED"
        )
        return result

    if yield_pct is not None and not (Decimal("0.1") <= yield_pct <= Decimal("100")):
        result.skipped_warning = (
            f"id={ingredient_id}: yield_percentage {yield_pct} out of [0.1, 100] -> SKIPPED"
        )
        return result

    if current_price is not None and current_price <= 0:
        result.skipped_warning = (
            f"id={ingredient_id}: current_price {current_price} <= 0 -> SKIPPED"
        )
        return result

    if name != db_row["name"]:
        other_id = _name_taken_by_other(db, name, ingredient_id)
        if other_id is not None:
            result.skipped_warning = (
                f"id={ingredient_id}: name {name!r} already exists for id={other_id} -> SKIPPED"
            )
            return result

    usage_unit = raw_row.get("usage_unit", "")
    usage_unit = usage_unit.strip() if usage_unit else None
    purchase_unit = raw_row.get("purchase_unit", "")
    purchase_unit = purchase_unit.strip() if purchase_unit else None

    set_clauses = []
    params = {"id": ingredient_id}

    if name != db_row["name"]:
        set_clauses.append("name = :name")
        params["name"] = name
        result.fields_changed.append("name")

    if purchase_price is not None and purchase_price != db_row["purchase_price"]:
        set_clauses.append("purchase_price = :purchase_price")
        params["purchase_price"] = purchase_price
        result.fields_changed.append("purchase_price")

    if purchase_unit is not None and purchase_unit != (db_row["purchase_unit"] or None):
        set_clauses.append("purchase_unit = :purchase_unit")
        params["purchase_unit"] = purchase_unit
        result.fields_changed.append("purchase_unit")

    if usage_unit is not None and usage_unit != (db_row["usage_unit"] or None):
        set_clauses.append("usage_unit = :usage_unit")
        params["usage_unit"] = usage_unit
        result.fields_changed.append("usage_unit")

    if conversion_factor is not None and conversion_factor != db_row["conversion_factor"]:
        set_clauses.append("conversion_factor = :conversion_factor")
        params["conversion_factor"] = conversion_factor
        result.fields_changed.append("conversion_factor")

    if yield_pct is not None:
        yield_fraction = yield_pct / Decimal("100")
        if yield_fraction != db_row["yield_percentage"]:
            set_clauses.append("yield_percentage = :yield_percentage")
            params["yield_percentage"] = yield_fraction
            result.fields_changed.append("yield_percentage")

    if set_clauses:
        counters["fields:" + ",".join(result.fields_changed)] += 0  # no-op, kept for clarity
        for f in result.fields_changed:
            counters[f] += 1
        if not dry_run:
            sql = "UPDATE ingredients SET " + ", ".join(set_clauses) + ", updated_at = now() " \
                  "WHERE id = :id AND is_active = true"
            db.execute(text(sql), params)

    if current_price is not None and current_price != db_row["current_price"]:
        result.price_changed = True
        counters["current_price"] += 1
        if not dry_run:
            db.execute(
                text(
                    """
                    INSERT INTO ingredient_price_history (ingredient_id, price, source)
                    VALUES (:id, :price, 'bulk_import')
                    """
                ),
                {"id": ingredient_id, "price": current_price},
            )

    if not dry_run and (set_clauses or result.price_changed):
        db.commit()
    elif not dry_run:
        db.rollback()

    return result


def run_import(input_path: Path, dry_run: bool) -> str:
    db = SessionLocal()
    counters: Counter = Counter()
    warnings: list[str] = []
    errors: list[str] = []
    total = 0
    processed = 0
    skipped_inactive = 0

    try:
        with input_path.open(newline="", encoding="utf-8") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect)
            for raw_row in reader:
                total += 1
                try:
                    result = process_row(db, raw_row, dry_run, counters)
                except Exception as exc:
                    db.rollback()
                    errors.append(f"id={raw_row.get('id')}: {exc}")
                    continue

                if result.skipped_inactive:
                    skipped_inactive += 1
                    continue
                if result.skipped_warning:
                    warnings.append(result.skipped_warning)
                    continue
                processed += 1
    finally:
        db.close()

    lines = []
    mode = "DRY RUN" if dry_run else "LIVE"
    lines.append(f"=== BULK IMPORT ({mode}) — {datetime.now():%Y-%m-%d %H:%M:%S} ===")
    lines.append(f"Source: {input_path}")
    lines.append(f"Total rows in CSV: {total}")
    lines.append(f"Rows processed: {processed}")
    lines.append(f"Rows skipped (inactive): {skipped_inactive}")
    lines.append(f"Rows skipped (warnings): {len(warnings)}")
    lines.append("")
    lines.append("Changes applied:" if not dry_run else "Changes that would be applied:")
    for field in ["name", "purchase_price", "purchase_unit", "usage_unit",
                   "conversion_factor", "yield_percentage"]:
        lines.append(f"  - {field} updated: {counters.get(field, 0)}")
    lines.append(f"  - current_price updated (via history): {counters.get('current_price', 0)}")
    lines.append("")
    lines.append("Warnings:")
    if warnings:
        for w in warnings:
            lines.append(f"  - {w}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Errors: {len(errors)}")
    for e in errors:
        lines.append(f"  - {e}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = run_import(args.input_csv, dry_run=args.dry_run)
    print(report)

    if not args.dry_run:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        log_path = OUTPUT_DIR / f"import_log_{datetime.now():%Y-%m-%d-%H%M%S}.txt"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\nLog appended -> {log_path}")


if __name__ == "__main__":
    main()
