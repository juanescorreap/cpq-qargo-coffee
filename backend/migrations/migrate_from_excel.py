"""Bulk data migration from Excel files to the CPQ system.

Run with:
    python -m backend.migrations.migrate_from_excel

Each function reads an Excel file from 'data/raw/' and performs a bulk insert
into the database using the SQLAlchemy session. Row-level errors are reported
as warnings so that a badly formatted row does not interrupt the full load.
"""

import csv
import re
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import func

import backend.models  # noqa: F401 — registers all models in Base.metadata
from backend.database import SessionLocal
from backend.migrations.utils import safe_decimal
from backend.models.category import Category
from backend.models.ingredient import Ingredient
from backend.models.product import (
    Product,
    ProductSize,
    RecipeIngredient,
    RecipeSubRecipe,
)
from backend.models.recipe_unit import IngredientRecipeUnitConversion, RecipeUnit

# Project root (two levels above this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RAW_DIR = _PROJECT_ROOT / "data" / "raw"
_TEMPLATES_DIR = _PROJECT_ROOT / "data" / "templates"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_NAN_LIKE = {None, "", "nan", "NaT", "NaN", "none", "null"}


def _is_empty(value: object) -> bool:
    """Returns True if the value is NaN, None or an empty string."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN check
        return True
    return str(value).strip().lower() in _NAN_LIKE


def _clean_str(value: object) -> str | None:
    """Converts to a clean str, or None if the value is empty."""
    if _is_empty(value):
        return None
    return str(value).strip() or None


def _to_bool(value: object, default: bool = False) -> bool:
    """Converts "TRUE"/"FALSE" strings (and variants) to bool.

    Examples:
        >>> _to_bool("TRUE")  → True
        >>> _to_bool("False") → False
        >>> _to_bool("1")     → True
        >>> _to_bool(None)    → False  (default)
    """
    if _is_empty(value):
        return default
    return str(value).strip().lower() in {"true", "1", "yes", "sí", "si"}


def _norm_slug(value: object) -> str | None:
    """Normalise a category value to the canonical slug convention: lower-case,
    trimmed, internal whitespace/hyphens collapsed to single underscores. So
    ``"Bebidas Calientes"`` / ``"bebidas-calientes"`` → ``"bebidas_calientes"``.
    Returns None for empties. Matches seed_data._CATEGORIES + category_margins so
    products FK-resolve AND markup resolution finds the category."""
    s = _clean_str(value)
    if s is None:
        return None
    return re.sub(r"[\s\-]+", "_", s.strip().lower()) or None


# ---------------------------------------------------------------------------
# migrate_ingredients
# ---------------------------------------------------------------------------

def migrate_ingredients() -> None:
    """Loads ingredients from 'data/raw/ingredientes.xlsx' (or ingredients.xlsx).

    Expected Excel format
    ---------------------
    The file must have a sheet with at least these columns
    (order does not matter; names are case-insensitive after strip):

    | Column            | Type    | Description                                      |
    |-------------------|---------|--------------------------------------------------|
    | nombre            | str     | Ingredient name. **Required.**                   |
    | categoria         | str     | Category (e.g.: "dairy", "syrups").              |
    | unidad_compra     | str     | Purchase unit (e.g.: "1L box").                  |
    | precio_compra     | number  | Price per purchase unit (COP or local currency)  |
    | unidad_uso        | str     | Unit used in recipes (e.g.: "ml", "g").          |
    | factor_conversion | number  | Usage units per purchase unit.                   |
    | yield_%           | number  | Yield percentage (0-100). Default 100.           |
    | url_proveedor     | str     | Supplier URL for scraping (optional).            |

    Rows without 'nombre' are skipped with a warning.
    Rows with conversion errors are skipped with a warning but do not stop
    the full process.

    Examples of valid values:
        nombre="Whole milk", categoria="dairy", precio_compra="4500",
        unidad_compra="litre", unidad_uso="ml", factor_conversion="1000", yield_%=98
    """
    # ------------------------------------------------------------------
    # 1. Locate the file (accepts two file names)
    # ------------------------------------------------------------------
    candidates = [
        _RAW_DIR / "ingredientes.xlsx",
        _RAW_DIR / "ingredients.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)

    if excel_path is None:
        tried = " or ".join(str(p) for p in candidates)
        print(f"❌ File not found: {tried}")
        return

    # ------------------------------------------------------------------
    # 2. Read the Excel
    # ------------------------------------------------------------------
    try:
        df = pd.read_excel(excel_path, dtype=str)  # dtype=str avoids automatic conversions
    except Exception as exc:
        print(f"❌ Could not read '{excel_path.name}': {exc}")
        return

    # Normalize column names: strip + lowercase
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Aliases: accepts both Spanish and English names
    _COL_ALIASES: dict[str, str] = {
        "name":               "nombre",
        "category":           "categoria",
        "purchase_unit":      "unidad_compra",
        "purchase_price":     "precio_compra",
        "usage_unit":         "unidad_uso",
        "conversion_factor":  "factor_conversion",
        "supplier_url":       "url_proveedor",
    }
    df.rename(columns=_COL_ALIASES, inplace=True)

    expected_cols = {
        "nombre", "categoria", "unidad_compra", "precio_compra",
        "unidad_uso", "factor_conversion", "yield_%", "canonical_unit",
        "url_proveedor",
    }
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Missing columns in Excel: {', '.join(sorted(missing))}")
        print("   Migration continues using None for absent columns.")

    total_rows = len(df)
    objects: list[Ingredient] = []
    skipped = 0

    # ------------------------------------------------------------------
    # 3. Process row by row
    # ------------------------------------------------------------------
    for idx, row in df.iterrows():
        row_num = idx + 2  # +2: header on row 1, data from row 2

        try:
            nombre = _clean_str(row.get("nombre"))
            if not nombre:
                print(f"  ⚠️  Row {row_num}: 'nombre' is empty — skipped.")
                skipped += 1
                continue

            raw_yield = row.get("yield_%")
            yield_pct: Decimal = (
                Decimal("1")
                if _is_empty(raw_yield)
                else safe_decimal(raw_yield)
            )
            # Guard: yield must be a fraction in (0, 1] — 1.0 = no waste.
            if not (Decimal("0") < yield_pct <= Decimal("1")):
                print(
                    f"  ⚠️  Row {row_num} ({nombre!r}): "
                    f"invalid yield_% ({yield_pct}), using 1.0."
                )
                yield_pct = Decimal("1")

            ingredient = Ingredient(
                name=nombre,
                category=_clean_str(row.get("categoria")),
                purchase_unit=_clean_str(row.get("unidad_compra")),
                purchase_price=safe_decimal(row.get("precio_compra")) or None,
                usage_unit=_clean_str(row.get("unidad_uso")),
                conversion_factor=safe_decimal(row.get("factor_conversion")) or None,
                yield_percentage=yield_pct,
                canonical_unit=_clean_str(row.get("canonical_unit")),
                source_url=_clean_str(row.get("url_proveedor")),
            )
            objects.append(ingredient)

        except Exception as exc:
            print(f"  ⚠️  Row {row_num}: unexpected error ({exc}) — skipped.")
            skipped += 1

    # ------------------------------------------------------------------
    # 4. Bulk insert + commit
    # ------------------------------------------------------------------
    if not objects:
        print(f"⚠️  No valid rows to insert ({skipped} skipped out of {total_rows}).")
        return

    db = SessionLocal()
    try:
        db.bulk_save_objects(objects)
        db.commit()
        inserted = len(objects)
        print(f"✅ Migrated {inserted} ingredients ({skipped} skipped out of {total_rows} rows)")
    except Exception as exc:
        db.rollback()
        print(f"❌ Commit error: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_products
# ---------------------------------------------------------------------------

def migrate_products() -> None:
    """Loads products from 'data/raw/productos.xlsx' (or products.xlsx).

    Expected Excel format
    ---------------------
    | Column           | English alias       | Type    | Description                              |
    |------------------|---------------------|---------|------------------------------------------|
    | nombre           | name                | str     | Product name. **Required.**              |
    | categoria        | category            | str     | Category (e.g.: "Hot Classics").         |
    | tamaño_base_oz   | base_size_oz        | number  | Base size in oz for recipe scaling.      |
    | tiempo_prep_min  | prep_time_min       | number  | Preparation time in minutes.             |
    | costo_labor_min  | labor_cost_per_min  | number  | Cost per minute of labor.                |
    | es_sub_receta    | is_sub_recipe       | bool    | "TRUE"/"FALSE". Default False.           |

    Rows without 'nombre' are skipped with a warning.
    """
    candidates = [
        _RAW_DIR / "productos.xlsx",
        _RAW_DIR / "products.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)
    if excel_path is None:
        tried = " or ".join(str(p) for p in candidates)
        print(f"❌ File not found: {tried}")
        return

    try:
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as exc:
        print(f"❌ Could not read '{excel_path.name}': {exc}")
        return

    df.columns = [str(c).strip().lower() for c in df.columns]

    _COL_ALIASES: dict[str, str] = {
        "name":               "nombre",
        "category":           "categoria",
        "category_slug":      "categoria",   # preferred header: value must be a categories.slug
        "base_size_oz":       "tamaño_base_oz",
        "prep_time_min":      "tiempo_prep_min",
        "labor_cost_per_min": "costo_labor_min",
        "is_sub_recipe":      "es_sub_receta",
    }
    df.rename(columns=_COL_ALIASES, inplace=True)

    expected_cols = {"nombre", "categoria", "tamaño_base_oz", "tiempo_prep_min", "costo_labor_min", "es_sub_receta"}
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Missing columns in Excel: {', '.join(sorted(missing))}")
        print("   Migration continues using None/False for absent columns.")

    total_rows = len(df)
    objects: list[Product] = []
    skipped = 0

    db = SessionLocal()
    try:
        # products.category is an FK to categories.slug. Resolve each row's
        # category against the seeded taxonomy (normalised to the canonical slug).
        # An unknown category does NOT abort the whole bulk insert (which is
        # all-or-nothing): the product loads with category=NULL + a warning, so a
        # single stray value can't zero out the entire catalogue. Run seed_data
        # (seed_categories) first so the canonical slugs exist.
        known_slugs: set[str] = {s for (s,) in db.query(Category.slug).all()}

        for idx, row in df.iterrows():
            row_num = idx + 2

            try:
                nombre = _clean_str(row.get("nombre"))
                if not nombre:
                    print(f"  ⚠️  Row {row_num}: 'nombre' is empty — skipped.")
                    skipped += 1
                    continue

                slug = _norm_slug(row.get("categoria"))
                if slug is not None and slug not in known_slugs:
                    print(
                        f"  ⚠️  Row {row_num} ({nombre!r}): category '{slug}' not in "
                        f"categories — loading with category=NULL."
                    )
                    slug = None

                objects.append(
                    Product(
                        name=nombre,
                        category=slug,
                        base_size_oz=safe_decimal(row.get("tamaño_base_oz")) or None,
                        prep_time_minutes=safe_decimal(row.get("tiempo_prep_min")) or None,
                        labor_cost_per_minute=safe_decimal(row.get("costo_labor_min")),
                        is_sub_recipe=_to_bool(row.get("es_sub_receta")),
                    )
                )

            except Exception as exc:
                print(f"  ⚠️  Row {row_num}: unexpected error ({exc}) — skipped.")
                skipped += 1

        if not objects:
            print(f"⚠️  No valid rows to insert ({skipped} skipped out of {total_rows}).")
            return

        db.bulk_save_objects(objects)
        db.commit()
        print(f"✅ Migrated {len(objects)} products ({skipped} skipped out of {total_rows} rows)")
    except Exception as exc:
        db.rollback()
        print(f"❌ Commit error: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_product_sizes
# ---------------------------------------------------------------------------

def migrate_product_sizes() -> None:
    """Loads size variants from 'data/raw/tamaños.xlsx' (or sizes.xlsx).

    Expected Excel format
    ---------------------
    | Column          | English alias  | Type   | Description                                    |
    |-----------------|----------------|--------|------------------------------------------------|
    | nombre_producto | product_name   | str    | Exact product name in DB. Required.            |
    | tamaño          | size           | str    | Size name (e.g.: "Small", "Large").            |
    | volumen_oz      | volume_oz      | number | Volume in oz for this size.                    |
    | factor_escala   | scale_factor   | number | Multiplier vs. base size (base = 1.0).         |
    | es_default      | is_default     | bool   | "TRUE"/"FALSE". One default size per product.  |

    Prerequisites
    -------------
    Products must exist in the DB before running this migration
    (see migrate_products()).
    """
    candidates = [
        _RAW_DIR / "tamaños.xlsx",
        _RAW_DIR / "sizes.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)
    if excel_path is None:
        tried = " or ".join(str(p) for p in candidates)
        print(f"❌ File not found: {tried}")
        return

    try:
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as exc:
        print(f"❌ Could not read '{excel_path.name}': {exc}")
        return

    df.columns = [str(c).strip().lower() for c in df.columns]

    _COL_ALIASES: dict[str, str] = {
        "product_name": "nombre_producto",
        "size":         "tamaño",
        "volume_oz":    "volumen_oz",
        "scale_factor": "factor_escala",
        "is_default":   "es_default",
    }
    df.rename(columns=_COL_ALIASES, inplace=True)

    expected_cols = {"nombre_producto", "tamaño", "volumen_oz", "factor_escala", "es_default"}
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Missing columns in Excel: {', '.join(sorted(missing))}")
        print("   Migration continues using None/False for absent columns.")

    total_rows = len(df)

    db = SessionLocal()
    try:
        # Pre-load name_lower → id map to avoid N+1 queries
        product_map: dict[str, int] = {
            name_lower: prod_id
            for prod_id, name_lower in db.query(
                Product.id,
                func.lower(Product.name).label("name_lower"),
            ).all()
        }
        # (product_id, size_name_lower) pairs already in DB for dedup
        existing_pairs: set[tuple[int, str]] = {
            (ps.product_id, (ps.size_name or "").lower())
            for ps in db.query(
                ProductSize.product_id,
                ProductSize.size_name,
            ).all()
        }

        objects: list[ProductSize] = []
        skipped = 0

        for idx, row in df.iterrows():
            row_num = idx + 2

            try:
                nombre = _clean_str(row.get("nombre_producto"))
                if not nombre:
                    print(f"  ⚠️  Row {row_num}: 'nombre_producto' is empty — skipped.")
                    skipped += 1
                    continue

                product_id = product_map.get(nombre.lower())
                if product_id is None:
                    print(f"  ⚠️  Row {row_num}: product '{nombre}' not found in DB — skipped.")
                    skipped += 1
                    continue

                size_name = _clean_str(row.get("tamaño"))

                pair = (product_id, (size_name or "").lower())
                if pair in existing_pairs:
                    print(
                        f"  ⚠️  Row {row_num}: size "
                        f"('{nombre}', '{size_name}') already exists — skipped."
                    )
                    skipped += 1
                    continue

                objects.append(
                    ProductSize(
                        product_id=product_id,
                        size_name=size_name,
                        volume_oz=safe_decimal(row.get("volumen_oz")) or None,
                        scale_factor=safe_decimal(row.get("factor_escala")),
                        is_default=_to_bool(row.get("es_default")),
                    )
                )
                existing_pairs.add(pair)

            except Exception as exc:
                print(f"  ⚠️  Row {row_num}: unexpected error ({exc}) — skipped.")
                skipped += 1

        if not objects:
            print(f"⚠️  No valid rows to insert ({skipped} skipped out of {total_rows}).")
            return

        db.bulk_save_objects(objects)
        db.commit()
        print(f"✅ Migrated {len(objects)} sizes ({skipped} skipped out of {total_rows} rows)")

    except Exception as exc:
        db.rollback()
        print(f"❌ Commit error: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_recipe_conversions
# ---------------------------------------------------------------------------

def migrate_recipe_conversions() -> None:
    """Loads recipe-unit conversions from 'data/raw/conversiones.xlsx'.

    Expected Excel format
    ---------------------
    One sheet with the following columns (case-insensitive; Spanish and
    English names are accepted):

    | Column                | English alias          | Type   | Description                              |
    |-----------------------|------------------------|--------|------------------------------------------|
    | nombre_ingrediente    | ingredient_name        | str    | Exact ingredient name in DB.             |
    | recipe_unit           | recipe_unit            | str    | Recipe unit name in DB.                  |
    | equivalencia_ml_o_g   | equivalent_ml_or_g     | number | Quantity in usage_unit per 1 recipe_unit.|
    | notas                 | notes                  | str    | Free comment (optional).                 |

    Prerequisites
    -------------
    Ingredients and recipe_units must exist in the DB before running this
    migration (see migrate_ingredients() and seed_data.py).

    Row validations
    ---------------
    - Skips rows without nombre_ingrediente.
    - Skips rows whose ingredient is not found in DB (warns by name).
    - Skips rows whose recipe_unit is not found in DB (warns by name).
    - Skips rows with empty or zero equivalencia.
    - Skips duplicate conversions (ingredient_id, recipe_unit_id) already existing.

    Examples of valid rows:
        "Syrup (Monin) Pump", "pump", 7, ""
        "Standard Espresso Shot", "shot", 28, "standard extraction 18 g"
    """
    # ------------------------------------------------------------------
    # 1. Locate the file
    # ------------------------------------------------------------------
    candidates = [
        _RAW_DIR / "conversiones.xlsx",
        _RAW_DIR / "conversions.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)

    if excel_path is None:
        tried = " or ".join(str(p) for p in candidates)
        print(f"❌ File not found: {tried}")
        return

    # ------------------------------------------------------------------
    # 2. Read the Excel
    # ------------------------------------------------------------------
    try:
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as exc:
        print(f"❌ Could not read '{excel_path.name}': {exc}")
        return

    df.columns = [str(c).strip().lower() for c in df.columns]

    # Aliases: accepts English or Spanish names
    _COL_ALIASES: dict[str, str] = {
        "ingredient_name":    "nombre_ingrediente",
        "equivalent_ml_or_g": "equivalencia_ml_o_g",
        "notes":              "notas",
    }
    df.rename(columns=_COL_ALIASES, inplace=True)

    expected_cols = {"nombre_ingrediente", "recipe_unit", "equivalencia_ml_o_g", "notas"}
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Missing columns in Excel: {', '.join(sorted(missing))}")
        print("   Migration continues using None for absent columns.")

    total_rows = len(df)

    # ------------------------------------------------------------------
    # 3. Open session and pre-load lookups to avoid N+1 queries
    # ------------------------------------------------------------------
    db = SessionLocal()
    try:
        # name_lower → id map for ingredients and recipe_units
        ingredient_map: dict[str, int] = {
            name_lower: ing_id
            for ing_id, name_lower in db.query(
                Ingredient.id,
                func.lower(Ingredient.name).label("name_lower"),
            ).all()
        }
        recipe_unit_map: dict[str, int] = {
            name_lower: ru_id
            for ru_id, name_lower in db.query(
                RecipeUnit.id,
                func.lower(RecipeUnit.name).label("name_lower"),
            ).all()
        }
        # Set of already-existing pairs to detect duplicates in DB
        existing_pairs: set[tuple[int, int]] = {
            (c.ingredient_id, c.recipe_unit_id)
            for c in db.query(
                IngredientRecipeUnitConversion.ingredient_id,
                IngredientRecipeUnitConversion.recipe_unit_id,
            ).all()
        }

        objects: list[IngredientRecipeUnitConversion] = []
        skipped = 0

        # ------------------------------------------------------------------
        # 4. Process row by row
        # ------------------------------------------------------------------
        for idx, row in df.iterrows():
            row_num = idx + 2

            try:
                nombre = _clean_str(row.get("nombre_ingrediente"))
                if not nombre:
                    print(f"  ⚠️  Row {row_num}: 'nombre_ingrediente' is empty — skipped.")
                    skipped += 1
                    continue

                recipe_unit_name = _clean_str(row.get("recipe_unit"))
                if not recipe_unit_name:
                    print(f"  ⚠️  Row {row_num} ({nombre!r}): 'recipe_unit' is empty — skipped.")
                    skipped += 1
                    continue

                ingredient_id = ingredient_map.get(nombre.lower())
                if ingredient_id is None:
                    print(f"  ⚠️  Row {row_num}: ingredient '{nombre}' not found in DB — skipped.")
                    skipped += 1
                    continue

                recipe_unit_id = recipe_unit_map.get(recipe_unit_name.lower())
                if recipe_unit_id is None:
                    print(f"  ⚠️  Row {row_num}: recipe_unit '{recipe_unit_name}' not found in DB — skipped.")
                    skipped += 1
                    continue

                pair = (ingredient_id, recipe_unit_id)
                if pair in existing_pairs:
                    print(
                        f"  ⚠️  Row {row_num}: conversion "
                        f"('{nombre}', '{recipe_unit_name}') already exists — skipped."
                    )
                    skipped += 1
                    continue

                equivalencia = safe_decimal(row.get("equivalencia_ml_o_g"))
                if not equivalencia:
                    print(
                        f"  ⚠️  Row {row_num} ({nombre!r}): "
                        f"invalid or zero equivalencia — skipped."
                    )
                    skipped += 1
                    continue

                objects.append(
                    IngredientRecipeUnitConversion(
                        ingredient_id=ingredient_id,
                        recipe_unit_id=recipe_unit_id,
                        usage_unit_quantity=equivalencia,
                        notes=_clean_str(row.get("notas")),
                    )
                )
                # Mark the pair as seen to avoid duplicates within the batch
                existing_pairs.add(pair)

            except Exception as exc:
                print(f"  ⚠️  Row {row_num}: unexpected error ({exc}) — skipped.")
                skipped += 1

        # ------------------------------------------------------------------
        # 5. Bulk insert + commit
        # ------------------------------------------------------------------
        if not objects:
            print(f"⚠️  No valid rows to insert ({skipped} skipped out of {total_rows}).")
            return

        db.bulk_save_objects(objects)
        db.commit()
        print(f"✅ Migrated {len(objects)} conversions ({skipped} skipped out of {total_rows} rows)")

    except Exception as exc:
        db.rollback()
        print(f"❌ Commit error: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_recipes
# ---------------------------------------------------------------------------

def migrate_recipes() -> None:
    """Loads recipe lines from 'data/raw/recetas.xlsx' (or recipes.xlsx).

    Expected Excel format
    ---------------------
    | Column             | English alias     | Type   | Description                                     |
    |--------------------|-------------------|--------|-------------------------------------------------|
    | nombre_producto    | product_name      | str    | Exact product name in DB. Required.             |
    | nombre_ingrediente | ingredient_name   | str    | Exact ingredient name in DB. Required.          |
    | cantidad           | quantity          | number | Plain number, e.g. 2, 240, 1.5. Required > 0.   |
    | recipe_unit        | recipe_unit       | str    | Unit name (e.g. "shot", "pump"). Optional.      |
    | escala_con_tamaño  | scales_with_size  | bool   | "TRUE"/"FALSE". Default True.                   |
    | yield_proceso_%    | process_yield_%   | number | % process loss. Default 0.                      |

    Quantity + unit
    ---------------
    quantity is a plain number and the unit lives in its own ``recipe_unit``
    column (no string parsing). If recipe_unit is empty, quantity is interpreted
    directly in the ingredient's usage_unit. If recipe_unit is set but not found
    in DB, a warning is issued and the line is inserted with recipe_unit_id=None.

    Prerequisites
    -------------
    Products and ingredients must exist in DB (migrate_products(),
    migrate_ingredients()).
    """
    candidates = [
        _RAW_DIR / "recetas.xlsx",
        _RAW_DIR / "recipes.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)
    if excel_path is None:
        tried = " or ".join(str(p) for p in candidates)
        print(f"❌ File not found: {tried}")
        return

    try:
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as exc:
        print(f"❌ Could not read '{excel_path.name}': {exc}")
        return

    df.columns = [str(c).strip().lower() for c in df.columns]

    _COL_ALIASES: dict[str, str] = {
        "product_name":    "nombre_producto",
        "ingredient_name": "nombre_ingrediente",
        "quantity":        "cantidad",
        "scales_with_size": "escala_con_tamaño",
        "process_yield_%": "yield_proceso_%",
    }
    df.rename(columns=_COL_ALIASES, inplace=True)

    expected_cols = {
        "nombre_producto", "nombre_ingrediente",
        "cantidad", "recipe_unit", "escala_con_tamaño", "yield_proceso_%",
    }
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Missing columns in Excel: {', '.join(sorted(missing))}")
        print("   Migration continues using default values for absent columns.")

    total_rows = len(df)

    db = SessionLocal()
    try:
        # Pre-load lookups to avoid N+1 queries
        product_map: dict[str, int] = {
            name_lower: prod_id
            for prod_id, name_lower in db.query(
                Product.id,
                func.lower(Product.name).label("name_lower"),
            ).all()
        }
        ingredient_map: dict[str, int] = {
            name_lower: ing_id
            for ing_id, name_lower in db.query(
                Ingredient.id,
                func.lower(Ingredient.name).label("name_lower"),
            ).all()
        }
        recipe_unit_map: dict[str, int] = {
            name_lower: ru_id
            for ru_id, name_lower in db.query(
                RecipeUnit.id,
                func.lower(RecipeUnit.name).label("name_lower"),
            ).all()
        }
        # Existing pairs: {(product_id, ingredient_id): (ri.id, recipe_unit_id)}
        # We store the id so we can UPDATE if the recipe_unit_id was NULL.
        existing_records: dict[tuple[int, int], tuple[int, int | None]] = {
            (ri.product_id, ri.ingredient_id): (ri.id, ri.recipe_unit_id)
            for ri in db.query(
                RecipeIngredient.id,
                RecipeIngredient.product_id,
                RecipeIngredient.ingredient_id,
                RecipeIngredient.recipe_unit_id,
            ).all()
        }

        objects: list[RecipeIngredient] = []
        upgrades: list[tuple[int, int]] = []   # (ri.id, new recipe_unit_id)
        skipped = 0

        for idx, row in df.iterrows():
            row_num = idx + 2

            try:
                nombre_producto = _clean_str(row.get("nombre_producto"))
                nombre_ingrediente = _clean_str(row.get("nombre_ingrediente"))

                if not nombre_producto:
                    print(f"  ⚠️  Row {row_num}: 'nombre_producto' is empty — skipped.")
                    skipped += 1
                    continue
                if not nombre_ingrediente:
                    print(f"  ⚠️  Row {row_num}: 'nombre_ingrediente' is empty — skipped.")
                    skipped += 1
                    continue

                product_id = product_map.get(nombre_producto.lower())
                if product_id is None:
                    print(f"  ⚠️  Row {row_num}: product '{nombre_producto}' not found in DB — skipped.")
                    skipped += 1
                    continue

                ingredient_id = ingredient_map.get(nombre_ingrediente.lower())
                if ingredient_id is None:
                    print(f"  ⚠️  Row {row_num}: ingredient '{nombre_ingrediente}' not found in DB — skipped.")
                    skipped += 1
                    continue

                # Quantity is a plain number; the unit lives in its own column
                # (recipe_unit), so there is no fragile string parsing anymore.
                quantity = safe_decimal(_clean_str(row.get("cantidad")))
                if quantity is None or quantity <= 0:
                    print(
                        f"  ⚠️  Row {row_num} ('{nombre_producto}' / '{nombre_ingrediente}'): "
                        f"quantity '{row.get('cantidad')}' inválida — skipped."
                    )
                    skipped += 1
                    continue

                # recipe_unit optional: empty => quantity is in the ingredient's usage_unit
                recipe_unit_id: int | None = None
                unit_name = _clean_str(row.get("recipe_unit"))
                if unit_name is not None:
                    recipe_unit_id = recipe_unit_map.get(unit_name.lower())
                    if recipe_unit_id is None:
                        print(
                            f"  ⚠️  Row {row_num}: recipe_unit '{unit_name}' not found in DB "
                            f"— will be inserted with recipe_unit_id=None."
                        )

                pair = (product_id, ingredient_id)
                if pair in existing_records:
                    existing_id, existing_ru_id = existing_records[pair]
                    if existing_ru_id is None and recipe_unit_id is not None:
                        # Row already exists but with recipe_unit_id=NULL; update it.
                        upgrades.append((existing_id, recipe_unit_id))
                        existing_records[pair] = (existing_id, recipe_unit_id)
                    else:
                        skipped += 1
                    continue

                objects.append(
                    RecipeIngredient(
                        product_id=product_id,
                        ingredient_id=ingredient_id,
                        quantity=quantity,
                        recipe_unit_id=recipe_unit_id,
                        scales_with_size=_to_bool(row.get("escala_con_tamaño"), default=True),
                        process_yield_loss=safe_decimal(row.get("yield_proceso_%")),
                    )
                )
                existing_records[pair] = (None, recipe_unit_id)

            except Exception as exc:
                print(f"  ⚠️  Row {row_num}: unexpected error ({exc}) — skipped.")
                skipped += 1

        if not objects and not upgrades:
            print(f"⚠️  No valid rows to insert ({skipped} skipped out of {total_rows}).")
            return

        for ri_id, ru_id in upgrades:
            db.query(RecipeIngredient).filter(RecipeIngredient.id == ri_id).update(
                {"recipe_unit_id": ru_id}, synchronize_session=False
            )

        if objects:
            db.bulk_save_objects(objects)

        db.commit()
        parts = [f"✅ Migrated {len(objects)} recipe ingredients"]
        if upgrades:
            parts.append(f"updated {len(upgrades)} recipe_unit_id")
        if skipped:
            parts.append(f"{skipped} skipped out of {total_rows} rows")
        print(", ".join(parts))

    except Exception as exc:
        db.rollback()
        print(f"❌ Commit error: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_recipe_sub_recipes
# ---------------------------------------------------------------------------

def migrate_recipe_sub_recipes() -> None:
    """Loads sub-recipe references from 'data/templates/recipe_sub_recipes.csv'.

    Expected CSV format
    -------------------
    | Column              | Type   | Description                                          |
    |---------------------|--------|------------------------------------------------------|
    | parent_product_name | str    | Exact product name in DB (parent recipe). Required.  |
    | sub_recipe_name     | str    | Exact name of a product flagged is_sub_recipe=True.  |
    | quantity            | number | Quantity of sub-recipe used. Required > 0.           |
    | recipe_unit         | str    | Unit label (informational; not stored in DB).        |
    | scales_with_size    | bool   | "true"/"1"/"yes" → True, else False.                 |

    Both parent and sub-recipe are rows in the ``products`` table — sub-recipes
    are products with ``is_sub_recipe=True``. The name lookup is case-insensitive
    and shared between parent and sub resolution.

    Prerequisites
    -------------
    Products must exist in DB (migrate_products()).
    """
    csv_path = _TEMPLATES_DIR / "recipe_sub_recipes.csv"
    if not csv_path.exists():
        print(f"❌ File not found: {csv_path}")
        return

    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except Exception as exc:
        print(f"❌ Could not read '{csv_path.name}': {exc}")
        return

    total_rows = len(rows)

    db = SessionLocal()
    try:
        # Pre-load product lookup (case-insensitive) -> (id, is_sub_recipe).
        # Sub-recipes are also products, so a single map serves both lookups.
        product_map: dict[str, tuple[int, bool]] = {
            name_lower: (prod_id, bool(is_sub))
            for prod_id, name_lower, is_sub in db.query(
                Product.id,
                func.lower(Product.name).label("name_lower"),
                Product.is_sub_recipe,
            ).all()
        }
        # Existing (parent_product_id, sub_recipe_id) pairs to avoid duplicates.
        existing_pairs: set[tuple[int, int]] = {
            (rsr.parent_product_id, rsr.sub_recipe_id)
            for rsr in db.query(
                RecipeSubRecipe.parent_product_id,
                RecipeSubRecipe.sub_recipe_id,
            ).all()
        }

        objects: list[RecipeSubRecipe] = []
        skipped = 0

        for idx, row in enumerate(rows):
            row_num = idx + 2  # +1 header, +1 1-based

            try:
                parent_name = _clean_str(row.get("parent_product_name"))
                sub_name = _clean_str(row.get("sub_recipe_name"))

                if not parent_name:
                    print(f"  ⚠️  Row {row_num}: 'parent_product_name' is empty — skipped.")
                    skipped += 1
                    continue
                if not sub_name:
                    print(f"  ⚠️  Row {row_num}: 'sub_recipe_name' is empty — skipped.")
                    skipped += 1
                    continue

                parent_entry = product_map.get(parent_name.lower())
                if parent_entry is None:
                    print(f"  ⚠️  Row {row_num}: parent product '{parent_name}' not found in DB — skipped.")
                    skipped += 1
                    continue
                parent_product_id = parent_entry[0]

                sub_entry = product_map.get(sub_name.lower())
                if sub_entry is None:
                    print(f"  ⚠️  Row {row_num}: sub-recipe '{sub_name}' not found in DB — skipped.")
                    skipped += 1
                    continue
                sub_recipe_id, sub_is_sub = sub_entry
                if not sub_is_sub:
                    print(
                        f"  ⚠️  Row {row_num}: '{sub_name}' exists but is_sub_recipe=False "
                        f"— loaded anyway."
                    )

                quantity = safe_decimal(_clean_str(row.get("quantity")))
                if quantity is None or quantity <= 0:
                    print(
                        f"  ⚠️  Row {row_num} ('{parent_name}' / '{sub_name}'): "
                        f"quantity '{row.get('quantity')}' inválida — skipped."
                    )
                    skipped += 1
                    continue

                pair = (parent_product_id, sub_recipe_id)
                if pair in existing_pairs:
                    print(
                        f"  ⚠️  Row {row_num}: pair ('{parent_name}' / '{sub_name}') "
                        f"already exists in DB — skipped."
                    )
                    skipped += 1
                    continue

                objects.append(
                    RecipeSubRecipe(
                        parent_product_id=parent_product_id,
                        sub_recipe_id=sub_recipe_id,
                        quantity=quantity,
                        scales_with_size=_to_bool(row.get("scales_with_size")),
                    )
                )
                existing_pairs.add(pair)

            except Exception as exc:
                print(f"  ⚠️  Row {row_num}: unexpected error ({exc}) — skipped.")
                skipped += 1

        if not objects:
            print(f"⚠️  No valid rows to insert ({skipped} skipped out of {total_rows}).")
            return

        db.bulk_save_objects(objects)
        db.commit()
        parts = [f"✅ Migrated {len(objects)} recipe sub-recipes"]
        if skipped:
            parts.append(f"{skipped} skipped out of {total_rows} rows")
        print(", ".join(parts))

    except Exception as exc:
        db.rollback()
        print(f"❌ Commit error: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("Starting migration from Excel...")
    try:
        migrate_ingredients()
        migrate_recipe_conversions()
        migrate_products()
        migrate_product_sizes()
        migrate_recipes()
        migrate_recipe_sub_recipes()
    except Exception as exc:
        print(f"❌ Fatal error during migration: {exc}", file=sys.stderr)
        sys.exit(1)
    print("✅ Migration complete")


if __name__ == "__main__":
    main()
