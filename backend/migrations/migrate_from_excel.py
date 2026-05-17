"""Migración masiva de datos desde archivos Excel al sistema CPQ.

Ejecutar con:
    python -m backend.migrations.migrate_from_excel

Cada función lee un archivo Excel de 'data/raw/' y hace bulk insert en la base
de datos usando la sesión de SQLAlchemy.  Los errores por fila se reportan como
warnings para que una fila mal formateada no interrumpa la carga completa.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import func

import backend.models  # noqa: F401 — registra todos los modelos en Base.metadata
from backend.database import SessionLocal
from backend.migrations.utils import parse_quantity_with_unit, safe_decimal
from backend.models.ingredient import Ingredient
from backend.models.product import Product, ProductSize, RecipeIngredient
from backend.models.recipe_unit import IngredientRecipeUnitConversion, RecipeUnit

# Raíz del proyecto (dos niveles arriba de este archivo)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RAW_DIR = _PROJECT_ROOT / "data" / "raw"

# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

_NAN_LIKE = {None, "", "nan", "NaT", "NaN", "none", "null"}


def _is_empty(value: object) -> bool:
    """Retorna True si el valor es NaN, None o string vacío."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN check
        return True
    return str(value).strip().lower() in _NAN_LIKE


def _clean_str(value: object) -> str | None:
    """Convierte a str limpio, o None si el valor está vacío."""
    if _is_empty(value):
        return None
    return str(value).strip() or None


def _to_bool(value: object, default: bool = False) -> bool:
    """Convierte strings "TRUE"/"FALSE" (y variantes) a bool.

    Ejemplos:
        >>> _to_bool("TRUE")  → True
        >>> _to_bool("False") → False
        >>> _to_bool("1")     → True
        >>> _to_bool(None)    → False  (default)
    """
    if _is_empty(value):
        return default
    return str(value).strip().lower() in {"true", "1", "yes", "sí", "si"}


# ---------------------------------------------------------------------------
# migrate_ingredients
# ---------------------------------------------------------------------------

def migrate_ingredients() -> None:
    """Carga ingredientes desde 'data/raw/ingredientes.xlsx' (o ingredients.xlsx).

    Formato esperado del Excel
    --------------------------
    El archivo debe tener una hoja con al menos estas columnas
    (el orden no importa; los nombres son case-insensitive después del strip):

    | Columna           | Tipo    | Descripción                                    |
    |-------------------|---------|------------------------------------------------|
    | nombre            | str     | Nombre del ingrediente. **Obligatorio.**        |
    | categoria         | str     | Categoría (ej: "lácteos", "jarabes").           |
    | unidad_compra     | str     | Unidad en la que se compra (ej: "caja 1L").     |
    | precio_compra     | number  | Precio por unidad de compra (COP o moneda local)|
    | unidad_uso        | str     | Unidad usada en recetas (ej: "ml", "g").        |
    | factor_conversion | number  | Unidades de uso por unidad de compra.           |
    | yield_%           | number  | Porcentaje de aprovechamiento (0-100). Default 100.|
    | url_proveedor     | str     | URL del proveedor para scraping (opcional).     |

    Filas sin 'nombre' se omiten con un warning.
    Filas con errores de conversión se omiten con un warning pero no detienen
    el proceso completo.

    Ejemplos de valores válidos:
        nombre="Leche entera", categoria="lácteos", precio_compra="4500",
        unidad_compra="litro", unidad_uso="ml", factor_conversion="1000", yield_%=98
    """
    # ------------------------------------------------------------------
    # 1. Localizar el archivo (acepta dos nombres de archivo)
    # ------------------------------------------------------------------
    candidates = [
        _RAW_DIR / "ingredientes.xlsx",
        _RAW_DIR / "ingredients.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)

    if excel_path is None:
        tried = " o ".join(str(p) for p in candidates)
        print(f"❌ Archivo no encontrado: {tried}")
        return

    # ------------------------------------------------------------------
    # 2. Leer el Excel
    # ------------------------------------------------------------------
    try:
        df = pd.read_excel(excel_path, dtype=str)  # dtype=str evita conversiones automáticas
    except Exception as exc:
        print(f"❌ No se pudo leer '{excel_path.name}': {exc}")
        return

    # Normalizar nombres de columnas: strip + lowercase
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Alias: acepta tanto nombres en español como en inglés
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
        "unidad_uso", "factor_conversion", "yield_%", "url_proveedor",
    }
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Columnas faltantes en el Excel: {', '.join(sorted(missing))}")
        print("   La migración continúa usando None para las columnas ausentes.")

    total_rows = len(df)
    objects: list[Ingredient] = []
    skipped = 0

    # ------------------------------------------------------------------
    # 3. Procesar fila a fila
    # ------------------------------------------------------------------
    for idx, row in df.iterrows():
        row_num = idx + 2  # +2: encabezado en fila 1, datos desde fila 2

        try:
            nombre = _clean_str(row.get("nombre"))
            if not nombre:
                print(f"  ⚠️  Fila {row_num}: 'nombre' vacío — omitida.")
                skipped += 1
                continue

            raw_yield = row.get("yield_%")
            yield_pct: Decimal = (
                Decimal("100")
                if _is_empty(raw_yield)
                else safe_decimal(raw_yield)
            )
            # Guardia: yield fuera de rango razonable
            if not (Decimal("0") < yield_pct <= Decimal("100")):
                print(
                    f"  ⚠️  Fila {row_num} ({nombre!r}): "
                    f"yield_% inválido ({yield_pct}), usando 100."
                )
                yield_pct = Decimal("100")

            ingredient = Ingredient(
                name=nombre,
                category=_clean_str(row.get("categoria")),
                purchase_unit=_clean_str(row.get("unidad_compra")),
                purchase_price=safe_decimal(row.get("precio_compra")) or None,
                usage_unit=_clean_str(row.get("unidad_uso")),
                conversion_factor=safe_decimal(row.get("factor_conversion")) or None,
                yield_percentage=yield_pct,
                source_url=_clean_str(row.get("url_proveedor")),
            )
            objects.append(ingredient)

        except Exception as exc:
            print(f"  ⚠️  Fila {row_num}: error inesperado ({exc}) — omitida.")
            skipped += 1

    # ------------------------------------------------------------------
    # 4. Bulk insert + commit
    # ------------------------------------------------------------------
    if not objects:
        print(f"⚠️  No hay filas válidas para insertar ({skipped} omitidas de {total_rows}).")
        return

    db = SessionLocal()
    try:
        db.bulk_save_objects(objects)
        db.commit()
        inserted = len(objects)
        print(f"✅ Migrated {inserted} ingredients ({skipped} omitidas de {total_rows} filas)")
    except Exception as exc:
        db.rollback()
        print(f"❌ Error al hacer commit: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_products
# ---------------------------------------------------------------------------

def migrate_products() -> None:
    """Carga productos desde 'data/raw/productos.xlsx' (o products.xlsx).

    Formato esperado del Excel
    --------------------------
    | Columna          | Alias inglés        | Tipo    | Descripción                              |
    |------------------|---------------------|---------|------------------------------------------|
    | nombre           | name                | str     | Nombre del producto. **Obligatorio.**    |
    | categoria        | category            | str     | Categoría (ej: "Hot Classics").          |
    | tamaño_base_oz   | base_size_oz        | number  | Tamaño base en onzas para escalar receta.|
    | tiempo_prep_min  | prep_time_min       | number  | Minutos de preparación.                  |
    | costo_labor_min  | labor_cost_per_min  | number  | Costo por minuto de labor.               |
    | es_sub_receta    | is_sub_recipe       | bool    | "TRUE"/"FALSE". Default False.           |

    Filas sin 'nombre' se omiten con warning.
    """
    candidates = [
        _RAW_DIR / "productos.xlsx",
        _RAW_DIR / "products.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)
    if excel_path is None:
        tried = " o ".join(str(p) for p in candidates)
        print(f"❌ Archivo no encontrado: {tried}")
        return

    try:
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as exc:
        print(f"❌ No se pudo leer '{excel_path.name}': {exc}")
        return

    df.columns = [str(c).strip().lower() for c in df.columns]

    _COL_ALIASES: dict[str, str] = {
        "name":               "nombre",
        "category":           "categoria",
        "base_size_oz":       "tamaño_base_oz",
        "prep_time_min":      "tiempo_prep_min",
        "labor_cost_per_min": "costo_labor_min",
        "is_sub_recipe":      "es_sub_receta",
    }
    df.rename(columns=_COL_ALIASES, inplace=True)

    expected_cols = {"nombre", "categoria", "tamaño_base_oz", "tiempo_prep_min", "costo_labor_min", "es_sub_receta"}
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Columnas faltantes en el Excel: {', '.join(sorted(missing))}")
        print("   La migración continúa usando None/False para las columnas ausentes.")

    total_rows = len(df)
    objects: list[Product] = []
    skipped = 0

    for idx, row in df.iterrows():
        row_num = idx + 2

        try:
            nombre = _clean_str(row.get("nombre"))
            if not nombre:
                print(f"  ⚠️  Fila {row_num}: 'nombre' vacío — omitida.")
                skipped += 1
                continue

            objects.append(
                Product(
                    name=nombre,
                    category=_clean_str(row.get("categoria")),
                    base_size_oz=safe_decimal(row.get("tamaño_base_oz")) or None,
                    prep_time_minutes=safe_decimal(row.get("tiempo_prep_min")) or None,
                    labor_cost_per_minute=safe_decimal(row.get("costo_labor_min")),
                    is_sub_recipe=_to_bool(row.get("es_sub_receta")),
                )
            )

        except Exception as exc:
            print(f"  ⚠️  Fila {row_num}: error inesperado ({exc}) — omitida.")
            skipped += 1

    if not objects:
        print(f"⚠️  No hay filas válidas para insertar ({skipped} omitidas de {total_rows}).")
        return

    db = SessionLocal()
    try:
        db.bulk_save_objects(objects)
        db.commit()
        print(f"✅ Migrated {len(objects)} products ({skipped} omitidas de {total_rows} filas)")
    except Exception as exc:
        db.rollback()
        print(f"❌ Error al hacer commit: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_product_sizes
# ---------------------------------------------------------------------------

def migrate_product_sizes() -> None:
    """Carga variantes de tamaño desde 'data/raw/tamaños.xlsx' (o sizes.xlsx).

    Formato esperado del Excel
    --------------------------
    | Columna         | Alias inglés  | Tipo   | Descripción                                    |
    |-----------------|---------------|--------|------------------------------------------------|
    | nombre_producto | product_name  | str    | Nombre exacto del producto en BD. Obligatorio. |
    | tamaño          | size          | str    | Nombre del tamaño (ej: "Small", "Grande").     |
    | volumen_oz      | volume_oz     | number | Volumen en onzas para este tamaño.             |
    | factor_escala   | scale_factor  | number | Multiplicador vs. tamaño base (base = 1.0).    |
    | es_default      | is_default    | bool   | "TRUE"/"FALSE". Un tamaño por defecto.         |

    Dependencias previas
    --------------------
    Los productos deben existir en la BD antes de ejecutar esta migración
    (ver migrate_products()).
    """
    candidates = [
        _RAW_DIR / "tamaños.xlsx",
        _RAW_DIR / "sizes.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)
    if excel_path is None:
        tried = " o ".join(str(p) for p in candidates)
        print(f"❌ Archivo no encontrado: {tried}")
        return

    try:
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as exc:
        print(f"❌ No se pudo leer '{excel_path.name}': {exc}")
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
        print(f"⚠️  Columnas faltantes en el Excel: {', '.join(sorted(missing))}")
        print("   La migración continúa usando None/False para las columnas ausentes.")

    total_rows = len(df)

    db = SessionLocal()
    try:
        # Pre-cargar mapa nombre_lower → id para evitar N+1 queries
        product_map: dict[str, int] = {
            name_lower: prod_id
            for prod_id, name_lower in db.query(
                Product.id,
                func.lower(Product.name).label("name_lower"),
            ).all()
        }
        # Pares (product_id, size_name_lower) ya existentes para dedup
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
                    print(f"  ⚠️  Fila {row_num}: 'nombre_producto' vacío — omitida.")
                    skipped += 1
                    continue

                product_id = product_map.get(nombre.lower())
                if product_id is None:
                    print(f"  ⚠️  Fila {row_num}: producto '{nombre}' no encontrado en BD — omitida.")
                    skipped += 1
                    continue

                size_name = _clean_str(row.get("tamaño"))

                pair = (product_id, (size_name or "").lower())
                if pair in existing_pairs:
                    print(
                        f"  ⚠️  Fila {row_num}: tamaño "
                        f"('{nombre}', '{size_name}') ya existe — omitida."
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
                print(f"  ⚠️  Fila {row_num}: error inesperado ({exc}) — omitida.")
                skipped += 1

        if not objects:
            print(f"⚠️  No hay filas válidas para insertar ({skipped} omitidas de {total_rows}).")
            return

        db.bulk_save_objects(objects)
        db.commit()
        print(f"✅ Migrated {len(objects)} sizes ({skipped} omitidas de {total_rows} filas)")

    except Exception as exc:
        db.rollback()
        print(f"❌ Error al hacer commit: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_recipe_conversions
# ---------------------------------------------------------------------------

def migrate_recipe_conversions() -> None:
    """Carga conversiones receta-unidad desde 'data/raw/conversiones.xlsx'.

    Formato esperado del Excel
    --------------------------
    Una hoja con las siguientes columnas (case-insensitive; se aceptan nombres
    en español e inglés):

    | Columna               | Alias inglés          | Tipo   | Descripción                              |
    |-----------------------|-----------------------|--------|------------------------------------------|
    | nombre_ingrediente    | ingredient_name       | str    | Nombre exacto del ingrediente en la BD.  |
    | recipe_unit           | recipe_unit           | str    | Nombre de la recipe_unit en la BD.       |
    | equivalencia_ml_o_g   | equivalent_ml_or_g    | number | Cantidad en usage_unit por 1 recipe_unit.|
    | notas                 | notes                 | str    | Comentario libre (opcional).             |

    Dependencias previas
    --------------------
    Los ingredientes y las recipe_units deben existir en la BD antes de
    ejecutar esta migración (ver migrate_ingredients() y seed_data.py).

    Validaciones por fila
    ---------------------
    - Omite filas sin nombre_ingrediente.
    - Omite filas cuyo ingrediente no se encuentre en BD (avisa por nombre).
    - Omite filas cuya recipe_unit no se encuentre en BD (avisa por nombre).
    - Omite filas con equivalencia vacía o cero.
    - Omite conversiones duplicadas (ingredient_id, recipe_unit_id) ya existentes.

    Ejemplos de filas válidas:
        "Syrup (Monin) Pump", "pump", 7, ""
        "Standard Espresso Shot", "shot", 28, "extracción estándar 18 g"
    """
    # ------------------------------------------------------------------
    # 1. Localizar el archivo
    # ------------------------------------------------------------------
    candidates = [
        _RAW_DIR / "conversiones.xlsx",
        _RAW_DIR / "conversions.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)

    if excel_path is None:
        tried = " o ".join(str(p) for p in candidates)
        print(f"❌ Archivo no encontrado: {tried}")
        return

    # ------------------------------------------------------------------
    # 2. Leer el Excel
    # ------------------------------------------------------------------
    try:
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as exc:
        print(f"❌ No se pudo leer '{excel_path.name}': {exc}")
        return

    df.columns = [str(c).strip().lower() for c in df.columns]

    # Alias: acepta nombres en inglés o español
    _COL_ALIASES: dict[str, str] = {
        "ingredient_name":    "nombre_ingrediente",
        "equivalent_ml_or_g": "equivalencia_ml_o_g",
        "notes":              "notas",
    }
    df.rename(columns=_COL_ALIASES, inplace=True)

    expected_cols = {"nombre_ingrediente", "recipe_unit", "equivalencia_ml_o_g", "notas"}
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Columnas faltantes en el Excel: {', '.join(sorted(missing))}")
        print("   La migración continúa usando None para las columnas ausentes.")

    total_rows = len(df)

    # ------------------------------------------------------------------
    # 3. Abrir sesión y pre-cargar lookups para evitar N+1 queries
    # ------------------------------------------------------------------
    db = SessionLocal()
    try:
        # Mapa nombre_lower → id para ingredientes y recipe_units
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
        # Conjunto de pares ya existentes para detectar duplicados en BD
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
        # 4. Procesar fila a fila
        # ------------------------------------------------------------------
        for idx, row in df.iterrows():
            row_num = idx + 2

            try:
                nombre = _clean_str(row.get("nombre_ingrediente"))
                if not nombre:
                    print(f"  ⚠️  Fila {row_num}: 'nombre_ingrediente' vacío — omitida.")
                    skipped += 1
                    continue

                recipe_unit_name = _clean_str(row.get("recipe_unit"))
                if not recipe_unit_name:
                    print(f"  ⚠️  Fila {row_num} ({nombre!r}): 'recipe_unit' vacío — omitida.")
                    skipped += 1
                    continue

                ingredient_id = ingredient_map.get(nombre.lower())
                if ingredient_id is None:
                    print(f"  ⚠️  Fila {row_num}: ingrediente '{nombre}' no encontrado en BD — omitida.")
                    skipped += 1
                    continue

                recipe_unit_id = recipe_unit_map.get(recipe_unit_name.lower())
                if recipe_unit_id is None:
                    print(f"  ⚠️  Fila {row_num}: recipe_unit '{recipe_unit_name}' no encontrada en BD — omitida.")
                    skipped += 1
                    continue

                pair = (ingredient_id, recipe_unit_id)
                if pair in existing_pairs:
                    print(
                        f"  ⚠️  Fila {row_num}: conversión "
                        f"('{nombre}', '{recipe_unit_name}') ya existe — omitida."
                    )
                    skipped += 1
                    continue

                equivalencia = safe_decimal(row.get("equivalencia_ml_o_g"))
                if not equivalencia:
                    print(
                        f"  ⚠️  Fila {row_num} ({nombre!r}): "
                        f"equivalencia inválida o cero — omitida."
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
                # Marcar el par como visto para evitar duplicados dentro del batch
                existing_pairs.add(pair)

            except Exception as exc:
                print(f"  ⚠️  Fila {row_num}: error inesperado ({exc}) — omitida.")
                skipped += 1

        # ------------------------------------------------------------------
        # 5. Bulk insert + commit
        # ------------------------------------------------------------------
        if not objects:
            print(f"⚠️  No hay filas válidas para insertar ({skipped} omitidas de {total_rows}).")
            return

        db.bulk_save_objects(objects)
        db.commit()
        print(f"✅ Migrated {len(objects)} conversions ({skipped} omitidas de {total_rows} filas)")

    except Exception as exc:
        db.rollback()
        print(f"❌ Error al hacer commit: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# migrate_recipes
# ---------------------------------------------------------------------------

def migrate_recipes() -> None:
    """Carga líneas de receta desde 'data/raw/recetas.xlsx' (o recipes.xlsx).

    Formato esperado del Excel
    --------------------------
    | Columna           | Alias inglés      | Tipo   | Descripción                                     |
    |-------------------|-------------------|--------|-------------------------------------------------|
    | nombre_producto   | product_name      | str    | Nombre exacto del producto en BD. Obligatorio.  |
    | nombre_ingrediente| ingredient_name   | str    | Nombre exacto del ingrediente en BD. Obligatorio|
    | cantidad          | quantity          | str    | Ej: "2 Standard Shot", "12 Oz", "4 Pump".       |
    | escala_con_tamaño | scales_with_size  | bool   | "TRUE"/"FALSE". Default True.                   |
    | yield_proceso_%   | process_yield_%   | number | % de merma en preparación. Default 0.           |

    Parseo de cantidad
    ------------------
    Se usa parse_quantity_with_unit() que extrae número y ÚLTIMA palabra como
    unidad. "2 Standard Shot" → (2.0, "shot"). Si la cantidad no puede
    parsearse, la fila se omite con warning.

    La recipe_unit se busca por nombre (case-insensitive). Si no existe en BD
    se avisa pero la línea se inserta igual con recipe_unit_id=None
    (cantidad interpretada directamente en la usage_unit del ingrediente).

    Dependencias previas
    --------------------
    Productos e ingredientes deben existir en BD (migrate_products(),
    migrate_ingredients()).
    """
    candidates = [
        _RAW_DIR / "recetas.xlsx",
        _RAW_DIR / "recipes.xlsx",
    ]
    excel_path: Path | None = next((p for p in candidates if p.exists()), None)
    if excel_path is None:
        tried = " o ".join(str(p) for p in candidates)
        print(f"❌ Archivo no encontrado: {tried}")
        return

    try:
        df = pd.read_excel(excel_path, dtype=str)
    except Exception as exc:
        print(f"❌ No se pudo leer '{excel_path.name}': {exc}")
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
        "cantidad", "escala_con_tamaño", "yield_proceso_%",
    }
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"⚠️  Columnas faltantes en el Excel: {', '.join(sorted(missing))}")
        print("   La migración continúa usando valores por defecto para las columnas ausentes.")

    total_rows = len(df)

    db = SessionLocal()
    try:
        # Pre-cargar lookups para evitar N+1 queries
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
        # Pares existentes: {(product_id, ingredient_id): (ri.id, recipe_unit_id)}
        # Guardamos el id para poder hacer UPDATE si el recipe_unit_id era NULL.
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
        upgrades: list[tuple[int, int]] = []   # (ri.id, nuevo recipe_unit_id)
        skipped = 0

        for idx, row in df.iterrows():
            row_num = idx + 2

            try:
                nombre_producto = _clean_str(row.get("nombre_producto"))
                nombre_ingrediente = _clean_str(row.get("nombre_ingrediente"))

                if not nombre_producto:
                    print(f"  ⚠️  Fila {row_num}: 'nombre_producto' vacío — omitida.")
                    skipped += 1
                    continue
                if not nombre_ingrediente:
                    print(f"  ⚠️  Fila {row_num}: 'nombre_ingrediente' vacío — omitida.")
                    skipped += 1
                    continue

                product_id = product_map.get(nombre_producto.lower())
                if product_id is None:
                    print(f"  ⚠️  Fila {row_num}: producto '{nombre_producto}' no encontrado en BD — omitida.")
                    skipped += 1
                    continue

                ingredient_id = ingredient_map.get(nombre_ingrediente.lower())
                if ingredient_id is None:
                    print(f"  ⚠️  Fila {row_num}: ingrediente '{nombre_ingrediente}' no encontrado en BD — omitida.")
                    skipped += 1
                    continue

                # Parsear cantidad (número + unidad opcional)
                raw_qty = _clean_str(row.get("cantidad")) or ""
                quantity, unit_name = parse_quantity_with_unit(raw_qty)

                if quantity is None:
                    print(
                        f"  ⚠️  Fila {row_num} ('{nombre_producto}' / '{nombre_ingrediente}'): "
                        f"cantidad '{raw_qty}' no se pudo parsear — omitida."
                    )
                    skipped += 1
                    continue

                # Buscar recipe_unit solo si hay unidad en el string
                recipe_unit_id: int | None = None
                if unit_name is not None:
                    recipe_unit_id = recipe_unit_map.get(unit_name.lower())
                    if recipe_unit_id is None:
                        print(
                            f"  ⚠️  Fila {row_num}: recipe_unit '{unit_name}' no encontrada en BD "
                            f"— se insertará con recipe_unit_id=None."
                        )

                pair = (product_id, ingredient_id)
                if pair in existing_records:
                    existing_id, existing_ru_id = existing_records[pair]
                    if existing_ru_id is None and recipe_unit_id is not None:
                        # Fila ya existe pero con recipe_unit_id=NULL; actualizar.
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
                print(f"  ⚠️  Fila {row_num}: error inesperado ({exc}) — omitida.")
                skipped += 1

        if not objects and not upgrades:
            print(f"⚠️  No hay filas válidas para insertar ({skipped} omitidas de {total_rows}).")
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
            parts.append(f"{skipped} omitidas de {total_rows} filas")
        print(", ".join(parts))

    except Exception as exc:
        db.rollback()
        print(f"❌ Error al hacer commit: {exc}")
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
    except Exception as exc:
        print(f"❌ Fatal error during migration: {exc}", file=sys.stderr)
        sys.exit(1)
    print("✅ Migration complete")


if __name__ == "__main__":
    main()
