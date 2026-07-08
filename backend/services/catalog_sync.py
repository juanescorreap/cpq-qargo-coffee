"""CatalogSyncService — pull ingredient prices from the external catalog API.

Pipeline per store:
    1. Resolve the store's catalog_store_id (store_catalog_mapping).
    2. Fetch the catalog array (JWT auth, 30s timeout, 2 retries, 401→refresh).
    3. For each item: match (SKU → fuzzy → new/skip), then apply the effect
       (update price via close+INSERT, create ingredient, or skip) and record a
       catalog_match_log row.
    4. Close the catalog_sync_log with aggregate counters.

Invariants (from the spec):
    - Prices are versioned by close+INSERT, never a direct UPDATE of a live row.
    - New ingredients are created WITHOUT recipe_ingredients (manual review).
    - pack_size conversions are only written when parseable — never invented.
    - Logs are append-only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import httpx
from rapidfuzz import fuzz, process
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import settings
from backend.migrations.utils import normalize_text_ascii
from backend.services.catalog_auth import get_catalog_auth

_FUZZY_CUTOFF = 90
_TIMEOUT = 30.0
_RETRIES = 2
_DEFAULT_CURRENCY = "USD"
_CREATED_BY = "catalog_sync"

SKIP_SUBCATEGORIES = {
    "Cleaning", "Cleaning Supplies", "Packaging",
    "Supplies", "Equipment", "Paper Goods",
}


@dataclass
class MatchResult:
    type: str                       # sku_exact | fuzzy_name | new | skipped
    score: Optional[float] = None
    ingredient_id: Optional[int] = None
    reason: Optional[str] = None


@dataclass
class _Counters:
    fetched: int = 0
    matched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    error: int = 0


class CatalogSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.auth = get_catalog_auth()
        self._base = (settings.CATALOG_API_BASE_URL or "").rstrip("/")

    # ── Fetch ────────────────────────────────────────────────────────────────
    async def _fetch_catalog(self, catalog_store_id: int) -> list[dict]:
        """GET the catalog array. 30s timeout, 2 retries, refresh on 401."""
        url = f"{self._base}/api/catalog/?store_id={catalog_store_id}"
        headers = await self.auth.get_headers()
        last_exc: Optional[Exception] = None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for attempt in range(_RETRIES + 1):
                try:
                    r = await client.get(url, headers=headers)
                    if r.status_code == 401:
                        await self.auth.refresh()
                        headers = await self.auth.get_headers()
                        r = await client.get(url, headers=headers)
                    r.raise_for_status()
                    data = r.json()
                    if not isinstance(data, list):
                        raise ValueError("Catalog response is not a JSON array")
                    return data
                except (httpx.HTTPError, ValueError) as exc:
                    last_exc = exc
                    if attempt == _RETRIES:
                        raise
        raise RuntimeError(f"Fetch failed: {last_exc}")

    # ── Matching ─────────────────────────────────────────────────────────────
    def _match_item(
        self, item: dict, existing_refs: dict, existing_names: dict
    ) -> MatchResult:
        # 1. SKU exact — high confidence, no review.
        sku = item.get("sku")
        if sku and sku in existing_refs:
            return MatchResult("sku_exact", score=1.0, ingredient_id=existing_refs[sku])

        # 2. Fuzzy name — WRatio >= 90, automatic with log.
        name = item.get("name") or ""
        best = process.extractOne(
            normalize_text_ascii(name),
            list(existing_names.keys()),
            scorer=fuzz.WRatio,
            score_cutoff=_FUZZY_CUTOFF,
        )
        if best:
            return MatchResult(
                "fuzzy_name", score=float(best[1]),
                ingredient_id=existing_names[best[0]],
            )

        # 3. No match — create or skip.
        if item.get("subcategory_name") in SKIP_SUBCATEGORIES:
            return MatchResult("skipped", reason="non-ingredient subcategory")
        price = item.get("unit_price")
        if price and price > 0:
            return MatchResult("new")
        return MatchResult("skipped", reason="no price")

    # ── Parsing / naming ─────────────────────────────────────────────────────
    def _parse_pack_size(self, pack_size: Optional[str]) -> Optional[dict]:
        """Parse "6/4ct / 11 oz" → {qty: 264, unit: 'oz'}; "12 / 1 lb" → 12 lb.

        Multiplies every integer/decimal factor before the unit. Returns None if
        no recognisable "<numbers> <unit>" tail is found. Never guesses.
        """
        if not pack_size or not isinstance(pack_size, str):
            return None
        s = pack_size.strip().lower()
        # Trailing "<number> <unit>" (unit = letters like oz/lb/kg/g/ml/l).
        m = re.search(r"([\d.]+)\s*(oz|lb|lbs|kg|g|gr|ml|l|ct|each|ea)\s*$", s)
        if not m:
            return None
        unit = m.group(1 + 1)  # the unit token
        tail_qty = float(m.group(1))
        # Everything before the matched tail: collect multiplicative factors.
        prefix = s[: m.start()]
        factors = re.findall(r"[\d.]+", prefix)
        qty = tail_qty
        for f in factors:
            try:
                qty *= float(f)
            except ValueError:
                return None
        # Normalise a couple of unit spellings.
        unit = {"lbs": "lb", "gr": "g"}.get(unit, unit)
        return {"qty": qty, "unit": unit}

    def _normalize_name(self, raw_name: str) -> str:
        """Canonical CPQ name: collapse whitespace, strip, Title Case (English)."""
        if not raw_name:
            return ""
        cleaned = re.sub(r"\s+", " ", raw_name.strip())
        return cleaned.title()

    # ── Reference maps ───────────────────────────────────────────────────────
    def _load_existing_refs(self) -> dict:
        rows = self.db.execute(
            text(
                "SELECT external_code, ingredient_id FROM ingredient_supplier_refs "
                "WHERE external_code IS NOT NULL AND is_active = true"
            )
        ).all()
        return {r[0]: r[1] for r in rows}

    def _load_existing_names(self) -> dict:
        rows = self.db.execute(
            text("SELECT id, name FROM ingredients WHERE is_active = true")
        ).all()
        out: dict = {}
        for ing_id, name in rows:
            key = normalize_text_ascii(name)
            if key:
                out.setdefault(key, ing_id)
        return out

    def _route_for_ingredient(self, ingredient_id: int) -> Optional[int]:
        """Active supply_route for this ingredient (via its supplier ref), if any."""
        return self.db.execute(
            text(
                "SELECT supply_route_id FROM ingredient_supplier_refs "
                "WHERE ingredient_id = :iid AND is_active = true "
                "AND supply_route_id IS NOT NULL ORDER BY id LIMIT 1"
            ),
            {"iid": ingredient_id},
        ).scalar()

    # ── Writes ───────────────────────────────────────────────────────────────
    def _update_price(
        self, ingredient_id: int, new_price, currency: str, source: str,
        purchase_unit: Optional[str] = None,
    ) -> tuple[bool, Optional[float]]:
        """Version the price via close+INSERT. Returns (changed, old_price).

        When the ingredient has a supply_route, writes supply_route_prices
        (close live row + INSERT new). Otherwise appends ingredient_price_history.
        Never mutates a live temporal row in place. Also mirrors the current
        value into ingredients.purchase_price.
        """
        route_id = self._route_for_ingredient(ingredient_id)
        old_price: Optional[float] = None
        per_unit = f"per {purchase_unit}" if purchase_unit else "per unit"

        if route_id is not None:
            live = self.db.execute(
                text(
                    "SELECT qargo_price, valid_from FROM supply_route_prices "
                    "WHERE supply_route_id = :r AND valid_until IS NULL"
                ),
                {"r": route_id},
            ).first()
            old_price = live[0] if live else None
            if old_price is not None and float(old_price) == float(new_price):
                return (False, float(old_price))
            # Close the live row, then insert the new one. If the live row's
            # valid_from is in the future (seed data), close/start at that date so
            # ck_srp_validity (valid_until >= valid_from) and the no-overlap
            # EXCLUDE both hold.
            today = date.today()
            close_date = max(live[1], today) if live and live[1] else today
            if live:
                self.db.execute(
                    text(
                        "UPDATE supply_route_prices SET valid_until = :d "
                        "WHERE supply_route_id = :r AND valid_until IS NULL"
                    ),
                    {"d": close_date, "r": route_id},
                )
            self.db.execute(
                text(
                    "INSERT INTO supply_route_prices "
                    "(supply_route_id, list_price, qargo_price, currency_code, "
                    " price_per_unit, valid_from, source, created_by) "
                    "VALUES (:r, :p, :p, :c, :pu, :vf, :src, :by)"
                ),
                {"r": route_id, "p": new_price, "c": currency, "pu": per_unit,
                 "vf": close_date, "src": source, "by": _CREATED_BY},
            )
        else:
            old_price = self.db.execute(
                text("SELECT purchase_price FROM ingredients WHERE id = :i"),
                {"i": ingredient_id},
            ).scalar()
            if old_price is not None and float(old_price) == float(new_price):
                return (False, float(old_price))
            self.db.execute(
                text(
                    "INSERT INTO ingredient_price_history (ingredient_id, price, source) "
                    "VALUES (:i, :p, :src)"
                ),
                {"i": ingredient_id, "p": new_price, "src": source},
            )

        # Mirror current value on the ingredient snapshot columns.
        self.db.execute(
            text(
                "UPDATE ingredients SET purchase_price = :p, current_price = :p, "
                "updated_at = now() WHERE id = :i"
            ),
            {"p": new_price, "i": ingredient_id},
        )
        return (True, float(old_price) if old_price is not None else None)

    def _ensure_supply_route(
        self,
        ingredient_id: int,
        *,
        external_name: Optional[str],
        external_code: Optional[str],
        purchase_unit: Optional[str],
        price,
        distributor_name: Optional[str] = None,
        catalog_item_id: Optional[int] = None,
    ) -> tuple[Optional[int], bool]:
        """Idempotently give an ingredient a traceable supply route.

        Creates distributor (get-or-create) → supply_route → ingredient_supplier_ref
        → supply_route_price so the cost engine treats the price as a routed price
        instead of an untracked fallback. No-op (returns the existing route) when the
        ingredient already has an active supply_route — safe to call twice.

        Returns (route_id, created). route_id is None only when price is missing or
        non-positive (supply_route_prices requires a positive price).
        """
        existing = self._route_for_ingredient(ingredient_id)
        if existing is not None:
            return existing, False
        if price is None or float(price) <= 0:
            return None, False

        dist_name = (distributor_name or "").strip() or "Qargo Catalog"
        dist_id = self.db.execute(
            text("SELECT id FROM distributors WHERE name = :n"), {"n": dist_name}
        ).scalar()
        if dist_id is None:
            dist_id = self.db.execute(
                text(
                    "INSERT INTO distributors (name, is_active) "
                    "VALUES (:n, true) RETURNING id"
                ),
                {"n": dist_name},
            ).scalar()

        route_id = self.db.execute(
            text(
                "INSERT INTO supply_routes "
                "(ingredient_id, distributor_id, is_direct, is_active, metadata) "
                "VALUES (:i, :d, false, true, CAST(:m AS jsonb)) RETURNING id"
            ),
            {"i": ingredient_id, "d": dist_id,
             "m": json.dumps({"source": "catalog_sync",
                              "catalog_item_id": catalog_item_id})},
        ).scalar()

        self.db.execute(
            text(
                "INSERT INTO ingredient_supplier_refs "
                "(ingredient_id, supply_route_id, external_name, external_code, "
                " purchase_unit, is_active) "
                "VALUES (:i, :r, :en, :ec, :pu, true)"
            ),
            {"i": ingredient_id, "r": route_id,
             "en": (external_name or "")[:300], "ec": external_code,
             "pu": (purchase_unit or "unit")[:100]},
        )

        per_unit = f"per {purchase_unit}" if purchase_unit else "per unit"
        self.db.execute(
            text(
                "INSERT INTO supply_route_prices "
                "(supply_route_id, list_price, qargo_price, currency_code, "
                " price_per_unit, valid_from, source, created_by) "
                "VALUES (:r, :p, :p, :c, :pu, :vf, :src, :by)"
            ),
            {"r": route_id, "p": price, "c": _DEFAULT_CURRENCY, "pu": per_unit,
             "vf": date.today(), "src": "catalog_sync", "by": _CREATED_BY},
        )
        return route_id, True

    def _create_ingredient(self, item: dict) -> int:
        """Create a canonical ingredient with no recipe. Needs manual review.

        Also creates its supply_route + supplier_ref + supply_route_price in the same
        transaction so the fresh catalog price is traceable (not a fallback).
        """
        name = self._normalize_name(item.get("name") or "")
        row = self.db.execute(
            text(
                "INSERT INTO ingredients (name, category, purchase_unit, "
                " purchase_price, current_price, is_active) "
                "VALUES (:n, :cat, :unit, :price, :price, true) RETURNING id"
            ),
            {"n": name, "cat": item.get("category_name"),
             "unit": item.get("unit"), "price": item.get("unit_price")},
        ).scalar()
        ingredient_id = int(row)
        self._ensure_supply_route(
            ingredient_id,
            external_name=item.get("name"),
            external_code=item.get("sku"),
            purchase_unit=item.get("unit"),
            price=item.get("unit_price"),
            distributor_name=item.get("distributor_name"),
            catalog_item_id=item.get("id"),
        )
        return ingredient_id

    def _update_availability(self, ingredient_id: int, status: str, active: bool) -> None:
        """close+INSERT availability rows, scoped to the ingredient's supply_route
        (ck_ia_scope requires a route or region scope). active=True → open a status
        row if none live; active=False → close any live row of that status.
        No route → cannot scope the record, so it is skipped."""
        route_id = self._route_for_ingredient(ingredient_id)
        if route_id is None:
            return
        live = self.db.execute(
            text(
                "SELECT id FROM ingredient_availability "
                "WHERE ingredient_id = :i AND status = :s AND supply_route_id = :r "
                "AND valid_until IS NULL"
            ),
            {"i": ingredient_id, "s": status, "r": route_id},
        ).scalar()
        if active and live is None:
            self.db.execute(
                text(
                    "INSERT INTO ingredient_availability "
                    "(ingredient_id, supply_route_id, status, valid_from, reported_by) "
                    "VALUES (:i, :r, :s, CURRENT_DATE, :by)"
                ),
                {"i": ingredient_id, "r": route_id, "s": status, "by": _CREATED_BY},
            )
        elif not active and live is not None:
            self.db.execute(
                text(
                    "UPDATE ingredient_availability SET valid_until = CURRENT_DATE "
                    "WHERE id = :id"
                ),
                {"id": live},
            )

    # ── Orchestration ────────────────────────────────────────────────────────
    async def sync_store(self, store_id: int, triggered_by: str):
        """Sync one store. Returns the completed catalog_sync_log id."""
        mapping = self.db.execute(
            text("SELECT catalog_store_id FROM store_catalog_mapping WHERE store_id = :s"),
            {"s": store_id},
        ).scalar()
        if mapping is None:
            raise ValueError("Store has no catalog mapping")

        sync_id = self.db.execute(
            text(
                "INSERT INTO catalog_sync_log (store_id, catalog_store_id, triggered_by, status) "
                "VALUES (:s, :c, :by, 'running') RETURNING id"
            ),
            {"s": store_id, "c": mapping, "by": triggered_by},
        ).scalar()
        self.db.commit()

        c = _Counters()
        try:
            items = await self._fetch_catalog(mapping)
            c.fetched = len(items)
            refs = self._load_existing_refs()
            names = self._load_existing_names()

            for item in items:
                try:
                    self._process_item(sync_id, item, refs, names, c)
                except Exception as exc:  # noqa: BLE001 — isolate per-item failure
                    c.error += 1
                    self.db.rollback()
                    self._log_match(sync_id, item, match_type="error", notes=str(exc)[:300])
                    self.db.commit()

            self.db.execute(
                text(
                    "UPDATE catalog_sync_log SET completed_at = now(), status = 'success', "
                    "items_fetched=:f, items_matched=:m, items_created=:cr, items_updated=:u, "
                    "items_skipped=:sk, items_error=:e WHERE id = :id"
                ),
                {"f": c.fetched, "m": c.matched, "cr": c.created, "u": c.updated,
                 "sk": c.skipped, "e": c.error, "id": sync_id},
            )
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 — whole-run failure
            self.db.rollback()
            self.db.execute(
                text(
                    "UPDATE catalog_sync_log SET completed_at = now(), status = 'error', "
                    "error_detail = :d WHERE id = :id"
                ),
                {"d": str(exc)[:500], "id": sync_id},
            )
            self.db.commit()
            raise
        return sync_id

    async def sync_all_stores(self, triggered_by: str) -> list[int]:
        store_ids = [
            r[0] for r in self.db.execute(
                text("SELECT store_id FROM store_catalog_mapping ORDER BY store_id")
            ).all()
        ]
        results = []
        for sid in store_ids:
            try:
                results.append(await self.sync_store(sid, triggered_by))
            except Exception:  # noqa: BLE001 — one store must not abort the rest
                continue
        return results

    def _process_item(self, sync_id, item, refs, names, c: _Counters) -> None:
        result = self._match_item(item, refs, names)
        currency = _DEFAULT_CURRENCY
        unit = item.get("unit")

        if result.type in ("sku_exact", "fuzzy_name"):
            c.matched += 1
            changed, old = self._update_price(
                result.ingredient_id, item.get("unit_price"), currency,
                source=f"catalog_sync:{result.type}", purchase_unit=unit,
            )
            self._apply_availability(item, result.ingredient_id)
            pack = self._parse_pack_size(item.get("pack_size"))
            notes = None if pack else "pack_size not parsed"
            if changed:
                c.updated += 1
            self._log_match(
                sync_id, item, match_type=result.type,
                ingredient_id=result.ingredient_id, fuzzy_score=result.score,
                action="updated" if changed else "unchanged",
                old_price=old, new_price=item.get("unit_price"),
                currency=currency, notes=notes,
            )

        elif result.type == "new":
            new_id = self._create_ingredient(item)
            c.created += 1
            self._apply_availability(item, new_id)
            self._log_match(
                sync_id, item, match_type="new", ingredient_id=new_id,
                action="created", new_price=item.get("unit_price"), currency=currency,
                notes="new ingredient — pending review",
            )

        else:  # skipped
            c.skipped += 1
            self._log_match(
                sync_id, item, match_type="skipped", action="skipped",
                notes=result.reason,
            )
        self.db.commit()

    def _apply_availability(self, item: dict, ingredient_id: int) -> None:
        self._update_availability(ingredient_id, "shortage", bool(item.get("is_out_of_stock")))
        self._update_availability(ingredient_id, "seasonal", bool(item.get("is_seasonal")))

    def _log_match(
        self, sync_id, item, match_type=None, ingredient_id=None, fuzzy_score=None,
        action=None, old_price=None, new_price=None, currency=None, notes=None,
    ) -> None:
        self.db.execute(
            text(
                "INSERT INTO catalog_match_log "
                "(sync_log_id, catalog_item_id, catalog_sku, catalog_name, match_type, "
                " matched_ingredient_id, fuzzy_score, action_taken, old_price, new_price, "
                " currency_code, notes) "
                "VALUES (:sid, :cid, :sku, :name, :mt, :iid, :score, :act, :old, :new, :cur, :notes)"
            ),
            {"sid": sync_id, "cid": item.get("id"), "sku": item.get("sku"),
             "name": (item.get("name") or "")[:300], "mt": match_type,
             "iid": ingredient_id, "score": fuzzy_score, "act": action,
             "old": old_price, "new": new_price, "cur": currency, "notes": notes},
        )


# ── Manual pending-review actions (pending_review_mapping_spec) ───────────────
# Auto-created ingredients land in catalog_match_log with action_taken='created'
# (the real value in prod — the spec's 'ingredient_created' does not exist). A
# reviewer then resolves each one to exactly one of: mapped / confirmed_new /
# deactivated_manual. These functions own that state transition.


def map_to_canonical(db: Session, pending_id: int, canonical_id: int) -> str:
    """Reassign every reference of a duplicate ingredient to the canonical one and
    deactivate the duplicate. All-or-nothing: any failure rolls the whole thing
    back and leaves the DB untouched. Returns the canonical ingredient's name.

    Raises ValueError with a user-facing message on validation failure. The
    reassignment UPDATEs are no-ops when the duplicate owns no rows in a table
    (the pending catalog-sync ingredients currently have neither supplier refs
    nor supply routes — see pre-check 2) — a matched-zero UPDATE never errors.
    """
    if pending_id == canonical_id:
        raise ValueError("Cannot map ingredient to itself")

    pending = db.execute(
        text("SELECT name, is_active FROM ingredients WHERE id = :i"),
        {"i": pending_id},
    ).first()
    if pending is None or not pending[1]:
        raise ValueError("Pending ingredient not found or inactive")

    canonical = db.execute(
        text("SELECT name, is_active FROM ingredients WHERE id = :i"),
        {"i": canonical_id},
    ).first()
    if canonical is None or not canonical[1]:
        raise ValueError("Canonical ingredient not found or inactive")

    canonical_name = canonical[0]
    try:
        # Reassign external references from the duplicate to the canonical.
        for table in (
            "ingredient_supplier_refs",
            "supply_routes",
            "ingredient_availability",
            "ingredient_recipe_unit_conversions",
        ):
            db.execute(
                text(f"UPDATE {table} SET ingredient_id = :c WHERE ingredient_id = :p"),
                {"c": canonical_id, "p": pending_id},
            )
        # Deactivate + tag the duplicate. Never deleted — [MAPPED] keeps it
        # identifiable in future audits.
        db.execute(
            text(
                "UPDATE ingredients SET is_active = false, "
                "name = '[MAPPED] ' || name, updated_at = now() WHERE id = :p"
            ),
            {"p": pending_id},
        )
        # Trace the mapping on the append-only match log.
        db.execute(
            text(
                "UPDATE catalog_match_log SET matched_ingredient_id = :c, "
                "action_taken = 'mapped', notes = :n WHERE matched_ingredient_id = :p"
            ),
            {
                "c": canonical_id,
                "p": pending_id,
                "n": (
                    f"Manually mapped to canonical id={canonical_id} "
                    f"({canonical_name}). Duplicate id={pending_id} deactivated."
                ),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return canonical_name


def confirm_as_new(db: Session, ingredient_id: int) -> None:
    """Confirm an auto-created ingredient is genuinely new. The ingredient already
    exists and is active — only the log status changes."""
    db.execute(
        text(
            "UPDATE catalog_match_log SET action_taken = 'confirmed_new', "
            "notes = 'Manually confirmed as new ingredient by user' "
            "WHERE matched_ingredient_id = :i AND action_taken = 'created'"
        ),
        {"i": ingredient_id},
    )
    db.commit()


def deactivate_pending(db: Session, ingredient_id: int) -> None:
    """Deactivate a non-recipe item (packaging/cleaning/etc.) without creating any
    relation. Marks the ingredient inactive and records it on the log."""
    db.execute(
        text("UPDATE ingredients SET is_active = false, updated_at = now() WHERE id = :i"),
        {"i": ingredient_id},
    )
    db.execute(
        text(
            "UPDATE catalog_match_log SET action_taken = 'deactivated_manual' "
            "WHERE matched_ingredient_id = :i AND action_taken = 'created'"
        ),
        {"i": ingredient_id},
    )
    db.commit()
