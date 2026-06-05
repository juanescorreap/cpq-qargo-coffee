"""Cost calculation service for CPQ catalog products.

Phase 1 rewrite (ENGINE_SUPPLIER_PLAN_V2 §1): prefetch -> pure compute -> (caller
persists). The numeric contract is UNCHANGED from the previous implementation —
same rounding (round(total, 2) per recipe level), same formulas — so the existing
regression tests stay green. The win is structural:

- ``load_context`` does a handful of bulk queries (no N+1, no per-ingredient
  ``fn_ingredient_unit_cost`` round-trip): ingredient prices are resolved
  set-based in ONE query.
- ``_PureCalculator`` walks the BOM as a memoized DAG (each sub-recipe valued
  once -> O(V+E), killing the old exponential re-expansion).
- ``CostCalculator(db)`` is kept as a thin on-demand facade with the exact same
  public API (``calculate_product_cost`` / ``get_cost_breakdown``) so the 11
  call-sites and the test-suite need no changes. The batch path
  (``PricingEngine.calculate_all_prices``) drives ``_PureCalculator`` directly
  with a single multi-product context and a shared memo.

Price resolution precedence (unchanged):
  with store -> fn_ingredient_unit_cost (local -> route -> catalogue)
  without store -> ingredient.current_price else purchase_price
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text

from backend.database import SessionLocal  # noqa: F401 — exposed for external use
from backend.models import (
    CategoryMargin,  # noqa: F401 — re-exported for callers/tests convenience
    Ingredient,
    IngredientRecipeUnitConversion,
    Product,
    ProductSize,
    RecipeIngredient,
    RecipeSubRecipe,
    RecipeUnit,
    SizePackaging,
    Store,
)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ---------------------------------------------------------------------------
# Immutable prefetch context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _RecipeLine:
    ingredient_id: int
    quantity: Decimal
    recipe_unit_id: Optional[int]
    scales_with_size: bool
    process_yield_loss: Decimal


@dataclass(frozen=True)
class _SubRef:
    sub_id: int
    quantity: Decimal
    scales_with_size: bool


@dataclass(frozen=True)
class _PkgLine:
    ingredient_id: int
    quantity: Decimal


@dataclass(frozen=True)
class _SizeInfo:
    id: int
    size_name: str
    scale_factor: Decimal
    is_default: bool


@dataclass(frozen=True)
class _IngredientInfo:
    id: int
    name: str
    conversion_factor: Optional[Decimal]
    yield_percentage: Optional[Decimal]
    usage_unit: Optional[str]


@dataclass(frozen=True)
class _Subst:
    """Active substitute for an ingredient at a store/date (1 level)."""

    sub_id: int
    ratio: Decimal
    recipe_unit_id: Optional[int]       # unit the ratio is expressed in (None=line's)
    cost_impact_pct: Optional[Decimal]  # informational; real cost uses the price


@dataclass(frozen=True)
class Sourcing:
    """Resolved sourcing for one (ingredient, recipe_unit) at a store/date.

    ``unit_price`` is the purchase-unit price already normalised to the engine's
    accumulation currency (COP). When ``source == 'route'`` and the supplier
    pack->recipe conversion is known, ``purchase_qty``/``recipe_qty`` give the
    SUPPLIER conversion that must override ``ingredient.conversion_factor``.
    """

    unit_price: Decimal
    currency: str                       # original supplier/catalogue currency
    purchase_qty: Optional[Decimal]     # supplier conversion (None => use catalogue)
    recipe_qty: Optional[Decimal]
    source: str                         # 'local' | 'route' | 'catalog'
    supply_route_id: Optional[int] = None
    manufacturer_id: Optional[int] = None
    distributor_id: Optional[int] = None
    price_valid_from: Optional[object] = None


@dataclass(frozen=True)
class CalcContext:
    """Immutable snapshot of everything needed to cost a set of products.

    Holds no DB session: once built it is a pure value, safe to share across
    read-only computation and (later) processes.
    """

    recipe_lines: Dict[int, List[_RecipeLine]]
    sub_recipes: Dict[int, List[_SubRef]]
    sizes: Dict[int, List[_SizeInfo]]
    packaging: Dict[int, List[_PkgLine]]
    ingredients: Dict[int, _IngredientInfo]
    unit_conv: Dict[Tuple[int, int], Decimal]
    # sourcing keyed by (ingredient_id, recipe_unit_id|None): price + provenance
    # + supplier conversion. Packaging uses the (id, None) key.
    sourcing: Dict[Tuple[int, Optional[int]], Sourcing]
    labor: Dict[int, Decimal]               # product_id -> labor cost (unscaled)
    # active substitutes keyed by ORIGINAL ingredient_id (only when store given)
    substitutes: Dict[int, _Subst]
    store_id: Optional[int]
    formula_version: str = "v1"


@dataclass
class BaseCost:
    """Cost of a product at scale=1, split so a size can be applied in O(1)."""

    fixed: Decimal = _ZERO          # lines/subs that do NOT scale + labor
    scalable: Decimal = _ZERO       # lines/subs that DO scale (at scale=1)

    @property
    def at_unit(self) -> Decimal:
        """Total at scale=1 (used as a sub-recipe's unit cost)."""
        return self.fixed + self.scalable


# ---------------------------------------------------------------------------
# Prefetch
# ---------------------------------------------------------------------------

def _bom_closure(db, product_ids: Set[int]) -> Tuple[Set[int], Dict[int, List[_SubRef]]]:
    """Transitive set of products reachable via sub-recipes + the sub-ref map."""
    closure: Set[int] = set(product_ids)
    sub_map: Dict[int, List[_SubRef]] = {}
    frontier: Set[int] = set(product_ids)

    while frontier:
        rows = (
            db.query(RecipeSubRecipe)
            .filter(RecipeSubRecipe.parent_product_id.in_(frontier))
            .all()
        )
        new: Set[int] = set()
        for row in rows:
            sub_map.setdefault(row.parent_product_id, []).append(
                _SubRef(
                    sub_id=row.sub_recipe_id,
                    quantity=Decimal(str(row.quantity)),
                    scales_with_size=bool(row.scales_with_size),
                )
            )
            if row.sub_recipe_id not in closure:
                new.add(row.sub_recipe_id)
        closure |= new
        frontier = new

    return closure, sub_map


def load_context(
    db,
    store_id: Optional[int],
    product_ids: Set[int],
    *,
    formula_version: str = "v1",
) -> CalcContext:
    """Build an immutable :class:`CalcContext` with a handful of bulk queries.

    Args:
        db: Active SQLAlchemy session (used only here, not stored).
        store_id: Store for local price resolution, or ``None`` for base prices.
        product_ids: Top-level products to cost. Sub-recipes are pulled in via
            the BOM closure automatically.
        formula_version: Tag recorded for lineage.
    """
    closure, sub_map = _bom_closure(db, set(product_ids))

    # Recipe lines for every product in the closure.
    recipe_lines: Dict[int, List[_RecipeLine]] = {}
    ri_rows = (
        db.query(RecipeIngredient)
        .filter(RecipeIngredient.product_id.in_(closure))
        .all()
    )
    for ri in ri_rows:
        recipe_lines.setdefault(ri.product_id, []).append(
            _RecipeLine(
                ingredient_id=ri.ingredient_id,
                quantity=Decimal(str(ri.quantity)),
                recipe_unit_id=ri.recipe_unit_id,
                scales_with_size=bool(ri.scales_with_size),
                process_yield_loss=Decimal(str(ri.process_yield_loss or 0)),
            )
        )

    # Active substitutes (only with a store: availability is per region/route).
    # Keyed by ORIGINAL ingredient_id. Set-based: one LATERAL over fn_active_substitute.
    substitutes: Dict[int, _Subst] = {}
    line_ingredient_ids = {
        l.ingredient_id for lines in recipe_lines.values() for l in lines
    }
    if line_ingredient_ids and store_id is not None:
        sub_rows = db.execute(
            text(
                "SELECT k.ingredient_id, s.substitute_ingredient_id, "
                "       s.quantity_ratio, s.recipe_unit_id, s.cost_impact_pct "
                "FROM unnest(CAST(:ids AS bigint[])) AS k(ingredient_id) "
                "CROSS JOIN LATERAL fn_active_substitute("
                "    k.ingredient_id, :s, CURRENT_DATE) AS s"
            ),
            {"ids": list(line_ingredient_ids), "s": store_id},
        ).all()
        for r in sub_rows:
            substitutes[r.ingredient_id] = _Subst(
                sub_id=r.substitute_ingredient_id,
                ratio=Decimal(str(r.quantity_ratio)),
                recipe_unit_id=r.recipe_unit_id,
                cost_impact_pct=Decimal(str(r.cost_impact_pct)) if r.cost_impact_pct is not None else None,
            )

    # Effective (ingredient_id, recipe_unit_id) substitution keys per line, so the
    # substitute's price + conversion get loaded too.
    subst_keys: Set[Tuple[int, Optional[int]]] = set()
    for lines in recipe_lines.values():
        for l in lines:
            sub = substitutes.get(l.ingredient_id)
            if sub is not None:
                eff_unit = sub.recipe_unit_id if sub.recipe_unit_id is not None else l.recipe_unit_id
                subst_keys.add((sub.sub_id, eff_unit))

    # Sizes for the top-level products being priced.
    sizes: Dict[int, List[_SizeInfo]] = {}
    size_rows = (
        db.query(ProductSize)
        .filter(ProductSize.product_id.in_(product_ids))
        .all()
    )
    size_ids: Set[int] = set()
    for s in size_rows:
        size_ids.add(s.id)
        sizes.setdefault(s.product_id, []).append(
            _SizeInfo(
                id=s.id,
                size_name=s.size_name,
                scale_factor=Decimal(str(s.scale_factor if s.scale_factor is not None else 1)),
                is_default=bool(s.is_default),
            )
        )

    # Packaging for those sizes.
    packaging: Dict[int, List[_PkgLine]] = {}
    pkg_rows = (
        db.query(SizePackaging)
        .filter(SizePackaging.size_id.in_(size_ids))
        .all()
        if size_ids
        else []
    )
    for p in pkg_rows:
        packaging.setdefault(p.size_id, []).append(
            _PkgLine(
                ingredient_id=p.packaging_ingredient_id,
                quantity=Decimal(str(p.quantity)),
            )
        )

    # All ingredient ids referenced (recipe + packaging + substitute targets).
    ingredient_ids: Set[int] = {l.ingredient_id for lines in recipe_lines.values() for l in lines}
    ingredient_ids |= {p.ingredient_id for lines in packaging.values() for p in lines}
    ingredient_ids |= {sub_id for (sub_id, _) in subst_keys}

    ingredients: Dict[int, _IngredientInfo] = {}
    price_fallback: Dict[int, Decimal] = {}
    if ingredient_ids:
        for i in db.query(Ingredient).filter(Ingredient.id.in_(ingredient_ids)).all():
            ingredients[i.id] = _IngredientInfo(
                id=i.id,
                name=i.name,
                conversion_factor=Decimal(str(i.conversion_factor)) if i.conversion_factor is not None else None,
                yield_percentage=Decimal(str(i.yield_percentage)) if i.yield_percentage is not None else None,
                usage_unit=i.usage_unit,
            )
            base = i.current_price if i.current_price is not None else i.purchase_price
            price_fallback[i.id] = Decimal(str(base)) if base is not None else _ZERO

    # Recipe-unit conversions for the (ingredient, recipe_unit) pairs that appear.
    unit_conv: Dict[Tuple[int, int], Decimal] = {}
    conv_pairs = {
        (l.ingredient_id, l.recipe_unit_id)
        for lines in recipe_lines.values()
        for l in lines
        if l.recipe_unit_id is not None
    }
    conv_pairs |= {(sub_id, ru) for (sub_id, ru) in subst_keys if ru is not None}
    if conv_pairs:
        ru_ids = {ru for (_, ru) in conv_pairs}
        ing_ids = {ing for (ing, _) in conv_pairs}
        for c in (
            db.query(IngredientRecipeUnitConversion)
            .filter(
                IngredientRecipeUnitConversion.ingredient_id.in_(ing_ids),
                IngredientRecipeUnitConversion.recipe_unit_id.in_(ru_ids),
            )
            .all()
        ):
            unit_conv[(c.ingredient_id, c.recipe_unit_id)] = Decimal(str(c.usage_unit_quantity))

    # Sourcing map: set-based resolution over (ingredient, recipe_unit) pairs.
    # Each entry carries price (normalised to COP), provenance and the supplier
    # pack->recipe conversion. No per-ingredient round-trip.
    #
    # Keys needed: every (ingredient_id, recipe_unit_id) used by a recipe line,
    # plus (packaging_ingredient_id, None).
    needed_keys: Set[Tuple[int, Optional[int]]] = {
        (l.ingredient_id, l.recipe_unit_id)
        for lines in recipe_lines.values()
        for l in lines
    }
    needed_keys |= {
        (p.ingredient_id, None)
        for lines in packaging.values()
        for p in lines
    }
    needed_keys |= subst_keys

    sourcing: Dict[Tuple[int, Optional[int]], Sourcing] = {}
    if needed_keys and store_id is not None:
        ings = [k[0] for k in needed_keys]
        rus = [k[1] for k in needed_keys]
        rows = db.execute(
            text(
                "SELECT k.ingredient_id, k.recipe_unit_id, "
                "       fn_convert_amount(s.unit_price, s.price_currency, 'COP', "
                "           COALESCE(s.price_valid_from, CURRENT_DATE)) AS unit_price_cop, "
                "       s.price_currency, s.purchase_qty, s.recipe_qty, s.source, "
                "       s.supply_route_id, s.manufacturer_id, s.distributor_id, "
                "       s.price_valid_from "
                "FROM unnest(CAST(:ings AS bigint[]), CAST(:rus AS bigint[])) "
                "         AS k(ingredient_id, recipe_unit_id) "
                "CROSS JOIN LATERAL fn_resolve_ingredient_sourcing("
                "    k.ingredient_id, :s, k.recipe_unit_id, CURRENT_DATE) AS s"
            ),
            {"s": store_id, "ings": ings, "rus": rus},
        ).all()
        for r in rows:
            price = (
                Decimal(str(r.unit_price_cop))
                if r.unit_price_cop is not None
                else price_fallback.get(r.ingredient_id, _ZERO)
            )
            sourcing[(r.ingredient_id, r.recipe_unit_id)] = Sourcing(
                unit_price=price,
                currency=r.price_currency or "COP",
                purchase_qty=Decimal(str(r.purchase_qty)) if r.purchase_qty is not None else None,
                recipe_qty=Decimal(str(r.recipe_qty)) if r.recipe_qty is not None else None,
                source=r.source or "catalog",
                supply_route_id=r.supply_route_id,
                manufacturer_id=r.manufacturer_id,
                distributor_id=r.distributor_id,
                price_valid_from=r.price_valid_from,
            )
    else:
        # No store -> catalogue price, no routing (mirrors legacy base-price path).
        for (ing_id, ru_id) in needed_keys:
            sourcing[(ing_id, ru_id)] = Sourcing(
                unit_price=price_fallback.get(ing_id, _ZERO),
                currency="COP",
                purchase_qty=None,
                recipe_qty=None,
                source="catalog",
            )

    # Labor per product (unscaled), reusing the loaded Product rows.
    labor: Dict[int, Decimal] = {}
    for prod in db.query(Product).filter(Product.id.in_(closure)).all():
        if prod.prep_time_minutes and prod.labor_cost_per_minute:
            labor[prod.id] = Decimal(str(prod.prep_time_minutes)) * Decimal(str(prod.labor_cost_per_minute))

    return CalcContext(
        recipe_lines=recipe_lines,
        sub_recipes=sub_map,
        sizes=sizes,
        packaging=packaging,
        ingredients=ingredients,
        unit_conv=unit_conv,
        sourcing=sourcing,
        labor=labor,
        substitutes=substitutes,
        store_id=store_id,
        formula_version=formula_version,
    )


# ---------------------------------------------------------------------------
# Pure compute (memoized DAG)
# ---------------------------------------------------------------------------

class CycleError(RuntimeError):
    """Raised when the BOM contains a cycle (defence; the DB also forbids it)."""


class _PureCalculator:
    """Pure cost engine over a :class:`CalcContext`. No DB session.

    Reused by both the on-demand facade and the batch driver. Numbers match the
    legacy engine exactly, including per-recipe-level rounding to 2 decimals.
    """

    def __init__(self, ctx: CalcContext) -> None:
        self.ctx = ctx

    def _denominator(self, ing: _IngredientInfo, src: Sourcing) -> Optional[Decimal]:
        """Units-per-purchase-unit. SUPPLIER conversion wins when the price comes
        from a route and the supplier pack->recipe conversion is known; otherwise
        the catalogue ``conversion_factor`` (Phase-2 / V2 §6 decision)."""
        if src.source == "route" and src.purchase_qty and src.recipe_qty:
            return src.recipe_qty / src.purchase_qty
        return ing.conversion_factor

    def _cost_of(
        self,
        ingredient_id: int,
        recipe_unit_id: Optional[int],
        quantity: Decimal,
        process_yield_loss: Decimal,
    ) -> Decimal:
        """Cost of ``quantity`` of an ingredient at scale=1 (yield/process/recipe
        conversion applied; size scale deferred). Used for both the original and
        the substitute ingredient."""
        ing = self.ctx.ingredients.get(ingredient_id)
        if ing is None:
            return _ZERO

        src = self.ctx.sourcing.get(
            (ingredient_id, recipe_unit_id)
        ) or Sourcing(_ZERO, "COP", None, None, "catalog")

        denom = self._denominator(ing, src)
        if not denom:
            return _ZERO

        qty = quantity
        if recipe_unit_id is not None:
            conv = self.ctx.unit_conv.get((ingredient_id, recipe_unit_id))
            if conv is None:
                # Mirror the legacy on-demand behaviour: a missing recipe-unit
                # conversion is a data error that must surface, not silently 0.
                raise ValueError(
                    f"Missing conversion for ingredient {ingredient_id} "
                    f"in recipe unit {recipe_unit_id}."
                )
            qty = qty * conv

        if ing.yield_percentage and ing.yield_percentage > 0:
            qty = qty / ing.yield_percentage

        if _ZERO < process_yield_loss < _HUNDRED:
            qty = qty / (process_yield_loss / _HUNDRED)

        return (src.unit_price / denom) * qty

    # -- line cost at scale=1 (substitute swap applied; scale deferred) --
    def _line_base_cost(self, line: _RecipeLine) -> Decimal:
        sub = self.ctx.substitutes.get(line.ingredient_id)
        if sub is not None:
            # Cost the SUBSTITUTE with quantity_ratio applied (1 level). Effective
            # unit = the substitute's declared unit, else the line's.
            eff_unit = sub.recipe_unit_id if sub.recipe_unit_id is not None else line.recipe_unit_id
            return self._cost_of(
                sub.sub_id, eff_unit, line.quantity * sub.ratio, line.process_yield_loss
            )
        return self._cost_of(
            line.ingredient_id, line.recipe_unit_id, line.quantity, line.process_yield_loss
        )

    def base_recipe_cost(
        self,
        product_id: int,
        memo: Dict[int, BaseCost],
        visiting: Set[int],
    ) -> BaseCost:
        """Memoized base cost (scale=1) of a product. O(V+E) over the BOM DAG."""
        cached = memo.get(product_id)
        if cached is not None:
            return cached
        if product_id in visiting:
            raise CycleError(f"Cycle detected at product {product_id}")
        visiting.add(product_id)

        fixed = _ZERO
        scalable = _ZERO

        for line in self.ctx.recipe_lines.get(product_id, []):
            cost = self._line_base_cost(line)
            if line.scales_with_size:
                scalable += cost
            else:
                fixed += cost

        for sub in self.ctx.sub_recipes.get(product_id, []):
            sub_base = self.base_recipe_cost(sub.sub_id, memo, visiting)
            # Legacy parity: each sub-recipe's unit cost is rounded to 2 decimals
            # (every recursive calculate_product_cost return was rounded) before
            # being multiplied by the consumed quantity.
            unit = round(sub_base.at_unit, 2)
            contribution = unit * sub.quantity
            if sub.scales_with_size:
                scalable += contribution
            else:
                fixed += contribution

        fixed += self.ctx.labor.get(product_id, _ZERO)

        visiting.discard(product_id)
        result = BaseCost(fixed=fixed, scalable=scalable)
        memo[product_id] = result
        return result

    def packaging_cost(self, size_id: Optional[int]) -> Decimal:
        if size_id is None:
            return _ZERO
        total = _ZERO
        for pkg in self.ctx.packaging.get(size_id, []):
            ing = self.ctx.ingredients.get(pkg.ingredient_id)
            if ing is None:
                continue
            src = self.ctx.sourcing.get(
                (pkg.ingredient_id, None)
            ) or Sourcing(_ZERO, "COP", None, None, "catalog")
            denom = self._denominator(ing, src)
            if not denom:
                continue
            unit_cost = src.unit_price / denom
            total += unit_cost * pkg.quantity
        return total

    def total_for_size(
        self,
        base: BaseCost,
        scale: Decimal,
        size_id: Optional[int],
    ) -> Decimal:
        """Final cost for a product+size. Single rounding at the end (legacy)."""
        raw = base.fixed + base.scalable * scale + self.packaging_cost(size_id)
        return round(raw, 2)


# ---------------------------------------------------------------------------
# On-demand facade (unchanged public API)
# ---------------------------------------------------------------------------

class CostCalculator:
    """On-demand facade: same API as before, pure engine underneath.

    Builds a single-product context per call (kills the old N+1 / exponential
    recursion for that product) and delegates to :class:`_PureCalculator`. The
    batch path uses :func:`load_context` + :class:`_PureCalculator` directly with
    a shared context and memo.
    """

    def __init__(self, db_session) -> None:
        self.db = db_session

    # -- helpers --------------------------------------------------------------
    def _resolve_size(self, ctx: CalcContext, product_id: int, size_id: Optional[int]):
        product_sizes = ctx.sizes.get(product_id, [])
        if size_id is not None:
            for s in product_sizes:
                if s.id == size_id:
                    return s
            # size_id may belong to a product not pre-loaded as top-level; fetch.
            s = self.db.query(ProductSize).filter(ProductSize.id == size_id).first()
            if not s:
                raise ValueError(f"Size {size_id} not found")
            return _SizeInfo(
                id=s.id,
                size_name=s.size_name,
                scale_factor=Decimal(str(s.scale_factor if s.scale_factor is not None else 1)),
                is_default=bool(s.is_default),
            )
        for s in product_sizes:
            if s.is_default:
                return s
        return None

    # -- public API -----------------------------------------------------------
    def calculate_product_cost(
        self,
        product_id: int,
        size_id: Optional[int] = None,
        store_id: Optional[int] = None,
        _recursion_depth: int = 0,  # kept for signature compatibility; unused
    ) -> Decimal:
        """Total production cost of a product (optionally for a size / store).

        Numeric result is identical to the previous implementation.
        """
        if not self.db.query(Product.id).filter(Product.id == product_id).first():
            raise ValueError(f"Product {product_id} not found")

        ctx = load_context(self.db, store_id, {product_id})
        pure = _PureCalculator(ctx)
        base = pure.base_recipe_cost(product_id, memo={}, visiting=set())

        size = self._resolve_size(ctx, product_id, size_id)
        scale = size.scale_factor if size else Decimal("1.0")
        resolved_size_id = size.id if size else None
        return pure.total_for_size(base, scale, resolved_size_id)

    def get_cost_breakdown(
        self,
        product_id: int,
        size_id: Optional[int] = None,
        store_id: Optional[int] = None,
    ) -> Dict:
        """Detailed, line-by-line cost breakdown (E4: no more empty TODO lists)."""
        product = self.db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise ValueError(f"Product {product_id} not found")

        ctx = load_context(self.db, store_id, {product_id})
        pure = _PureCalculator(ctx)
        memo: Dict[int, BaseCost] = {}
        base = pure.base_recipe_cost(product_id, memo, set())

        size = self._resolve_size(ctx, product_id, size_id)
        scale = size.scale_factor if size else Decimal("1.0")
        resolved_size_id = size.id if size else None

        # Direct ingredients (valued at the selected size), with substitute swap.
        ingredient_lines: List[Dict] = []
        ingredients_total = _ZERO
        for line in ctx.recipe_lines.get(product_id, []):
            sub = ctx.substitutes.get(line.ingredient_id)
            if sub is not None:
                eff_id = sub.sub_id
                eff_unit = sub.recipe_unit_id if sub.recipe_unit_id is not None else line.recipe_unit_id
                base_qty = line.quantity * sub.ratio
            else:
                eff_id = line.ingredient_id
                eff_unit = line.recipe_unit_id
                base_qty = line.quantity

            ing = ctx.ingredients.get(eff_id)
            if ing is None:
                continue
            src = ctx.sourcing.get((eff_id, eff_unit)) \
                or Sourcing(_ZERO, "COP", None, None, "catalog")
            denom = pure._denominator(ing, src)
            if not denom:
                continue
            line_scale = scale if line.scales_with_size else Decimal("1")
            cost = pure._line_base_cost(line) * line_scale
            ingredients_total += cost
            qty = base_qty
            if eff_unit is not None:
                qty = qty * ctx.unit_conv.get((eff_id, eff_unit), _ZERO)
            qty = qty * line_scale
            unit_cost = src.unit_price / denom
            ingredient_lines.append({
                "ingredient_id": eff_id,
                "name": ing.name,
                "quantity": qty,
                "unit": ing.usage_unit,
                "unit_cost": unit_cost,
                "line_cost": cost,
                "price_source": src.source,
                "supply_route_id": src.supply_route_id,
                "manufacturer_id": src.manufacturer_id,
                "distributor_id": src.distributor_id,
                "is_substitute": sub is not None,
                "original_ingredient_id": line.ingredient_id if sub is not None else None,
            })

        # Sub-recipes (unit cost each, legacy per-sub rounding).
        sub_lines: List[Dict] = []
        sub_total = _ZERO
        for sub in ctx.sub_recipes.get(product_id, []):
            unit = round(pure.base_recipe_cost(sub.sub_id, memo, set()).at_unit, 2)
            qty = sub.quantity * (scale if sub.scales_with_size else Decimal("1"))
            line_cost = unit * qty
            sub_total += line_cost
            sub_name = self.db.query(Product.name).filter(Product.id == sub.sub_id).scalar()
            sub_lines.append({
                "sub_product_id": sub.sub_id,
                "name": sub_name,
                "quantity": qty,
                "unit_cost": unit,
                "line_cost": line_cost,
            })

        # Packaging.
        packaging_lines: List[Dict] = []
        packaging_total = _ZERO
        for pkg in ctx.packaging.get(resolved_size_id, []) if resolved_size_id else []:
            ing = ctx.ingredients.get(pkg.ingredient_id)
            if ing is None:
                continue
            src = ctx.sourcing.get((pkg.ingredient_id, None)) \
                or Sourcing(_ZERO, "COP", None, None, "catalog")
            denom = pure._denominator(ing, src)
            if not denom:
                continue
            unit_cost = src.unit_price / denom
            cost = unit_cost * pkg.quantity
            packaging_total += cost
            packaging_lines.append({
                "ingredient_id": ing.id,
                "name": ing.name,
                "quantity": pkg.quantity,
                "unit_cost": unit_cost,
                "line_cost": cost,
                "price_source": src.source,
            })

        labor_total = ctx.labor.get(product_id, _ZERO)
        total_cost = pure.total_for_size(base, scale, resolved_size_id)
        store = self.db.query(Store).filter(Store.id == store_id).first() if store_id else None
        has_substitutes = any(
            l.ingredient_id in ctx.substitutes
            for lines in ctx.recipe_lines.values()
            for l in lines
        )

        return {
            "product_id": product_id,
            "product_name": product.name,
            "size_id": resolved_size_id,
            "size_name": size.size_name if size else None,
            "store_id": store_id,
            "store_name": store.name if store else None,
            "total_cost": total_cost,
            "has_substitutes": has_substitutes,
            "breakdown": {
                "ingredients": ingredient_lines,
                "sub_recipes": sub_lines,
                "packaging": packaging_lines,
                "labor": {
                    "minutes": product.prep_time_minutes or _ZERO,
                    "cost_per_minute": product.labor_cost_per_minute or _ZERO,
                    "cost": labor_total,
                },
            },
            "totals": {
                "ingredients": ingredients_total,
                "sub_recipes": sub_total,
                "packaging": packaging_total,
                "labor": labor_total,
            },
        }
