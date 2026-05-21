"""Analysis report generator for the Qargo Coffee CPQ.

Responsibilities:
- Product costs with a breakdown by component (ingredients, sub-recipes,
  packaging, labor) for each active size.
- Margin analysis: classifies pricings as negative, low, healthy, and high.
- Price benchmark of own prices versus competitors with established matches.
- Simulation of the cost impact of a percentage change in an ingredient.
"""

import logging
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models import (
    Competitor,
    CompetitorProduct,
    Ingredient,
    Product,
    ProductCompetitorMatch,
    ProductPricing,
    ProductSize,
    RecipeIngredient,
)
from backend.services.cost_calculator import CostCalculator
from backend.services.pricing_engine import PricingEngine

logger = logging.getLogger("report_generator")


class ReportGenerator:
    """Generator for cost, margin, and competitor analysis reports.

    Encapsulates the logic for four report types:

    1. **Product costs**: total cost + breakdown by component for each active
       size, with support for local prices per store.
    2. **Margin analysis**: classifies all current pricings into four ranges
       (negative, low < 30 %, healthy 30–80 %, high > 80 %).
    3. **Competitor benchmark**: price difference for each match established
       in ``ProductCompetitorMatch``.
    4. **Impact simulation**: projects how a percentage change in the price of
       an ingredient affects the cost of products that use it, without writing
       data to the DB.

    All methods capture per-item errors so that incomplete data does not
    interrupt the rest of the report.

    Attributes:
        db: Active SQLAlchemy session.  The caller is responsible for its
            lifecycle (commit / rollback / close).
        cost_calculator: Instance of :class:`~backend.services.cost_calculator.CostCalculator`
            built with the same session.
        pricing_engine: Instance of :class:`~backend.services.pricing_engine.PricingEngine`
            built with the same session.
    """

    def __init__(self, db: Session) -> None:
        """Initialise the generator with a database session.

        Args:
            db: Active SQLAlchemy session (e.g. ``next(get_db())`` in FastAPI).
        """
        self.db = db
        self.cost_calculator = CostCalculator(db)
        self.pricing_engine = PricingEngine(db)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def product_costs_report(
        self,
        store_id: Optional[int] = None,
    ) -> List[Dict]:
        """Report of product costs with a breakdown by component.

        Iterates over all active products and calculates the production cost
        for each of their sizes.  Products or sizes that generate an error
        (ingredient without price, empty recipe, etc.) are skipped and logged
        as WARNING without interrupting the batch.

        Args:
            store_id: PK of the store to use local ingredient prices
                (``StoreIngredientPrice``).  ``None`` uses the global base
                price of each ingredient.

        Returns:
            List sorted by product name::

                [
                    {
                        'product_id':   int,
                        'product_name': str,
                        'category':     str | None,
                        'sizes': [
                            {
                                'size_name': str,
                                'cost':      Decimal,
                                'cost_breakdown': {
                                    'ingredients': Decimal,
                                    'sub_recipes': Decimal,
                                    'packaging':   Decimal,
                                    'labor':       Decimal,
                                }
                            },
                            ...
                        ]
                    },
                    ...
                ]
        """
        products = (
            self.db.query(Product)
            .filter(Product.is_active == True)
            .order_by(Product.name)
            .all()
        )

        report: List[Dict] = []

        for product in products:
            sizes = (
                self.db.query(ProductSize)
                .filter(ProductSize.product_id == product.id)
                .order_by(ProductSize.scale_factor)
                .all()
            )

            sizes_data: List[Dict] = []
            for size in sizes:
                try:
                    cost = self.cost_calculator.calculate_product_cost(
                        product.id, size.id, store_id
                    )
                    breakdown = self.cost_calculator.get_cost_breakdown(
                        product.id, size.id, store_id
                    )
                    sizes_data.append({
                        "size_name":      size.size_name,
                        "cost":           cost,
                        "cost_breakdown": breakdown.get("totals", {}),
                    })
                except Exception as exc:
                    logger.warning(
                        "product_costs_report: skipping %s (%s) — %s",
                        product.name, size.size_name, exc,
                    )

            report.append({
                "product_id":   product.id,
                "product_name": product.name,
                "category":     product.category,
                "sizes":        sizes_data,
            })

        return report

    def margin_analysis_report(self) -> Dict:
        """Report of margin analysis over all current pricings.

        Classifies each ``ProductPricing`` record by the gross margin
        calculated as ``(price − cost) / price × 100``:

        - **negative_margin**: margin < 0 % (price below cost).
        - **low_margin**: 0 % ≤ margin < 30 %.
        - **healthy_margin**: 30 % ≤ margin ≤ 80 %.
        - **high_margin**: margin > 80 %.

        Records with null, zero, or negative ``final_price`` or
        ``calculated_cost`` are skipped.  Errors when resolving product/size
        in the DB are captured and logged as WARNING.

        Returns:
            Dictionary with four lists.  Each item has the structure::

                {
                    'product_name': str,
                    'size_name':    str,
                    'cost':         float,
                    'price':        float,
                    'margin_pct':   float,
                }

            ``negative_margin`` and ``low_margin`` are sorted ascending by
            margin; ``high_margin`` descending.
        """
        all_pricing = self.db.query(ProductPricing).all()

        negative: List[Dict] = []
        low:      List[Dict] = []
        healthy:  List[Dict] = []
        high:     List[Dict] = []

        for pricing in all_pricing:
            if not pricing.final_price or not pricing.calculated_cost:
                continue
            if float(pricing.final_price) <= 0 or float(pricing.calculated_cost) <= 0:
                continue

            try:
                price = float(pricing.final_price)
                cost  = float(pricing.calculated_cost)
                margin_pct = (price - cost) / price * 100

                product = self.db.get(Product, pricing.product_id)
                size    = self.db.get(ProductSize, pricing.size_id)

                if not product or not size:
                    logger.warning(
                        "margin_analysis_report: missing product/size for pricing %d",
                        pricing.id,
                    )
                    continue

                item = {
                    "product_name": product.name,
                    "size_name":    size.size_name,
                    "cost":         cost,
                    "price":        price,
                    "margin_pct":   round(margin_pct, 2),
                }

                if margin_pct < 0:
                    negative.append(item)
                elif margin_pct < 30:
                    low.append(item)
                elif margin_pct <= 80:
                    healthy.append(item)
                else:
                    high.append(item)

            except Exception as exc:
                logger.warning(
                    "margin_analysis_report: error on pricing %d — %s",
                    pricing.id, exc,
                )

        return {
            "negative_margin": sorted(negative, key=lambda x: x["margin_pct"]),
            "low_margin":      sorted(low,      key=lambda x: x["margin_pct"]),
            "healthy_margin":  healthy,
            "high_margin":     sorted(high, key=lambda x: x["margin_pct"], reverse=True),
        }

    def competitor_benchmark_report(self) -> List[Dict]:
        """Report comparing our prices against competitors.

        Only includes products for which an explicit match exists in
        ``ProductCompetitorMatch`` *and* a current price exists in
        ``ProductPricing`` with ``store_id IS NULL`` (global price).  If a
        match has no associated pricing it is silently skipped.

        Errors when resolving related entities (product, size, competitor)
        are captured and logged as WARNING.

        Returns:
            List sorted by price difference percentage descending
            (those most expensive relative to competition appear first)::

                [
                    {
                        'our_product':          str,
                        'our_size':             str,
                        'our_price':            float,
                        'competitor':           str,
                        'competitor_product':   str,
                        'competitor_price':     float,
                        'price_difference':     float,   # ours − competitor (COP)
                        'price_difference_pct': float,   # difference / competitor × 100
                    },
                    ...
                ]
        """
        matches = self.db.query(ProductCompetitorMatch).all()
        report: List[Dict] = []

        for match in matches:
            try:
                our_product  = self.db.get(Product, match.our_product_id)
                our_size     = self.db.get(ProductSize, match.our_size_id)
                comp_product = self.db.get(CompetitorProduct, match.competitor_product_id)

                if not our_product or not our_size or not comp_product:
                    logger.warning(
                        "competitor_benchmark_report: missing entity for match %d",
                        match.id,
                    )
                    continue

                competitor = self.db.get(Competitor, comp_product.competitor_id)
                if not competitor:
                    logger.warning(
                        "competitor_benchmark_report: competitor %d not found (match %d)",
                        comp_product.competitor_id, match.id,
                    )
                    continue

                our_pricing = (
                    self.db.query(ProductPricing)
                    .filter(
                        ProductPricing.product_id == match.our_product_id,
                        ProductPricing.size_id    == match.our_size_id,
                        ProductPricing.store_id.is_(None),
                    )
                    .order_by(ProductPricing.effective_date.desc())
                    .first()
                )

                if not our_pricing:
                    continue

                if not comp_product.price or float(comp_product.price) <= 0:
                    continue

                our_price  = float(our_pricing.final_price)
                comp_price = float(comp_product.price)
                price_diff     = our_price - comp_price
                price_diff_pct = price_diff / comp_price * 100

                report.append({
                    "our_product":          our_product.name,
                    "our_size":             our_size.size_name,
                    "our_price":            our_price,
                    "competitor":           competitor.name,
                    "competitor_product":   comp_product.product_name,
                    "competitor_price":     comp_price,
                    "price_difference":     round(price_diff, 2),
                    "price_difference_pct": round(price_diff_pct, 2),
                })

            except Exception as exc:
                logger.warning(
                    "competitor_benchmark_report: error on match %d — %s",
                    match.id, exc,
                )

        return sorted(report, key=lambda x: x["price_difference_pct"], reverse=True)

    def price_impact_simulation(
        self,
        ingredient_id: int,
        percent_change: Decimal,
    ) -> Dict:
        """Simulate the cost impact of a percentage change in an ingredient price.

        Calculates the current and projected cost of each product × size
        combination that uses the ingredient, **without writing any data to
        the DB**.  The temporary price modification of the ingredient is
        performed inside a ``no_autoflush`` block to ensure SQLAlchemy does
        not propagate the temporary value to the DB server.

        Args:
            ingredient_id: PK of the ingredient whose price is simulated.
            percent_change: Percentage variation.  Positive = increase,
                negative = reduction.  Example: ``Decimal("10")`` → +10 %,
                ``Decimal("-5")`` → −5 %.

        Returns:
            On success, a dictionary with the results::

                {
                    'ingredient':      str,
                    'current_price':   Decimal,
                    'new_price':       Decimal,
                    'percent_change':  Decimal,
                    'affected_products': [
                        {
                            'product':           str,
                            'size':              str,
                            'current_cost':      Decimal,
                            'new_cost':          Decimal,
                            'cost_increase':     Decimal,   # may be negative
                            'cost_increase_pct': Decimal,
                        },
                        ...
                    ]
                }

            The ``affected_products`` list is sorted from highest to lowest
            impact (``cost_increase_pct`` descending).  Products or sizes that
            generate an error are skipped and logged as WARNING.

            On error, returns ``{'error': str}``.

        Raises:
            Does not raise exceptions to the caller; errors are converted into
            the ``'error'`` key of the dict or into skipped entries with WARNING.
        """
        ingredient = self.db.get(Ingredient, ingredient_id)
        if not ingredient:
            return {"error": f"Ingredient {ingredient_id} not found"}

        if not ingredient.purchase_price or float(ingredient.purchase_price) <= 0:
            return {
                "error": (
                    f"Ingredient '{ingredient.name}' has no valid purchase price "
                    "and cannot be used in a simulation"
                )
            }

        current_price = Decimal(str(ingredient.purchase_price))
        new_price = current_price * (Decimal("1") + percent_change / Decimal("100"))

        recipe_ings = (
            self.db.query(RecipeIngredient)
            .filter(RecipeIngredient.ingredient_id == ingredient_id)
            .all()
        )

        if not recipe_ings:
            return {
                "ingredient":        ingredient.name,
                "current_price":     current_price,
                "new_price":         new_price,
                "percent_change":    percent_change,
                "affected_products": [],
            }

        product_ids = {r.product_id for r in recipe_ings}
        affected: List[Dict] = []

        for product_id in product_ids:
            product = self.db.get(Product, product_id)
            if not product:
                logger.warning(
                    "price_impact_simulation: product %d not found, skipping",
                    product_id,
                )
                continue

            sizes = (
                self.db.query(ProductSize)
                .filter(ProductSize.product_id == product_id)
                .order_by(ProductSize.scale_factor)
                .all()
            )

            for size in sizes:
                try:
                    current_cost = self.cost_calculator.calculate_product_cost(
                        product.id, size.id, None
                    )

                    with self.db.no_autoflush:
                        original_price = ingredient.purchase_price
                        ingredient.purchase_price = new_price
                        new_cost = self.cost_calculator.calculate_product_cost(
                            product.id, size.id, None
                        )
                        ingredient.purchase_price = original_price

                    cost_increase = new_cost - current_cost
                    cost_increase_pct = (
                        cost_increase / current_cost * Decimal("100")
                        if current_cost > 0
                        else Decimal("0")
                    )

                    affected.append({
                        "product":           product.name,
                        "size":              size.size_name,
                        "current_cost":      current_cost,
                        "new_cost":          new_cost,
                        "cost_increase":     cost_increase,
                        "cost_increase_pct": round(cost_increase_pct, 4),
                    })

                except Exception as exc:
                    logger.warning(
                        "price_impact_simulation: error on %s (%s) — %s",
                        product.name, size.size_name, exc,
                    )

        return {
            "ingredient":        ingredient.name,
            "current_price":     current_price,
            "new_price":         new_price,
            "percent_change":    percent_change,
            "affected_products": sorted(
                affected,
                key=lambda x: x["cost_increase_pct"],
                reverse=True,
            ),
        }
