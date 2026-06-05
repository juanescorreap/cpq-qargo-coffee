"""Pre-flight validator for bulk data templates (data/templates/*.csv).

Dry-run gate that runs BEFORE any INSERT. Validates each CSV against the live
catalog tables WITHOUT writing. Two failure classes (see data/INGESTION_GUIDE.md
§4):

  * ROW error (dirty but resolvable) -> the row is sent to a dead-letter file
    ``data/_rejects/<name>.rejects.csv`` with a ``reject_reason`` column; the
    batch keeps going.
  * STRUCTURAL error (missing required column, unreadable file, empty base
    catalog) -> with ``--strict`` the whole run aborts before touching the DB.

Usage
-----
    python -m backend.migrations.preflight_check data/templates/regions.csv --type regions
    python -m backend.migrations.preflight_check data/templates/ --all
    python -m backend.migrations.preflight_check data/templates/ --all --strict

Exit codes: 0 = all clean · 2 = some rows rejected · 1 = structural error.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import func

import backend.models  # noqa: F401 — registers models in Base.metadata
from backend.database import SessionLocal
from backend.models.currency import Currency
from backend.models.ingredient import Ingredient
from backend.models.product import Product
from backend.models.recipe_unit import RecipeUnit
from backend.models.store import Store
from backend.models.supply_chain import Distributor, Manufacturer, Region

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REJECT_DIR = _PROJECT_ROOT / "data" / "_rejects"


# ---------------------------------------------------------------------------
# Catalog snapshot (loaded once, dry — no writes)
# ---------------------------------------------------------------------------

class Catalog:
    """Lower-cased name/code lookups for fast in-memory FK resolution."""

    def __init__(self, db) -> None:
        self.ingredients = self._name_set(db, Ingredient.name)
        self.products = self._name_set(db, Product.name)
        self.recipe_units = self._name_set(db, RecipeUnit.name)
        self.manufacturers = self._name_set(db, Manufacturer.name)
        self.distributors = self._name_set(db, Distributor.name)
        self.currencies = {c for (c,) in db.query(Currency.code).all()}
        self.region_codes = {c for (c,) in db.query(Region.code).all()}
        self.store_codes = {c for (c,) in db.query(Store.code).all()}

    @staticmethod
    def _name_set(db, col) -> set[str]:
        return {n for (n,) in db.query(func.lower(col)).all()}


def _norm(value: Optional[str]) -> str:
    """Trim + collapse internal whitespace; '' for empties/NaN-likes."""
    if value is None:
        return ""
    s = " ".join(str(value).split())
    return "" if s.lower() in {"nan", "none", "null", "nat"} else s


# ---------------------------------------------------------------------------
# Reusable field validators -> return error string or None
# ---------------------------------------------------------------------------

def _req(label: str) -> Callable[[str], Optional[str]]:
    return lambda v: None if _norm(v) else f"'{label}' es requerido y está vacío"


def _in(cat_attr: str, label: str) -> Callable[[str], Optional[str]]:
    def check(v: str, *, _cat=cat_attr, _l=label) -> Optional[str]:
        nv = _norm(v)
        if not nv:
            return None  # optional ref; required-ness handled separately
        bucket = getattr(check.catalog, _cat)
        key = nv.lower() if _cat not in {"currencies", "region_codes", "store_codes"} else nv
        return None if key in bucket else f"{_l} '{nv}' no existe en catálogo"
    return check


def _positive_decimal(label: str) -> Callable[[str], Optional[str]]:
    def check(v: str, *, _l=label) -> Optional[str]:
        nv = _norm(v)
        if not nv:
            return f"'{_l}' es requerido"
        try:
            if Decimal(nv.replace(",", "")) <= 0:
                return f"'{_l}' debe ser > 0 (got {nv})"
        except InvalidOperation:
            return f"'{_l}' no es numérico ({nv})"
        return None
    return check


def _date_opt(label: str) -> Callable[[str], Optional[str]]:
    def check(v: str, *, _l=label) -> Optional[str]:
        nv = _norm(v)
        if not nv:
            return None
        try:
            datetime.strptime(nv, "%Y-%m-%d")
        except ValueError:
            return f"'{_l}' debe ser YYYY-MM-DD ({nv})"
        return None
    return check


# ---------------------------------------------------------------------------
# Per-template spec: required columns + per-row rule fn(row, catalog) -> [errors]
# ---------------------------------------------------------------------------

def _route_key_errors(row: dict, cat: Catalog) -> list[str]:
    """Validate the (ingredient, manufacturer, distributor) natural route key."""
    errs: list[str] = []
    if _norm(row.get("ingredient_name")).lower() not in cat.ingredients:
        errs.append(f"ingredient_name '{_norm(row.get('ingredient_name'))}' no existe")
    man, dist = _norm(row.get("manufacturer_name")), _norm(row.get("distributor_name"))
    if man and man.lower() not in cat.manufacturers:
        errs.append(f"manufacturer_name '{man}' no existe")
    if dist and dist.lower() not in cat.distributors:
        errs.append(f"distributor_name '{dist}' no existe")
    if not man and not dist:
        errs.append("ruta sin fabricante ni distribuidor")
    return errs


def _v_regions(row, cat):
    e = []
    if not _norm(row.get("name")):
        e.append("'name' requerido")
    if not _norm(row.get("code")):
        e.append("'code' requerido")
    return e


def _v_stores_regions(row, cat):
    e = []
    if _norm(row.get("store_code")) not in cat.store_codes:
        e.append(f"store_code '{_norm(row.get('store_code'))}' no existe")
    if _norm(row.get("region_code")) not in cat.region_codes:
        e.append(f"region_code '{_norm(row.get('region_code'))}' no existe (cargar regions.csv primero)")
    return e


def _v_simple_named(row, cat):
    return [] if _norm(row.get("name")) else ["'name' requerido"]


def _v_routes(row, cat):
    e = _route_key_errors(row, cat)
    is_direct = _norm(row.get("is_direct")).lower() in {"true", "1", "yes", "si", "sí"}
    if is_direct and _norm(row.get("distributor_name")):
        e.append("is_direct=true pero distributor_name no está vacío (CHECK ck_supply_routes_direct_no_distributor)")
    return e


def _v_isr(row, cat):
    e = _route_key_errors(row, cat)
    if not _norm(row.get("external_name")):
        e.append("'external_name' requerido")
    if not _norm(row.get("purchase_unit")):
        e.append("'purchase_unit' requerido")
    return e


def _v_suc(row, cat):
    e = _route_key_errors(row, cat)
    if _norm(row.get("recipe_unit")).lower() not in cat.recipe_units:
        e.append(f"recipe_unit '{_norm(row.get('recipe_unit'))}' no existe en recipe_units")
    for col in ("purchase_qty", "recipe_qty"):
        err = _positive_decimal(col)(row.get(col))
        if err:
            e.append(err)
    return e


def _v_prices(row, cat):
    e = _route_key_errors(row, cat)
    err = _positive_decimal("list_price")(row.get("list_price"))
    if err:
        e.append(err)
    err = _positive_decimal("qargo_price")(row.get("qargo_price"))
    if err:
        e.append(err)
    try:
        if Decimal(_norm(row.get("qargo_price")) or "0") > Decimal(_norm(row.get("list_price")) or "0"):
            e.append("qargo_price > list_price (CHECK ck_srp_qargo_lte_list)")
    except InvalidOperation:
        pass
    if _norm(row.get("currency_code")) not in cat.currencies:
        e.append(f"currency_code '{_norm(row.get('currency_code'))}' no existe en currencies")
    if _norm(row.get("price_unit")).lower() not in cat.recipe_units:
        e.append(f"price_unit '{_norm(row.get('price_unit'))}' no existe en recipe_units")
    if not _norm(row.get("created_by")):
        e.append("'created_by' requerido")
    return e


def _v_ingredient_prices(row, cat):
    e = []
    if _norm(row.get("ingredient_name")).lower() not in cat.ingredients:
        e.append(f"ingredient_name '{_norm(row.get('ingredient_name'))}' no existe")
    err = _positive_decimal("purchase_price")(row.get("purchase_price"))
    if err:
        e.append(err)
    if _norm(row.get("currency_code")) not in cat.currencies:
        e.append(f"currency_code '{_norm(row.get('currency_code'))}' no existe en currencies")
    e += [x for x in [_date_opt("effective_date")(row.get("effective_date"))] if x]
    return e


def _v_assignments(row, cat):
    e = _route_key_errors(row, cat)
    st = _norm(row.get("scope_type")).lower()
    code = _norm(row.get("scope_code"))
    if st == "region":
        if code not in cat.region_codes:
            e.append(f"region scope_code '{code}' no existe")
    elif st == "store":
        if code not in cat.store_codes:
            e.append(f"store scope_code '{code}' no existe")
    else:
        e.append("scope_type debe ser 'region' o 'store'")
    pr = _norm(row.get("priority")) or "1"
    if not pr.isdigit() or int(pr) < 1:
        e.append(f"priority debe ser entero >= 1 ({pr})")
    if not _norm(row.get("assigned_by")):
        e.append("'assigned_by' requerido")
    return e


def _v_availability(row, cat):
    e = []
    if _norm(row.get("ingredient_name")).lower() not in cat.ingredients:
        e.append(f"ingredient_name no existe")
    status = _norm(row.get("status")).lower()
    if status not in {"available", "shortage", "discontinued", "seasonal"}:
        e.append(f"status inválido '{status}'")
    if _norm(row.get("expected_resume")) and status != "shortage":
        e.append("expected_resume solo permitido si status=shortage (CHECK ck_ia_resume_only_for_shortage)")
    return e


def _v_substitutes(row, cat):
    e = []
    orig = _norm(row.get("original_ingredient_name"))
    sub = _norm(row.get("substitute_ingredient_name"))
    if orig.lower() not in cat.ingredients:
        e.append(f"original '{orig}' no existe")
    if sub.lower() not in cat.ingredients:
        e.append(f"substitute '{sub}' no existe")
    if orig and sub and orig.lower() == sub.lower():
        e.append("sustituto = original (CHECK ck_ingredient_substitutes_no_self)")
    if _norm(row.get("activation_condition")) and _norm(row.get("activation_condition")).lower() not in {"shortage", "unavailable", "always"}:
        e.append("activation_condition inválida")
    return e


# columna requerida mínima + validador por tipo
_SPECS: dict[str, tuple[set[str], Callable]] = {
    "regions": ({"name", "code"}, _v_regions),
    "stores_regions": ({"store_code", "region_code"}, _v_stores_regions),
    "manufacturers": ({"name"}, _v_simple_named),
    "distributors": ({"name"}, _v_simple_named),
    "supply_routes": ({"ingredient_name"}, _v_routes),
    "ingredient_supplier_refs": ({"ingredient_name", "external_name", "purchase_unit"}, _v_isr),
    "supplier_unit_conversions": ({"ingredient_name", "recipe_unit", "purchase_qty", "recipe_qty"}, _v_suc),
    "supply_route_prices": ({"ingredient_name", "list_price", "qargo_price", "currency_code", "price_unit", "created_by"}, _v_prices),
    "ingredient_prices": ({"ingredient_name", "purchase_price", "currency_code"}, _v_ingredient_prices),
    "supply_route_assignments": ({"scope_type", "scope_code", "ingredient_name", "assigned_by"}, _v_assignments),
    "ingredient_availability": ({"ingredient_name", "scope_type", "status"}, _v_availability),
    "ingredient_substitutes": ({"original_ingredient_name", "substitute_ingredient_name", "approved_by", "approval_date"}, _v_substitutes),
}


def _infer_type(path: Path) -> Optional[str]:
    return path.stem if path.stem in _SPECS else None


# ---------------------------------------------------------------------------
# Validate one file
# ---------------------------------------------------------------------------

class StructuralError(Exception):
    pass


def validate_file(path: Path, ttype: str, cat: Catalog, strict: bool) -> tuple[int, int]:
    """Return (ok_rows, rejected_rows). Raises StructuralError on schema break."""
    required, rule = _SPECS[ttype]
    # bind catalog to the closure-based _in validators (unused now, kept simple)
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = {(c or "").strip().lower() for c in (reader.fieldnames or [])}
            missing = required - header
            if missing:
                msg = f"{path.name}: faltan columnas requeridas {sorted(missing)}"
                if strict:
                    raise StructuralError(msg)
                print(f"  ❌ ESTRUCTURAL {msg}")
                return (0, 0)

            ok, rejects = 0, []
            seen_keys: set[str] = set()
            for i, raw in enumerate(reader, start=2):
                row = {(k or "").strip().lower(): v for k, v in raw.items()}
                errs = rule(row, cat) or []
                # duplicate natural-key guard for route-keyed files
                if "ingredient_name" in row and ttype in {"supply_routes", "ingredient_supplier_refs"}:
                    key = "|".join(_norm(row.get(c)).lower() for c in ("ingredient_name", "manufacturer_name", "distributor_name"))
                    if key in seen_keys:
                        errs.append("clave de ruta duplicada en el archivo (routing ambiguo)")
                    seen_keys.add(key)
                if errs:
                    rejects.append({**raw, "reject_reason": "; ".join(errs), "_row": i})
                else:
                    ok += 1
    except StructuralError:
        raise
    except Exception as exc:  # unreadable file = structural
        msg = f"{path.name}: ilegible ({exc})"
        if strict:
            raise StructuralError(msg) from exc
        print(f"  ❌ ESTRUCTURAL {msg}")
        return (0, 0)

    if rejects:
        _REJECT_DIR.mkdir(parents=True, exist_ok=True)
        out = _REJECT_DIR / f"{path.stem}.rejects.csv"
        cols = [c for c in rejects[0] if c != "_row"]
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rejects:
                r.pop("_row", None)
                w.writerow(r)
        print(f"  ⚠️  {path.name}: OK={ok}  REJECT={len(rejects)} → {out.relative_to(_PROJECT_ROOT)}")
    else:
        print(f"  ✅ {path.name}: OK={ok}  REJECT=0")
    return (ok, len(rejects))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-flight validator for /data templates")
    ap.add_argument("path", help="CSV file or directory")
    ap.add_argument("--type", help="template type (inferred from filename if omitted)")
    ap.add_argument("--all", action="store_true", help="validate every known CSV in the directory")
    ap.add_argument("--strict", action="store_true", help="abort on the first structural error")
    args = ap.parse_args()

    target = Path(args.path)
    files: list[tuple[Path, str]] = []
    if target.is_dir():
        for p in sorted(target.glob("*.csv")):
            t = _infer_type(p)
            if t:
                files.append((p, t))
            elif not args.all:
                print(f"  ↪︎  {p.name}: tipo desconocido, omitido (usa --type)")
        if not files:
            print("No se encontraron CSV reconocibles.")
            sys.exit(1)
    else:
        t = args.type or _infer_type(target)
        if not t or t not in _SPECS:
            print(f"❌ Tipo no reconocido para {target.name}. Usa --type con uno de: {', '.join(_SPECS)}")
            sys.exit(1)
        files.append((target, t))

    db = SessionLocal()
    try:
        cat = Catalog(db)
        if not cat.currencies:
            print("❌ ESTRUCTURAL: tabla currencies vacía — sembrar catálogos base primero.")
            sys.exit(1)
    finally:
        db.close()

    total_ok = total_rej = 0
    print(f"Pre-flight ({len(files)} archivo(s)) — strict={args.strict}\n")
    try:
        for p, t in files:
            ok, rej = validate_file(p, t, cat, args.strict)
            total_ok += ok
            total_rej += rej
    except StructuralError as exc:
        print(f"\n❌ ABORT (strict): {exc}")
        sys.exit(1)

    print(f"\nResumen: OK={total_ok}  REJECT={total_rej}")
    sys.exit(2 if total_rej else 0)


if __name__ == "__main__":
    main()
