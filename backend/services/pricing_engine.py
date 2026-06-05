"""Price calculation and persistence engine for the Qargo Coffee CPQ.

Responsibilities:
- Calculate the suggested price from the production cost + markup.
- Resolve the markup according to hierarchy: explicit override → override saved
  in ``ProductPricing`` → ``CategoryMargin`` of the category → default 50 %.
- Persist prices in ``ProductPricing`` with automatic auditing in
  ``ProductPriceHistory`` when the price changes.
- Recalculate all active products in bulk (batch operation).
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models import (
    CategoryMargin,
    Product,
    ProductPriceHistory,
    ProductPricing,
    ProductSize,
)
from backend.services.cost_calculator import (
    BaseCost,
    CostCalculator,
    _PureCalculator,
    load_context,
)

logger = logging.getLogger("pricing_engine")

_DEFAULT_MARKUP = Decimal("50.0")


class PricingEngine:
    """Price calculation engine with margins for coffee-shop products.

    Encapsulates the logic for:
    - Markup resolution according to the priority hierarchy.
    - Suggested price calculation rounded to the nearest 100 COP.
    - Upsert persistence in ``ProductPricing`` with automatic history.
    - Batch recalculation of all active products.

    Instances are stateless with respect to calculated results and can be
    reused across multiple calls within the same DB session.

    Attributes:
        db: Active SQLAlchemy session.  The caller is responsible for its
            lifecycle (commit / rollback / close).
        cost_calculator: Instance of :class:`~backend.services.cost_calculator.CostCalculator`
            built internally with the same session.
    """

    def __init__(self, db: Session) -> None:
        """Initialise the engine with a database session.

        Args:
            db: Active SQLAlchemy session (e.g. ``next(get_db())`` in FastAPI,
                or directly ``SessionLocal()`` in scripts and batch tasks).
        """
        self.db = db
        self.cost_calculator = CostCalculator(db)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_price(
        self,
        product_id: int,
        size_id: int,
        store_id: Optional[int] = None,
        markup_override: Optional[Decimal] = None,
    ) -> Dict:
        """Calculate the suggested price of a product based on cost + markup.

        Markup hierarchy (highest to lowest priority):

        1. ``markup_override`` passed explicitly to this method.
        2. ``ProductPricing.markup_override`` from the most recent active
           record in the DB for the (product, size, store) combination.
        3. ``CategoryMargin.markup_percentage`` of the product's category.
        4. Global default of 50 %.

        The rounded price approximates the suggested price to the nearest
        multiple of 100 COP, which is the standard pricing convention in
        Colombia.

        Args:
            product_id: PK of the product in the ``products`` table.
            size_id: PK of the size (``ProductSize``) to calculate.
            store_id: PK of the store.  When specified, the production cost
                uses that store's local prices via :class:`CostCalculator`.
                ``None`` uses global base prices.
            markup_override: Markup percentage to apply instead of the
                automatically resolved one (e.g. ``Decimal("65.0")`` → 65 %).
                Has the highest priority in the hierarchy.

        Returns:
            Dictionary with all intermediate and final values::

                {
                    'product_id':        int,
                    'size_id':           int,
                    'store_id':          int | None,
                    'cost':              Decimal,   # production cost
                    'markup_percentage': Decimal,   # applied markup (%)
                    'suggested_price':   Decimal,   # cost × (1 + markup/100)
                    'rounded_price':     Decimal,   # rounded to 100 COP
                }

        Raises:
            ValueError: Propagated from :class:`CostCalculator` if the product
                or size do not exist, or if an ingredient has no price.
        """
        cost = self.cost_calculator.calculate_product_cost(
            product_id, size_id, store_id
        )

        markup = self._resolve_markup(product_id, size_id, store_id, markup_override)

        suggested_price = cost * (Decimal("1") + markup / Decimal("100"))
        rounded_price = Decimal(round(suggested_price / Decimal("100")) * 100)

        return {
            "product_id": product_id,
            "size_id": size_id,
            "store_id": store_id,
            "cost": cost,
            "markup_percentage": markup,
            "suggested_price": suggested_price,
            "rounded_price": rounded_price,
        }

    def save_pricing(
        self,
        product_id: int,
        size_id: int,
        store_id: Optional[int],
        final_price: Decimal,
        markup_override: Optional[Decimal] = None,
        is_manual: bool = False,
        cost: Optional[Decimal] = None,
    ) -> ProductPricing:
        """Save or update the price of a product in the database.

        Performs an upsert on ``ProductPricing`` using the combination
        (product_id, size_id, store_id, effective_date=today) as the natural
        key.  If the record already exists, it is updated; otherwise it is
        created.

        Each time the final price changes relative to the previous record, a
        row is automatically inserted in ``ProductPriceHistory`` to allow
        profitability analysis over time.  The markup recorded in the history
        is calculated as follows:

        - ``markup_override`` if provided.
        - Reverse-engineered ``(final_price / cost − 1) × 100`` otherwise,
          useful for recording manual prices while maintaining traceability of
          the effective margin.

        Args:
            product_id: PK of the product.
            size_id: PK of the size.
            store_id: PK of the store or ``None`` for a global price that
                applies to all stores without a specific price.
            final_price: Final price to persist (COP).
            markup_override: Explicit markup percentage to save in the record.
                If ``None``, the ``markup_override`` field of the model is left
                ``NULL`` and the history markup is inferred.
            is_manual: ``True`` when the price was set manually without
                following the markup formula (e.g. promotional price).

        Returns:
            :class:`~backend.models.ProductPricing` instance created or
            updated, refreshed from the database after commit.

        Raises:
            ValueError: Propagated from :class:`CostCalculator` if the product
                or size do not exist.
            ZeroDivisionError: If the calculated cost is zero and no
                ``markup_override`` is provided (the markup reverse-engineering
                requires dividing by the cost).
        """
        # Batch callers pass a precomputed cost to avoid recomputing (and
        # re-loading a context) per row; on-demand callers omit it.
        if cost is None:
            cost = self.cost_calculator.calculate_product_cost(
                product_id, size_id, store_id
            )

        if markup_override is not None:
            markup_used = markup_override
        elif cost > 0:
            markup_used = ((final_price / cost) - Decimal("1")) * Decimal("100")
        else:
            markup_used = _DEFAULT_MARKUP

        today = date.today()
        # product_pricing holds the CURRENT effective price; uniqueness is
        # (product_id, size_id, COALESCE(store_id, 0), currency_code) — NOT dated.
        # The dated trail lives in product_price_history. So upsert on that key
        # and refresh effective_date, instead of inserting one row per day.
        currency_code = "COP"
        existing = (
            self.db.query(ProductPricing)
            .filter(
                ProductPricing.product_id == product_id,
                ProductPricing.size_id == size_id,
                ProductPricing.store_id == store_id,
                ProductPricing.currency_code == currency_code,
            )
            .first()
        )

        if existing:
            old_price = existing.final_price
            existing.calculated_cost = cost
            existing.markup_override = markup_override
            existing.final_price = final_price
            existing.is_manual_price = is_manual
            existing.effective_date = today
        else:
            existing = ProductPricing(
                product_id=product_id,
                size_id=size_id,
                store_id=store_id,
                calculated_cost=cost,
                markup_override=markup_override,
                final_price=final_price,
                is_manual_price=is_manual,
                effective_date=today,
                currency_code=currency_code,
            )
            self.db.add(existing)
            old_price = None

        if old_price is None or old_price != final_price:
            history = ProductPriceHistory(
                product_id=product_id,
                size_id=size_id,
                store_id=store_id,
                cost=cost,
                price=final_price,
                markup_used=markup_used,
            )
            self.db.add(history)

        try:
            self.db.commit()
            self.db.refresh(existing)
        except Exception:
            self.db.rollback()
            raise
        return existing

    def calculate_all_prices(
        self,
        store_id: Optional[int] = None,
        save_to_db: bool = False,
    ) -> Dict:
        """Calculate (and optionally save) prices for all active products.

        Iterates over all ``Product`` records with ``is_active=True`` and their
        associated ``ProductSize`` records.  Errors for individual items are
        captured and accumulated in the result without interrupting the batch,
        so a product with incomplete data does not block the rest.

        Logging emitted during the operation:

        - ``INFO`` at start with the total number of products found.
        - ``DEBUG`` for each product×size combination calculated successfully,
          including cost, markup, and final price.
        - ``WARNING`` for each combination that fails, with the error message.
        - ``INFO`` at finish with a summary of successes and errors.

        This operation can be expensive for large catalogues; it is recommended
        to run it in a background worker or scheduled task outside the HTTP
        request cycle.

        Args:
            store_id: PK of the store for cost calculation with local prices.
                ``None`` uses the global base price of each ingredient.
            save_to_db: If ``True``, persists each calculated price in
                ``ProductPricing`` (and generates history when it changes).
                If ``False``, only calculates without writing — useful for
                previewing the impact of a cost change before confirming it.

        Returns:
            Summary of the batch operation::

                {
                    'total_products':    int,         # active products found
                    'total_sizes':       int,         # product × size combinations
                    'prices_calculated': int,         # successfully calculated
                    'errors':            List[str],   # "<product> (<size>): <reason>"
                }
        """
        products = (
            self.db.query(Product).filter(Product.is_active == True).all()
        )
        product_ids = {p.id for p in products}

        total_sizes = 0
        prices_calculated = 0
        errors: List[str] = []

        logger.info(
            "Batch pricing started — products=%d store_id=%s save=%s",
            len(products),
            store_id,
            save_to_db,
        )

        # Single bulk prefetch + shared memo for the whole batch (no N+1, each
        # sub-recipe valued once across all products).
        ctx = load_context(self.db, store_id, product_ids)
        pure = _PureCalculator(ctx)
        memo: Dict[tuple, BaseCost] = {}
        markups = self._bulk_markups(products, store_id)

        for product in products:
            try:
                base = pure.base_recipe_cost(product.id, memo, set())
            except Exception as exc:  # cycle / data error: skip whole product
                errors.append(f"{product.name}: {exc}")
                logger.warning("  FAIL %s: %s", product.name, exc)
                continue

            for size in ctx.sizes.get(product.id, []):
                total_sizes += 1
                label = f"{product.name} ({size.size_name})"

                try:
                    cost = pure.total_for_size(base, size.scale_factor, size.id)
                    markup = markups.get(
                        (product.id, size.id), _DEFAULT_MARKUP
                    )
                    suggested = cost * (Decimal("1") + markup / Decimal("100"))
                    rounded = Decimal(round(suggested / Decimal("100")) * 100)

                    if save_to_db:
                        self.save_pricing(
                            product.id,
                            size.id,
                            store_id,
                            rounded,
                            is_manual=False,
                            cost=cost,
                        )

                    prices_calculated += 1
                    logger.debug(
                        "  OK %-40s cost=%10s  markup=%5.1f%%  price=%10s",
                        label, cost, markup, rounded,
                    )
                except Exception as exc:
                    error_msg = f"{label}: {exc}"
                    errors.append(error_msg)
                    logger.warning("  FAIL %s", error_msg)

        logger.info(
            "Batch pricing finished — calculated=%d/%d  errors=%d",
            prices_calculated,
            total_sizes,
            len(errors),
        )

        return {
            "total_products": len(products),
            "total_sizes": total_sizes,
            "prices_calculated": prices_calculated,
            "errors": errors,
        }

    def _bulk_markups(
        self, products: List[Product], store_id: Optional[int]
    ) -> Dict[tuple, Decimal]:
        """Resolve markups for all (product, size) up front — kills the N+1 in
        the batch loop. Same hierarchy as :meth:`_resolve_markup` minus the
        explicit override arg: ProductPricing.markup_override -> CategoryMargin
        -> default.
        """
        product_ids = [p.id for p in products]
        category_by_product = {p.id: p.category for p in products}

        # Per-(product,size) override saved in ProductPricing for this store.
        overrides: Dict[tuple, Decimal] = {}
        if product_ids:
            q = (
                self.db.query(ProductPricing)
                .filter(
                    ProductPricing.product_id.in_(product_ids),
                    ProductPricing.store_id == store_id,
                )
                .order_by(ProductPricing.effective_date.asc())
            )
            for row in q:  # ascending -> last write wins (most recent)
                if row.markup_override is not None:
                    overrides[(row.product_id, row.size_id)] = Decimal(
                        str(row.markup_override)
                    )

        # Category margins.
        cat_margins: Dict[str, Decimal] = {
            cm.category: Decimal(str(cm.markup_percentage))
            for cm in self.db.query(CategoryMargin).all()
        }

        # Sizes per product.
        result: Dict[tuple, Decimal] = {}
        size_rows = (
            self.db.query(ProductSize.id, ProductSize.product_id)
            .filter(ProductSize.product_id.in_(product_ids))
            .all()
            if product_ids
            else []
        )
        for size_id, pid in size_rows:
            key = (pid, size_id)
            if key in overrides:
                result[key] = overrides[key]
                continue
            cat = category_by_product.get(pid)
            if cat and cat in cat_margins:
                result[key] = cat_margins[cat]
            else:
                result[key] = _DEFAULT_MARKUP
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_markup(
        self,
        product_id: int,
        size_id: int,
        store_id: Optional[int],
        markup_override: Optional[Decimal],
    ) -> Decimal:
        """Determine the markup to apply according to the priority hierarchy.

        Hierarchy (highest to lowest):

        1. ``markup_override`` argument from the caller.
        2. ``ProductPricing.markup_override`` from the most recent active
           record for (product, size, store).
        3. ``CategoryMargin.markup_percentage`` of the product's category.
        4. Global default ``_DEFAULT_MARKUP`` (50 %).

        Extracted as a private method so that both :meth:`calculate_price` and
        future helpers can reuse it without duplicating logic.

        Args:
            product_id: PK of the product.
            size_id: PK of the size.
            store_id: PK of the store or ``None`` for global.
            markup_override: Explicit override passed by the external caller.

        Returns:
            Markup as a percentage in ``Decimal`` (e.g. ``Decimal("65.0")``).
        """
        if markup_override is not None:
            return markup_override

        existing_pricing = (
            self.db.query(ProductPricing)
            .filter(
                ProductPricing.product_id == product_id,
                ProductPricing.size_id == size_id,
                ProductPricing.store_id == store_id,
            )
            .order_by(ProductPricing.effective_date.desc())
            .first()
        )

        if existing_pricing and existing_pricing.markup_override is not None:
            return Decimal(str(existing_pricing.markup_override))

        product = (
            self.db.query(Product)
            .filter(Product.id == product_id)
            .first()
        )

        if product and product.category:
            category_margin = (
                self.db.query(CategoryMargin)
                .filter(CategoryMargin.category == product.category)
                .first()
            )
            if category_margin:
                return Decimal(str(category_margin.markup_percentage))

        return _DEFAULT_MARKUP
