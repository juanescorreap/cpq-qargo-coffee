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
    unit_price: Dict[int, Decimal]          # ingredient_id -> purchase-unit price
    labor: Dict[int, Decimal]               # product_id -> labor cost (unscaled)
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

    # All ingredient ids referenced (recipe + packaging).
    ingredient_ids: Set[int] = {l.ingredient_id for lines in recipe_lines.values() for l in lines}
    ingredient_ids |= {p.ingredient_id for lines in packaging.values() for p in lines}

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

    # Price map: set-based resolution (no per-ingredient round-trip).
    unit_price: Dict[int, Decimal] = {}
    if ingredient_ids and store_id is not None:
        rows = db.execute(
            text(
                "SELECT i AS ingredient_id, "
                "fn_ingredient_unit_cost(i, :s, CURRENT_DATE) AS price "
                "FROM unnest(CAST(:ids AS bigint[])) AS i"
            ),
            {"s": store_id, "ids": list(ingredient_ids)},
        ).all()
        for r in rows:
            unit_price[r.ingredient_id] = (
                Decimal(str(r.price)) if r.price is not None else price_fallback.get(r.ingredient_id, _ZERO)
            )
    else:
        unit_price = dict(price_fallback)

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
        unit_price=unit_price,
        labor=labor,
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

    # -- line cost at scale=1 (yield/process/conversion applied; scale deferred) --
    def _line_base_cost(self, line: _RecipeLine) -> Decimal:
        ing = self.ctx.ingredients.get(line.ingredient_id)
        if ing is None or not ing.conversion_factor:
            return _ZERO

        qty = line.quantity
        if line.recipe_unit_id is not None:
            conv = self.ctx.unit_conv.get((line.ingredient_id, line.recipe_unit_id))
            if conv is None:
                # Mirror the legacy on-demand behaviour: a missing recipe-unit
                # conversion is a data error that must surface, not silently 0.
                raise ValueError(
                    f"Missing conversion for ingredient {line.ingredient_id} "
                    f"in recipe unit {line.recipe_unit_id}."
                )
            qty = qty * conv

        if ing.yield_percentage and ing.yield_percentage > 0:
            qty = qty / ing.yield_percentage

        if _ZERO < line.process_yield_loss < _HUNDRED:
            qty = qty / (line.process_yield_loss / _HUNDRED)

        unit_cost = self.ctx.unit_price.get(line.ingredient_id, _ZERO) / ing.conversion_factor
        return unit_cost * qty

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
            if ing is None or not ing.conversion_factor:
                continue
            unit_cost = self.ctx.unit_price.get(pkg.ingredient_id, _ZERO) / ing.conversion_factor
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

        price_source = "store" if store_id is not None else "base"

        # Direct ingredients (valued at the selected size).
        ingredient_lines: List[Dict] = []
        ingredients_total = _ZERO
        for line in ctx.recipe_lines.get(product_id, []):
            ing = ctx.ingredients.get(line.ingredient_id)
            if ing is None or not ing.conversion_factor:
                continue
            line_scale = scale if line.scales_with_size else Decimal("1")
            cost = pure._line_base_cost(line) * line_scale
            ingredients_total += cost
            qty = line.quantity
            if line.recipe_unit_id is not None:
                qty = qty * ctx.unit_conv.get((line.ingredient_id, line.recipe_unit_id), _ZERO)
            qty = qty * line_scale
            unit_cost = ctx.unit_price.get(line.ingredient_id, _ZERO) / ing.conversion_factor
            ingredient_lines.append({
                "ingredient_id": ing.id,
                "name": ing.name,
                "quantity": qty,
                "unit": ing.usage_unit,
                "unit_cost": unit_cost,
                "line_cost": cost,
                "price_source": price_source,
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
            if ing is None or not ing.conversion_factor:
                continue
            unit_cost = ctx.unit_price.get(pkg.ingredient_id, _ZERO) / ing.conversion_factor
            cost = unit_cost * pkg.quantity
            packaging_total += cost
            packaging_lines.append({
                "ingredient_id": ing.id,
                "name": ing.name,
                "quantity": pkg.quantity,
                "unit_cost": unit_cost,
                "line_cost": cost,
                "price_source": price_source,
            })

        labor_total = ctx.labor.get(product_id, _ZERO)
        total_cost = pure.total_for_size(base, scale, resolved_size_id)
        store = self.db.query(Store).filter(Store.id == store_id).first() if store_id else None

        return {
            "product_id": product_id,
            "product_name": product.name,
            "size_id": resolved_size_id,
            "size_name": size.size_name if size else None,
            "store_id": store_id,
            "store_name": store.name if store else None,
            "total_cost": total_cost,
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
