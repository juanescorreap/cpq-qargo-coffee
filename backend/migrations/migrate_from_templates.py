"""Bulk loader for the supply-chain CSV templates (data/templates/*.csv).

Companion to ``migrate_from_excel.py`` (which loads the 5 core .xlsx files).
This one ingests the supply-chain layer the Excel path never covered:
regions, manufacturers, distributors, store↔region links, catalogue prices,
supply routes, supplier refs/conversions, route prices, route assignments,
availability and substitutes.

Design
------
* Loads files in strict DAG order (see data/INGESTION_GUIDE.md §2). Each phase
  re-reads its lookup maps from the DB, so a route inserted in phase 2 is
  visible to the price loader in phase 3.
* Natural-key resolution (no IDs in the CSVs): the route key is the triple
  ``(ingredient_name, manufacturer_name, distributor_name)`` — empty
  distributor = direct purchase.
* Row-level resilience: every row runs inside a SAVEPOINT. A resolvable error
  (unknown name, constraint violation, qargo>list) rolls back just that row and
  appends it to ``data/_rejects/<name>.rejects.csv`` with a ``reject_reason``;
  the batch keeps going. A structural error (missing required column) skips the
  file, or aborts the whole run under ``--strict``.
* Idempotent: re-running skips rows already present (by natural key).

Run
---
    python -m backend.migrations.migrate_from_templates                # all, DAG order
    python -m backend.migrations.migrate_from_templates --only regions # one file
    python -m backend.migrations.migrate_from_templates --strict       # abort on structural
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError

import backend.models  # noqa: F401 — registers models in Base.metadata
from backend.database import SessionLocal
from backend.migrations.preflight_check import _norm
from backend.migrations.utils import safe_decimal
from backend.models.ingredient import Ingredient, IngredientPriceHistory
from backend.models.recipe_unit import RecipeUnit
from backend.models.store import Store
from backend.models.supply_chain import (
    Distributor,
    IngredientAvailability,
    IngredientSubstitute,
    IngredientSupplierRef,
    Manufacturer,
    Region,
    SupplierUnitConversion,
    SupplyRoute,
    SupplyRouteAssignment,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_DIR = _PROJECT_ROOT / "data" / "templates"
_REJECT_DIR = _PROJECT_ROOT / "data" / "_rejects"


# ---------------------------------------------------------------------------
# Small parsers
# ---------------------------------------------------------------------------

def _to_bool(value: object, default: bool = False) -> bool:
    s = _norm(str(value) if value is not None else "")
    if not s:
        return default
    return s.lower() in {"true", "1", "yes", "si", "sí"}


def _to_date(value: object) -> Optional[date]:
    s = _norm(str(value) if value is not None else "")
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


class RowError(Exception):
    """Resolvable per-row problem → dead-letter, keep going."""


class StructuralError(Exception):
    """File-level problem → skip file (or abort under --strict)."""


# ---------------------------------------------------------------------------
# CSV reader + dead-letter writer
# ---------------------------------------------------------------------------

def _read(path: Path, required: set[str]) -> tuple[list[dict], list[str]]:
    if not path.exists():
        raise StructuralError(f"{path.name}: no existe")
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = {(c or "").strip().lower() for c in (reader.fieldnames or [])}
        missing = required - header
        if missing:
            raise StructuralError(f"{path.name}: faltan columnas {sorted(missing)}")
        rows = [{(k or "").strip().lower(): v for k, v in r.items()} for r in reader]
    return rows, list(reader.fieldnames or [])


def _write_rejects(stem: str, rejects: list[dict]) -> Optional[Path]:
    if not rejects:
        return None
    _REJECT_DIR.mkdir(parents=True, exist_ok=True)
    out = _REJECT_DIR / f"{stem}.rejects.csv"
    cols = [c for c in rejects[0] if c != "_row"]
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rejects:
            r.pop("_row", None)
            w.writerow(r)
    return out


# ---------------------------------------------------------------------------
# Lookup maps (rebuilt per phase so later loaders see earlier inserts)
# ---------------------------------------------------------------------------

class Maps:
    def __init__(self, db) -> None:
        self.db = db
        self.refresh()

    def refresh(self) -> None:
        db = self.db
        self.ingredient = self._lower_map(db, Ingredient.id, Ingredient.name)
        self.recipe_unit = self._lower_map(db, RecipeUnit.id, RecipeUnit.name)
        self.manufacturer = self._lower_map(db, Manufacturer.id, Manufacturer.name)
        self.distributor = self._lower_map(db, Distributor.id, Distributor.name)
        self.region = {c: i for (i, c) in db.query(Region.id, Region.code).all()}
        self.store = {c: i for (i, c) in db.query(Store.id, Store.code).all()}
        # route key (ingredient_id, manufacturer_id|None, distributor_id|None) -> route_id
        self.route: dict[tuple, int] = {
            (r.ingredient_id, r.manufacturer_id, r.distributor_id): r.id
            for r in db.query(
                SupplyRoute.id,
                SupplyRoute.ingredient_id,
                SupplyRoute.manufacturer_id,
                SupplyRoute.distributor_id,
            ).all()
        }
        # supplier ref by (ingredient_id, route_id) -> ref_id
        # Note: one entry per (ing, route) pair; used by resolvers.py for SUC lookup.
        # Multiple refs per (ing, route) (different SKUs) collapse to the last loaded —
        # acceptable for the resolver because SUC links all conversions to that ref_id.
        _isr_rows = db.query(
            IngredientSupplierRef.id,
            IngredientSupplierRef.ingredient_id,
            IngredientSupplierRef.supply_route_id,
            IngredientSupplierRef.external_code,
        ).all()
        self.ref: dict[tuple, int] = {
            (s.ingredient_id, s.supply_route_id): s.id
            for s in _isr_rows
        }
        # Exact dedup key for _h_isr: (route_id, external_code).
        # Prevents incorrectly skipping additional SKUs for the same route.
        self.isr_exist: set[tuple] = {
            (s.supply_route_id, s.external_code) for s in _isr_rows
        }

    @staticmethod
    def _lower_map(db, id_col, name_col) -> dict[str, int]:
        return {n: i for (i, n) in db.query(id_col, func.lower(name_col)).all()}

    # -- resolvers (raise RowError when unresolved) --
    def ing(self, name: str) -> int:
        i = self.ingredient.get(_norm(name).lower())
        if i is None:
            raise RowError(f"ingredient_name '{_norm(name)}' no existe")
        return i

    def ru(self, name: str) -> int:
        i = self.recipe_unit.get(_norm(name).lower())
        if i is None:
            raise RowError(f"recipe_unit '{_norm(name)}' no existe en recipe_units")
        return i

    def route_key(self, row: dict) -> tuple[int, Optional[int], Optional[int]]:
        ing_id = self.ing(row.get("ingredient_name"))
        man = _norm(row.get("manufacturer_name"))
        dist = _norm(row.get("distributor_name"))
        man_id = self.manufacturer.get(man.lower()) if man else None
        dist_id = self.distributor.get(dist.lower()) if dist else None
        if man and man_id is None:
            raise RowError(f"manufacturer_name '{man}' no existe")
        if dist and dist_id is None:
            raise RowError(f"distributor_name '{dist}' no existe")
        if man_id is None and dist_id is None:
            raise RowError("ruta sin fabricante ni distribuidor")
        return (ing_id, man_id, dist_id)

    def route_id(self, row: dict) -> int:
        key = self.route_key(row)
        rid = self.route.get(key)
        if rid is None:
            raise RowError("ruta no existe (cargar supply_routes.csv primero)")
        return rid


# ---------------------------------------------------------------------------
# Generic file processor: per-row savepoint + dead-letter
# ---------------------------------------------------------------------------

def _process(
    db,
    path: Path,
    required: set[str],
    handler: Callable[[dict, Maps], Optional[str]],
    maps: Maps,
    strict: bool,
) -> tuple[int, int, int]:
    """Returns (inserted, skipped, rejected). handler returns 'skip' to dedup,
    None on insert, or raises RowError to dead-letter."""
    try:
        rows, _ = _read(path, required)
    except StructuralError as exc:
        if strict:
            raise
        print(f"  ❌ ESTRUCTURAL {exc}")
        return (0, 0, 0)

    inserted = skipped = 0
    rejects: list[dict] = []
    for i, raw in enumerate(rows, start=2):
        sp = db.begin_nested()
        try:
            result = handler(raw, maps)
            if result == "skip":
                sp.rollback()
                skipped += 1
            else:
                db.flush()       # surface constraint errors inside this savepoint
                sp.commit()
                inserted += 1
        except RowError as exc:
            sp.rollback()
            rejects.append({**raw, "reject_reason": str(exc)})
        except SQLAlchemyError as exc:
            sp.rollback()
            msg = str(getattr(exc, "orig", exc)).splitlines()[0][:200]
            rejects.append({**raw, "reject_reason": f"DB: {msg}"})

    db.commit()
    maps.refresh()
    out = _write_rejects(path.stem, rejects)
    tag = "✅" if not rejects else "⚠️ "
    extra = f" → {out.relative_to(_PROJECT_ROOT)}" if out else ""
    print(f"  {tag} {path.name}: insert={inserted} skip={skipped} reject={len(rejects)}{extra}")
    return (inserted, skipped, len(rejects))


# ---------------------------------------------------------------------------
# Per-template handlers
# ---------------------------------------------------------------------------

def _h_regions(row, m: Maps):
    code = _norm(row.get("code"))
    if not _norm(row.get("name")) or not code:
        raise RowError("name y code requeridos")
    if code in m.region:
        return "skip"
    m.db.add(Region(
        name=_norm(row.get("name")), code=code,
        country_code=_norm(row.get("country_code")) or "CO",
        is_active=_to_bool(row.get("is_active"), True),
    ))


def _h_manufacturers(row, m: Maps):
    name = _norm(row.get("name"))
    if not name:
        raise RowError("name requerido")
    if name.lower() in m.manufacturer:
        return "skip"
    m.db.add(Manufacturer(
        name=name, country_code=_norm(row.get("country_code")) or "CO",
        tax_id=_norm(row.get("tax_id")) or None,
        website=_norm(row.get("website")) or None,
        is_active=_to_bool(row.get("is_active"), True),
    ))


def _h_distributors(row, m: Maps):
    name = _norm(row.get("name"))
    if not name:
        raise RowError("name requerido")
    if name.lower() in m.distributor:
        return "skip"
    m.db.add(Distributor(
        name=name, country_code=_norm(row.get("country_code")) or "CO",
        tax_id=_norm(row.get("tax_id")) or None,
        contact_email=_norm(row.get("contact_email")) or None,
        contact_phone=_norm(row.get("contact_phone")) or None,
        is_active=_to_bool(row.get("is_active"), True),
    ))


def _h_stores_regions(row, m: Maps):
    sc, rc = _norm(row.get("store_code")), _norm(row.get("region_code"))
    sid = m.store.get(sc)
    if sid is None:
        raise RowError(f"store_code '{sc}' no existe")
    rid = m.region.get(rc)
    if rid is None:
        raise RowError(f"region_code '{rc}' no existe (cargar regions.csv primero)")
    store = m.db.get(Store, sid)
    if store.region_id == rid:
        return "skip"
    store.region_id = rid


def _h_ingredient_prices(row, m: Maps):
    ing_id = m.ing(row.get("ingredient_name"))
    price = safe_decimal(row.get("purchase_price"))
    if not price or price <= 0:
        raise RowError(f"purchase_price inválido ({row.get('purchase_price')})")
    ccy = _norm(row.get("currency_code"))
    if len(ccy) != 3:
        raise RowError(f"currency_code inválido ({ccy})")
    ing = m.db.get(Ingredient, ing_id)
    ing.purchase_price = price  # engine catalogue fallback reads this
    # Append to history → sync trigger denormalises current_price.
    m.db.add(IngredientPriceHistory(
        ingredient_id=ing_id, price=price,
        source=_norm(row.get("source")) or "bulk_upload",
    ))


def _h_supply_routes(row, m: Maps):
    key = m.route_key(row)
    if key in m.route:
        return "skip"
    is_direct = _to_bool(row.get("is_direct"), False)
    if is_direct and key[2] is not None:
        raise RowError("is_direct=true con distribuidor (viola CHECK)")
    m.db.add(SupplyRoute(
        ingredient_id=key[0], manufacturer_id=key[1], distributor_id=key[2],
        is_direct=is_direct, is_active=_to_bool(row.get("is_active"), True),
    ))


def _h_isr(row, m: Maps):
    ing_id = m.ing(row.get("ingredient_name"))
    rid = m.route_id(row)
    ext_code = _norm(row.get("external_code")) or None
    if (rid, ext_code) in m.isr_exist:
        return "skip"
    ext = _norm(row.get("external_name"))
    pu = _norm(row.get("purchase_unit"))
    if not ext or not pu:
        raise RowError("external_name y purchase_unit requeridos")
    m.db.add(IngredientSupplierRef(
        ingredient_id=ing_id, supply_route_id=rid, external_name=ext,
        external_code=_norm(row.get("external_code")) or None, purchase_unit=pu,
        units_per_pack=safe_decimal(row.get("units_per_pack")) or None,
    ))


def _h_suc(row, m: Maps):
    from backend.migrations.resolvers import ResolveStatus, resolve_supplier_ref

    result = resolve_supplier_ref(
        m,
        row.get("ingredient_name"),
        row.get("manufacturer_name"),
        row.get("distributor_name"),
    )
    if result.status == ResolveStatus.NOT_FOUND:
        raise RowError(result.reason)
    if result.status == ResolveStatus.AMBIGUOUS:
        raise RowError(
            f"ambiguo: '{row.get('ingredient_name')}' resuelve a "
            f"{len(result.candidates)} refs posibles (IDs: {result.candidates}) — "
            f"especificar fabricante o distribuidor en CSV para desambiguar"
        )
    ref_id = result.ingredient_ref_id

    ru_id = m.ru(row.get("recipe_unit"))
    pq, rq = safe_decimal(row.get("purchase_qty")), safe_decimal(row.get("recipe_qty"))
    if not pq or pq <= 0 or not rq or rq <= 0:
        raise RowError("purchase_qty y recipe_qty deben ser > 0")
    m.db.add(SupplierUnitConversion(
        ingredient_ref_id=ref_id, recipe_unit_id=ru_id, purchase_qty=pq, recipe_qty=rq,
        notes=_norm(row.get("notes")) or None,
    ))


_INGEST_PRICE_SQL = (
    "SELECT fn_ingest_route_price(:rid, :lp, :qp, :ccy, :puid, :ppu, :src, :by, "
    "COALESCE(:vf, CURRENT_DATE))"
)


def _h_route_prices(row, m: Maps):
    rid = m.route_id(row)
    lp, qp = safe_decimal(row.get("list_price")), safe_decimal(row.get("qargo_price"))
    if not lp or lp <= 0 or not qp or qp <= 0:
        raise RowError("list_price y qargo_price deben ser > 0")
    ccy = _norm(row.get("currency_code"))
    # price_unit_id is nullable — free text (e.g. "per case") is valid; only try FK lookup
    pu_text = _norm(row.get("price_unit"))
    if not pu_text:
        raise RowError("price_unit requerido (texto libre, ej: 'per case', 'per unit')")
    puid = m.recipe_unit.get(pu_text.lower())  # None if free text — that's OK
    by = _norm(row.get("created_by"))
    if not by:
        raise RowError("created_by requerido")
    # fn enforces qargo<=list, temporal close+insert, advisory lock, outbox enqueue.
    m.db.execute(text(_INGEST_PRICE_SQL), {
        "rid": rid, "lp": str(lp), "qp": str(qp), "ccy": ccy, "puid": puid,
        "ppu": _norm(row.get("price_unit")), "src": _norm(row.get("source")) or None,
        "by": by, "vf": _to_date(row.get("valid_from")),
    })


def _h_assignments(row, m: Maps):
    rid = m.route_id(row)
    st = _norm(row.get("scope_type")).lower()
    code = _norm(row.get("scope_code"))
    region_id = store_id = None
    if st == "region":
        region_id = m.region.get(code)
        if region_id is None:
            raise RowError(f"region scope_code '{code}' no existe")
    elif st == "store":
        store_id = m.store.get(code)
        if store_id is None:
            raise RowError(f"store scope_code '{code}' no existe")
    else:
        raise RowError("scope_type debe ser 'region' o 'store'")
    by = _norm(row.get("assigned_by"))
    if not by:
        raise RowError("assigned_by requerido")
    pr = _norm(row.get("priority")) or "1"
    m.db.add(SupplyRouteAssignment(
        supply_route_id=rid, region_id=region_id, store_id=store_id,
        priority=int(pr), valid_from=_to_date(row.get("valid_from")) or date.today(),
        assigned_by=by, change_reason=_norm(row.get("change_reason")) or None,
    ))


def _h_availability(row, m: Maps):
    ing_id = m.ing(row.get("ingredient_name"))
    st = _norm(row.get("scope_type")).lower()
    route_id = region_id = None
    if st == "route":
        route_id = m.route_id(row)
    elif st == "region":
        region_id = m.region.get(_norm(row.get("region_code")))
        if region_id is None:
            raise RowError(f"region_code '{_norm(row.get('region_code'))}' no existe")
    else:
        raise RowError("scope_type debe ser 'route' o 'region'")
    status = _norm(row.get("status")).lower()
    m.db.add(IngredientAvailability(
        ingredient_id=ing_id, supply_route_id=route_id, region_id=region_id,
        status=status, expected_resume=_to_date(row.get("expected_resume")),
        valid_from=_to_date(row.get("valid_from")) or date.today(),
        reported_by=_norm(row.get("reported_by")) or None,
    ))


def _h_substitutes(row, m: Maps):
    orig = m.ing(row.get("original_ingredient_name"))
    sub = m.ing(row.get("substitute_ingredient_name"))
    appr_date = _to_date(row.get("approval_date"))
    if appr_date is None:
        raise RowError("approval_date requerido (YYYY-MM-DD)")
    ru_name = _norm(row.get("recipe_unit"))
    m.db.add(IngredientSubstitute(
        original_ingredient_id=orig, substitute_ingredient_id=sub,
        approved_by=_norm(row.get("approved_by")), approval_date=appr_date,
        activation_condition=_norm(row.get("activation_condition")) or "shortage",
        quantity_ratio=safe_decimal(row.get("quantity_ratio")) or Decimal("1.0"),
        recipe_unit_id=m.ru(ru_name) if ru_name else None,
        cost_impact_pct=safe_decimal(row.get("cost_impact_pct")),
        valid_from=_to_date(row.get("valid_from")) or date.today(),
    ))


# (filename stem, required cols, handler) in strict DAG order
_PIPELINE: list[tuple[str, set[str], Callable]] = [
    ("regions", {"name", "code"}, _h_regions),
    ("manufacturers", {"name"}, _h_manufacturers),
    ("distributors", {"name"}, _h_distributors),
    ("stores_regions", {"store_code", "region_code"}, _h_stores_regions),
    ("ingredient_prices", {"ingredient_name", "purchase_price", "currency_code"}, _h_ingredient_prices),
    ("supply_routes", {"ingredient_name"}, _h_supply_routes),
    ("ingredient_supplier_refs", {"ingredient_name", "external_name", "purchase_unit"}, _h_isr),
    ("supplier_unit_conversions", {"ingredient_name", "recipe_unit", "purchase_qty", "recipe_qty"}, _h_suc),
    ("supply_route_prices", {"ingredient_name", "list_price", "qargo_price", "currency_code", "price_unit", "created_by"}, _h_route_prices),
    ("supply_route_assignments", {"scope_type", "scope_code", "ingredient_name", "assigned_by"}, _h_assignments),
    ("ingredient_availability", {"ingredient_name", "scope_type", "status"}, _h_availability),
    ("ingredient_substitutes", {"original_ingredient_name", "substitute_ingredient_name", "approved_by", "approval_date"}, _h_substitutes),
]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Load supply-chain CSV templates in DAG order")
    ap.add_argument("--dir", default=str(_TEMPLATE_DIR), help="templates directory")
    ap.add_argument("--only", help="load a single template by stem (e.g. regions)")
    ap.add_argument("--strict", action="store_true", help="abort on structural error")
    args = ap.parse_args()

    tdir = Path(args.dir)
    pipeline = _PIPELINE
    if args.only:
        pipeline = [x for x in _PIPELINE if x[0] == args.only]
        if not pipeline:
            print(f"❌ '{args.only}' no es una plantilla válida: {[p[0] for p in _PIPELINE]}")
            sys.exit(1)

    print(f"Cargando {len(pipeline)} plantilla(s) desde {tdir} — strict={args.strict}\n")
    db = SessionLocal()
    tot_ins = tot_skip = tot_rej = 0
    try:
        maps = Maps(db)
        for stem, required, handler in pipeline:
            path = tdir / f"{stem}.csv"
            if not path.exists():
                print(f"  ↪︎  {stem}.csv: ausente, omitido")
                continue
            try:
                ins, skip, rej = _process(db, path, required, handler, maps, args.strict)
            except StructuralError as exc:
                print(f"\n❌ ABORT (strict): {exc}")
                sys.exit(1)
            tot_ins += ins
            tot_skip += skip
            tot_rej += rej
    finally:
        db.close()

    print(f"\nResumen: insert={tot_ins}  skip={tot_skip}  reject={tot_rej}")
    if tot_rej:
        print("Revisa data/_rejects/ , corrige y re-corre. Exit 2.")
    sys.exit(2 if tot_rej else 0)


if __name__ == "__main__":
    main()
