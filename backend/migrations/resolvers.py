"""Typed resolver: maps (ingredient_name, manufacturer_name, distributor_name) → ingredient_ref_id.

Returns one of three explicit outcomes so callers act intentionally, not by exception:
  RESOLVED(ingredient_ref_id)        — unique, confident match
  AMBIGUOUS(candidates)              — multiple refs match; human decision needed
  NOT_FOUND(reason)                  — no match, with specific diagnosis of why

Fuzzy threshold is 92 (WRatio). Below threshold → NOT_FOUND with best-candidate note.

Tie-breaking for AMBIGUOUS:
  Call break_tie_if_equivalent(db, candidates) to check whether all candidate refs
  share the same purchase_unit and units_per_pack. If yes (CONFIRMADO_EQUIVALENTE),
  tie-breaker chooses: (1) is_direct=True route first, (2) min ref_id as deterministic
  fallback. If candidates differ → NOT_EQUIVALENT, returned as-is for human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from rapidfuzz import fuzz
from rapidfuzz import process as rf_process

from backend.migrations.preflight_check import _norm

_FUZZY_THRESHOLD = 92


class ResolveStatus(Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"


@dataclass
class ResolveResult:
    status: ResolveStatus
    ingredient_ref_id: Optional[int] = None     # populated when RESOLVED
    candidates: list[int] = field(default_factory=list)  # populated when AMBIGUOUS
    reason: str = ""                             # populated when NOT_FOUND
    fuzzy_note: str = ""                         # non-empty when a fuzzy match was used


def _fuzzy_lookup(query: str, mapping: dict[str, int], label: str) -> tuple[Optional[int], str]:
    """Fuzzy-match *query* against *mapping* keys. Returns (id, note).

    If score >= _FUZZY_THRESHOLD: id is the resolved value, note describes the match.
    Otherwise: id is None, note is the NOT_FOUND reason.
    """
    if not mapping:
        return None, f"{label} '{query}' no existe (tabla vacía)"
    result = rf_process.extractOne(query, list(mapping.keys()), scorer=fuzz.WRatio)
    if result is None:
        return None, f"{label} '{query}' sin candidatos"
    best_match, score, _ = result
    if score >= _FUZZY_THRESHOLD:
        return mapping[best_match], f"fuzzy: '{query}' → '{best_match}' (score {score:.0f})"
    return None, (
        f"{label} '{query}' sin match ≥{_FUZZY_THRESHOLD} — "
        f"mejor candidato: '{best_match}' (score {score:.0f})"
    )


def resolve_supplier_ref(
    maps,
    ingredient_name: Optional[str],
    manufacturer_name: Optional[str],
    distributor_name: Optional[str],
) -> ResolveResult:
    """Resolve CSV name columns → ingredient_ref_id.

    Resolution steps:
      a) ingredient_name → ingredient_id (exact, then fuzzy ≥90)
      b) manufacturer_name + distributor_name → filter supply_routes for that ingredient
         (if both empty: accept any route for the ingredient — no manufacturer/distributor filter)
      c) matching routes → look up ingredient_supplier_refs
         - exactly 1 ref → RESOLVED
         - >1 refs → AMBIGUOUS (list all candidate IDs)
         - 0 refs, routes exist → NOT_FOUND "routes exist but no ref created yet"
         - 0 routes → NOT_FOUND with specific diagnosis
    """
    fuzzy_notes: list[str] = []

    # ── a: ingredient ─────────────────────────────────────────────────────────
    ing_norm = _norm(ingredient_name).lower() if ingredient_name else ""
    if not ing_norm:
        return ResolveResult(status=ResolveStatus.NOT_FOUND, reason="ingredient_name vacío")

    ing_id = maps.ingredient.get(ing_norm)
    if ing_id is None:
        resolved_id, note = _fuzzy_lookup(ing_norm, maps.ingredient, "ingrediente")
        if resolved_id is None:
            return ResolveResult(status=ResolveStatus.NOT_FOUND, reason=note)
        ing_id = resolved_id
        fuzzy_notes.append(note)

    # ── b: manufacturer + distributor ─────────────────────────────────────────
    man = _norm(manufacturer_name).lower() if manufacturer_name else ""
    dist = _norm(distributor_name).lower() if distributor_name else ""

    man_id: Optional[int] = None
    dist_id: Optional[int] = None

    if man:
        man_id = maps.manufacturer.get(man)
        if man_id is None:
            resolved_id, note = _fuzzy_lookup(man, maps.manufacturer, "fabricante")
            if resolved_id is None:
                return ResolveResult(status=ResolveStatus.NOT_FOUND, reason=note)
            man_id = resolved_id
            fuzzy_notes.append(note)

    if dist:
        dist_id = maps.distributor.get(dist)
        if dist_id is None:
            resolved_id, note = _fuzzy_lookup(dist, maps.distributor, "distribuidor")
            if resolved_id is None:
                return ResolveResult(status=ResolveStatus.NOT_FOUND, reason=note)
            dist_id = resolved_id
            fuzzy_notes.append(note)

    # ── b: filter supply_routes ───────────────────────────────────────────────
    matching_route_ids: list[int] = []
    for (r_ing_id, r_man_id, r_dist_id), r_id in maps.route.items():
        if r_ing_id != ing_id:
            continue
        if man_id is not None and r_man_id != man_id:
            continue
        if dist_id is not None and r_dist_id != dist_id:
            continue
        matching_route_ids.append(r_id)

    if not matching_route_ids:
        all_routes = [r_id for (r_ing, _, _), r_id in maps.route.items() if r_ing == ing_id]
        if not all_routes:
            return ResolveResult(
                status=ResolveStatus.NOT_FOUND,
                reason=f"ingrediente '{ingredient_name}' existe pero no tiene supply_routes registradas",
            )
        return ResolveResult(
            status=ResolveStatus.NOT_FOUND,
            reason=(
                f"ingrediente '{ingredient_name}' tiene {len(all_routes)} ruta(s) "
                f"pero ninguna coincide con fabricante='{manufacturer_name or ''}' "
                f"/ distribuidor='{distributor_name or ''}'"
            ),
        )

    # ── c: look up refs ───────────────────────────────────────────────────────
    ref_ids: list[int] = [
        maps.ref[(ing_id, r_id)]
        for r_id in matching_route_ids
        if (ing_id, r_id) in maps.ref
    ]

    if not ref_ids:
        return ResolveResult(
            status=ResolveStatus.NOT_FOUND,
            reason=(
                f"ruta(s) {matching_route_ids} existen para '{ingredient_name}' "
                f"pero sin ingredient_supplier_ref creada aún"
            ),
        )

    if len(ref_ids) == 1:
        return ResolveResult(
            status=ResolveStatus.RESOLVED,
            ingredient_ref_id=ref_ids[0],
            fuzzy_note="; ".join(fuzzy_notes),
        )

    return ResolveResult(
        status=ResolveStatus.AMBIGUOUS,
        candidates=ref_ids,
        fuzzy_note="; ".join(fuzzy_notes),
    )


# ---------------------------------------------------------------------------
# Tie-breaker (call only after resolve_supplier_ref returns AMBIGUOUS)
# ---------------------------------------------------------------------------

class TieResult:
    """Result of break_tie_if_equivalent."""
    __slots__ = ("equivalent", "resolved_ref_id", "tie_rule", "differences")

    def __init__(self, *, equivalent: bool, resolved_ref_id=None, tie_rule="", differences=None):
        self.equivalent = equivalent
        self.resolved_ref_id = resolved_ref_id  # set when equivalent=True
        self.tie_rule = tie_rule                  # human-readable rule used
        self.differences = differences or {}     # set when equivalent=False


_PACK_DIFF_THRESHOLD = 0.50  # auto-resolve only when pack diff < 50%


def _pick_by_priority(rows):
    """Select one ref from candidates using the general priority rule.

    Priority:
      1. is_direct=True AND units_per_pack defined
      2. Most-frequent units_per_pack value (excluding None), min ref_id among ties
      3. Min ref_id final fallback
    """
    from collections import Counter

    direct_with_pack = [r for r in rows if r.is_direct and r.units_per_pack is not None]
    if direct_with_pack:
        return min(direct_with_pack, key=lambda r: r.id)

    pack_counts = Counter(r.units_per_pack for r in rows if r.units_per_pack is not None)
    if pack_counts:
        most_common_pack = pack_counts.most_common(1)[0][0]
        best_group = [r for r in rows if r.units_per_pack == most_common_pack]
        return min(best_group, key=lambda r: r.id)

    return min(rows, key=lambda r: r.id)


def _ref_details(rows) -> list[dict]:
    return [
        {
            "ref_id": r.id,
            "purchase_unit": r.purchase_unit,
            "units_per_pack": str(r.units_per_pack),
            "supply_route_id": r.supply_route_id,
            "is_direct": r.is_direct,
        }
        for r in rows
    ]


def break_tie_if_equivalent(db, candidate_ref_ids: list[int]) -> TieResult:
    """Attempt to auto-resolve AMBIGUOUS candidates.

    Phase 1 — all-same check:
      If all candidates share the same purchase_unit AND units_per_pack → trivially
      equivalent, apply _pick_by_priority and return CONFIRMED.

    Phase 2 — general rule with safety threshold (applied when NOT all same):
      1. Pick candidate via _pick_by_priority (is_direct+pack > majority+pack > min_id).
      2. Safety check: compare chosen.units_per_pack against every alternative that has
         a defined pack value.
         - chosen has None pack AND alternatives have defined values → NOT_EQUIVALENT
           (diff is unknown, conservative).
         - any alternative differs by ≥ _PACK_DIFF_THRESHOLD (50%) → NOT_EQUIVALENT
           (cost impact too large to auto-resolve).
         - all alternatives within 50% (or all None) → return as RESOLVED.

    NOT_EQUIVALENT (equivalent=False) cases go back to the caller as AMBIGUOUS_REAL
    for human review. Only the two cases that exceed the safety threshold (Croissant
    Chocolate and Tiramisu) should reach this state in normal operation.
    """
    from backend.models.supply_chain import IngredientSupplierRef, SupplyRoute

    rows = (
        db.query(
            IngredientSupplierRef.id,
            IngredientSupplierRef.purchase_unit,
            IngredientSupplierRef.units_per_pack,
            IngredientSupplierRef.supply_route_id,
            SupplyRoute.is_direct,
        )
        .join(SupplyRoute, SupplyRoute.id == IngredientSupplierRef.supply_route_id)
        .filter(IngredientSupplierRef.id.in_(candidate_ref_ids))
        .all()
    )

    purchase_units = {r.purchase_unit for r in rows}
    norm_packs = {str(r.units_per_pack) if r.units_per_pack is not None else "None" for r in rows}

    # Phase 1: trivially all-same
    if len(purchase_units) == 1 and len(norm_packs) == 1:
        chosen = _pick_by_priority(rows)
        rule = (
            f"all_equivalent → {'is_direct=True' if chosen.is_direct else 'min ref_id'}"
            f" → ref_id={chosen.id}"
        )
        return TieResult(equivalent=True, resolved_ref_id=chosen.id, tie_rule=rule)

    # Phase 2: not all same — try general rule with safety threshold
    chosen = _pick_by_priority(rows)

    if chosen.units_per_pack is None:
        alts_with_pack = [r for r in rows if r.id != chosen.id and r.units_per_pack is not None]
        if alts_with_pack:
            return TieResult(
                equivalent=False,
                differences={
                    "reason": (
                        f"chosen ref_id={chosen.id} has units_per_pack=None but "
                        f"{len(alts_with_pack)} alternative(s) have defined values — "
                        "diff unknown, manual review required"
                    ),
                    "ref_details": _ref_details(rows),
                },
            )

    # Check diff vs every alternative with a defined pack
    chosen_pack = float(chosen.units_per_pack) if chosen.units_per_pack is not None else None
    for alt in rows:
        if alt.id == chosen.id or alt.units_per_pack is None:
            continue
        alt_pack = float(alt.units_per_pack)
        if chosen_pack is None:
            continue  # chosen_pack=None already handled above
        diff = abs(chosen_pack - alt_pack) / max(chosen_pack, alt_pack)
        if diff >= _PACK_DIFF_THRESHOLD:
            return TieResult(
                equivalent=False,
                differences={
                    "reason": (
                        f"pack diff ≥{_PACK_DIFF_THRESHOLD:.0%}: chosen ref_id={chosen.id} "
                        f"(pack={chosen.units_per_pack}) vs ref_id={alt.id} "
                        f"(pack={alt.units_per_pack}) — diff={diff:.1%}"
                    ),
                    "ref_details": _ref_details(rows),
                },
            )

    # All alternatives within 50% (or have no defined pack) → safe to auto-resolve
    direct_note = "is_direct=True + " if chosen.is_direct else ""
    rule = (
        f"general_rule: {direct_note}pack={chosen.units_per_pack}, "
        f"all alts within {_PACK_DIFF_THRESHOLD:.0%} → ref_id={chosen.id}"
    )
    return TieResult(equivalent=True, resolved_ref_id=chosen.id, tie_rule=rule)
