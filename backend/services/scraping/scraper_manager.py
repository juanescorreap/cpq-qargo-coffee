"""
Main entry point for the scraping system.

ScraperManager is the single public interface the rest of the application
uses.  It orchestrates ConfigLoader → ScraperFactory → concrete scrapers and
is responsible for persisting results to the database.

Responsibilities
----------------
- Create and cache scrapers via ScraperFactory.
- Detect which scraper to use for a given URL.
- Scrape individual ingredient prices and persist them to DB.
- Scrape competitor menus in bulk and persist products to DB.
- Provide batch operations over all configured scrapers.
- Return structured result dicts (never raw Playwright objects).

Database models used
--------------------
- Ingredient / IngredientPriceHistory  (backend.models.ingredient)
- Competitor / CompetitorProduct       (backend.models.competitor)

Usage::

    from backend.database import get_db
    from backend.services.scraping.scraper_manager import ScraperManager

    with next(get_db()) as db:
        manager = ScraperManager(db)

        # List all configured scrapers
        print(manager.list_available_scrapers())

        # Update one ingredient price
        result = manager.scrape_ingredient(ingredient_id=3)
        print(result["new_price"], result["price_change_pct"])

        # Scrape a competitor's full menu
        result = manager.scrape_competitor_menu(competitor_id=1)
        print(result["new_products"], result["updated_products"])
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models import (
    Competitor,
    CompetitorProduct,
    Ingredient,
    IngredientPriceHistory,
)

from .core.exceptions import ConfigurationError, ScraperNotFoundError, ScrapingException
from .core.models import ScrapedProduct, ScraperConfig
from .core.scraper_factory import ScraperFactory
from .utils.config_loader import ConfigLoader


_CONFIG_DIR = Path(__file__).parent / "config"


class ScraperManager:
    """
    Orchestrator for the scraping system.

    All public methods return plain dicts so callers never need to import
    scraping internals.  Every method is safe to call even when no scraper
    is configured for the requested resource — it returns ``success=False``
    with a descriptive ``error`` key instead of raising.

    Examples::

        manager = ScraperManager(db)

        # List scrapers
        for s in manager.list_available_scrapers():
            print(s["id"], s["base_url"])

        # Price update for one ingredient
        r = manager.scrape_ingredient(ingredient_id=5, update_db=True)
        if r["success"]:
            print(f"Price: {r['old_price']} → {r['new_price']}")

        # Competitor menu
        r = manager.scrape_competitor_menu(competitor_id=2)
        print(r["new_products"], "new,", r["updated_products"], "updated")

        # Batch update all ingredients
        r = manager.scrape_all_ingredients()
        print(r["success"], "/", r["total"], "succeeded")
    """

    def __init__(
        self,
        db: Session,
        config_dir: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            db:         Active SQLAlchemy session. The manager never opens or
                        closes sessions; that is the caller's responsibility.
            config_dir: Override for the YAML config directory.  Defaults to
                        ``backend/services/scraping/config/``.
            logger:     Optional logger; a default one is created if omitted.
        """
        self.db = db
        self.logger = logger or self._create_default_logger()

        resolved_dir = config_dir or str(_CONFIG_DIR)
        self.config_loader = ConfigLoader(resolved_dir, logger=self.logger)
        self.factory = ScraperFactory(
            self.config_loader, enable_cache=True, logger=self.logger
        )

        # Cache URL→scraper_id to avoid re-scanning configs on every call.
        self._url_cache: Dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_available_scrapers(self) -> List[Dict[str, Any]]:
        """
        Return metadata for every configured scraper.

        Reads ``registry.yaml`` for display info (name, priority, schedule)
        and falls back to scanning the filesystem when that file is absent or
        does not list the scraper.

        Returns:
            List of dicts, each with keys:

            - ``id``           — scraper_id (YAML filename stem)
            - ``name``         — business_name from config
            - ``type``         — ``'competitor'`` or ``'supplier'``
            - ``scraper_type`` — ``'restaurant'``, ``'retail'``, etc.
            - ``enabled``      — whether the scraper is active
            - ``base_url``
            - ``priority``     — sort key from registry.yaml (default 99)
            - ``schedule``     — cron expression or None
            - ``notes``        — free-text notes from registry.yaml

            Sorted ascending by priority.
        """
        registry = self.config_loader.load_registry()
        registry_scrapers: Dict[str, Dict] = {}

        # Build a flat lookup from registry.yaml (structure: scrapers.competitors / .suppliers).
        for category in ("competitors", "suppliers"):
            for entry in registry.get("scrapers", {}).get(category, []):
                if isinstance(entry, dict) and "id" in entry:
                    registry_scrapers[entry["id"]] = entry

        all_configs = self.config_loader.load_all_configs()
        results: List[Dict[str, Any]] = []

        for scraper_id, config in all_configs.items():
            reg_entry = registry_scrapers.get(scraper_id, {})
            results.append({
                "id": scraper_id,
                "name": config.business_name,
                "type": config.business_type,
                "scraper_type": config.scraper_type,
                "enabled": config.enabled,
                "base_url": config.base_url,
                "priority": reg_entry.get("priority", 99),
                "schedule": reg_entry.get("schedule"),
                "notes": reg_entry.get("notes"),
            })

        return sorted(results, key=lambda x: (x["priority"], x["id"]))

    def get_scraper(self, scraper_id: str):
        """
        Return a configured scraper instance by ID.

        The caller is responsible for the lifecycle (setup / teardown),
        typically via the ``with`` statement.

        Args:
            scraper_id: YAML filename stem (e.g. ``'competitor_001'``).

        Returns:
            Configured :class:`~.core.base_scraper.BaseScraper` instance.

        Raises:
            ScraperNotFoundError: Config file not found or invalid.

        Examples::

            with manager.get_scraper("competitor_001") as s:
                products = s.search_products("cappuccino", limit=20)
        """
        return self.factory.create_scraper(scraper_id)

    def scrape_ingredient(
        self,
        ingredient_id: int,
        update_db: bool = True,
    ) -> Dict[str, Any]:
        """
        Scrape the current price of a single ingredient and optionally persist it.

        Steps:
          1. Load ingredient from DB; return error if not found or has no URL.
          2. Detect which scraper handles the ingredient's ``source_url``.
          3. Scrape the product page.
          4. If ``update_db`` and price changed, append to price history and
             update ``purchase_price`` + ``last_scraped``.

        Args:
            ingredient_id: Primary key of the :class:`~backend.models.Ingredient`.
            update_db:     Persist the new price when True (default).

        Returns:
            Dict with keys:

            - ``success`` (bool)
            - ``ingredient_id``, ``ingredient_name``
            - ``old_price``, ``new_price``, ``price_change``, ``price_change_pct``
            - ``scraper_id``, ``business_name``, ``scraped_at``
            - ``updated_db`` — whether the DB was actually written
            - ``error`` — None on success, message string on failure

        Examples::

            r = manager.scrape_ingredient(5)
            if r["success"]:
                print(f"{r['price_change_pct']:+.1f}% change")
        """
        self.logger.info("Scraping ingredient id=%d", ingredient_id)

        ingredient: Optional[Ingredient] = self.db.get(Ingredient, ingredient_id)
        if ingredient is None:
            return _error(ingredient_id=ingredient_id, error="Ingredient not found")

        if not ingredient.source_url:
            return _error(
                ingredient_id=ingredient_id,
                ingredient_name=ingredient.name,
                error="Ingredient has no source_url",
            )

        scraper_id = self._detect_scraper_by_url(ingredient.source_url)
        if scraper_id is None:
            return _error(
                ingredient_id=ingredient_id,
                ingredient_name=ingredient.name,
                error=f"No scraper configured for URL: {ingredient.source_url}",
            )

        try:
            scraper = self.factory.create_scraper(scraper_id)
        except (ScraperNotFoundError, ConfigurationError) as exc:
            return _error(
                ingredient_id=ingredient_id,
                ingredient_name=ingredient.name,
                scraper_id=scraper_id,
                error=f"Failed to create scraper: {exc.message}",
            )

        try:
            with scraper:
                scraped = scraper.scrape_product(ingredient.source_url)
        except ScrapingException as exc:
            return _error(
                ingredient_id=ingredient_id,
                ingredient_name=ingredient.name,
                scraper_id=scraper_id,
                error=f"Scraping failed [{exc.code}]: {exc.message}",
            )
        except Exception as exc:
            return _error(
                ingredient_id=ingredient_id,
                ingredient_name=ingredient.name,
                scraper_id=scraper_id,
                error=f"Unexpected error: {exc}",
            )

        old_price = Decimal(str(ingredient.purchase_price or 0))
        new_price = scraped.price
        price_change = new_price - old_price
        price_change_pct = float(price_change / old_price * 100) if old_price else 0.0
        db_written = False

        if update_db and new_price > 0:
            if new_price != old_price:
                self.db.add(IngredientPriceHistory(
                    ingredient_id=ingredient_id,
                    price=new_price,
                    source="scraping",
                ))
                ingredient.purchase_price = float(new_price)

            ingredient.last_scraped = datetime.now(timezone.utc)

            try:
                self.db.commit()
                db_written = True
                self.logger.info(
                    "Updated ingredient '%s': %s → %s (%+.1f%%)",
                    ingredient.name, old_price, new_price, price_change_pct,
                )
            except Exception as exc:
                self.db.rollback()
                self.logger.error("DB commit failed for ingredient %d: %s", ingredient_id, exc)
                return _error(
                    ingredient_id=ingredient_id,
                    ingredient_name=ingredient.name,
                    scraper_id=scraper_id,
                    error=f"DB commit failed: {exc}",
                )

        return {
            "success": True,
            "ingredient_id": ingredient_id,
            "ingredient_name": ingredient.name,
            "old_price": old_price,
            "new_price": new_price,
            "price_change": price_change,
            "price_change_pct": price_change_pct,
            "scraper_id": scraper_id,
            "business_name": scraped.business_name,
            "scraped_at": scraped.scraped_at,
            "updated_db": db_written,
            "error": None,
        }

    def scrape_competitor_menu(
        self,
        competitor_id: int,
        search_queries: Optional[List[str]] = None,
        limit_per_query: int = 10,
    ) -> Dict[str, Any]:
        """
        Scrape a competitor's product catalogue and persist to ``competitor_products``.

        Steps:
          1. Load competitor from DB.
          2. Find the scraper matching the competitor's ``website_url`` or name.
          3. Run each search query; collect up to ``limit_per_query`` per query.
          4. Upsert results into ``competitor_products`` (match on name).

        Args:
            competitor_id:    Primary key of :class:`~backend.models.Competitor`.
            search_queries:   Terms to search.  Defaults to a built-in coffee list.
            limit_per_query:  Max products per query.

        Returns:
            Dict with keys:

            - ``success``, ``competitor_id``, ``competitor_name``, ``scraper_id``
            - ``total_products_found``, ``new_products``, ``updated_products``
            - ``errors`` — list of per-product or per-query error messages
            - ``execution_time_ms``

        Examples::

            r = manager.scrape_competitor_menu(1, ["café", "latte"], limit_per_query=20)
            print(r["new_products"], "added")
        """
        started_at = datetime.now()
        self.logger.info("Scraping competitor menu id=%d", competitor_id)

        competitor: Optional[Competitor] = self.db.get(Competitor, competitor_id)
        if competitor is None:
            return _error(competitor_id=competitor_id, error="Competitor not found")

        scraper_id = self._get_scraper_for_competitor(competitor)
        if scraper_id is None:
            return _error(
                competitor_id=competitor_id,
                competitor_name=competitor.name,
                error="No scraper configured for this competitor",
            )

        try:
            scraper = self.factory.create_scraper(scraper_id)
        except (ScraperNotFoundError, ConfigurationError) as exc:
            return _error(
                competitor_id=competitor_id,
                competitor_name=competitor.name,
                error=f"Failed to create scraper: {exc.message}",
            )

        queries = search_queries or self._default_search_queries()
        all_products: List[ScrapedProduct] = []
        query_errors: List[str] = []

        with scraper:
            for query in queries:
                try:
                    batch = scraper.search_products(query, limit=limit_per_query)
                    all_products.extend(batch)
                    self.logger.debug(
                        "[%s] query='%s' → %d products", scraper_id, query, len(batch)
                    )
                except ScrapingException as exc:
                    msg = f"Query '{query}' failed [{exc.code}]: {exc.message}"
                    query_errors.append(msg)
                    self.logger.warning(msg)
                except Exception as exc:
                    msg = f"Query '{query}' unexpected error: {exc}"
                    query_errors.append(msg)
                    self.logger.warning(msg)

        save = self._save_competitor_products(competitor_id, all_products)
        elapsed_ms = (datetime.now() - started_at).total_seconds() * 1_000

        return {
            "success": True,
            "competitor_id": competitor_id,
            "competitor_name": competitor.name,
            "scraper_id": scraper_id,
            "business_name": scraper.business_name,
            "total_products_found": len(all_products),
            "new_products": save["new_count"],
            "updated_products": save["updated_count"],
            "errors": query_errors + save["errors"],
            "execution_time_ms": elapsed_ms,
        }

    def scrape_all_ingredients(
        self,
        supplier_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Scrape prices for every ingredient that has a ``source_url``.

        Args:
            supplier_only: When True, skip ingredients whose scraper is typed
                           as ``'competitor'`` (e.g. prices found on a café menu).

        Returns:
            Dict with keys:

            - ``total``, ``success``, ``failed``, ``skipped``
            - ``results`` — list of individual :meth:`scrape_ingredient` results
            - ``execution_time_ms``

        Examples::

            r = manager.scrape_all_ingredients(supplier_only=True)
            print(r["success"], "/", r["total"])
        """
        started_at = datetime.now()
        self.logger.info("Scraping all ingredients (supplier_only=%s)", supplier_only)

        ingredients: List[Ingredient] = (
            self.db.query(Ingredient)
            .filter(Ingredient.source_url.isnot(None), Ingredient.is_active.is_(True))
            .all()
        )

        totals = {"total": len(ingredients), "success": 0, "failed": 0, "skipped": 0}
        results: List[Dict] = []

        for ing in ingredients:
            if supplier_only:
                sid = self._detect_scraper_by_url(ing.source_url)
                if sid:
                    try:
                        cfg = self.config_loader.load_config(sid)
                        if cfg.business_type != "supplier":
                            totals["skipped"] += 1
                            continue
                    except Exception:
                        pass

            result = self.scrape_ingredient(ing.id, update_db=True)
            results.append(result)
            if result["success"]:
                totals["success"] += 1
            else:
                totals["failed"] += 1

        elapsed_ms = (datetime.now() - started_at).total_seconds() * 1_000
        self.logger.info(
            "Ingredients scraped: %d success, %d failed, %d skipped / %d total",
            totals["success"], totals["failed"], totals["skipped"], totals["total"],
        )

        return {**totals, "results": results, "execution_time_ms": elapsed_ms}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_scraper_by_url(self, url: str) -> Optional[str]:
        """
        Return the scraper_id whose ``base_url`` is a prefix of *url*.

        Results are cached in ``self._url_cache`` so repeated calls for the
        same URL do not re-scan the config directory.

        Args:
            url: Full product or page URL.

        Returns:
            scraper_id string, or None if no match.
        """
        if url in self._url_cache:
            return self._url_cache[url]

        all_configs = self.config_loader.load_all_configs()
        matched: Optional[str] = None

        for scraper_id, config in all_configs.items():
            base = config.base_url.replace("https://", "").replace("http://", "").rstrip("/")
            target = url.replace("https://", "").replace("http://", "")
            if target.startswith(base):
                matched = scraper_id
                break

        if matched:
            self.logger.debug("URL '%s' matched scraper '%s'", url, matched)
        else:
            self.logger.warning("No scraper found for URL: %s", url)

        self._url_cache[url] = matched
        return matched

    def _get_scraper_for_competitor(self, competitor: Competitor) -> Optional[str]:
        """
        Find the scraper configured for *competitor*.

        Match strategy (first match wins):
          1. ``website_url`` domain prefix against ``base_url``.
          2. Case-insensitive substring of ``business_name`` in competitor name
             or vice versa.

        Returns:
            scraper_id or None.
        """
        all_configs = self.config_loader.load_all_configs()

        for scraper_id, config in all_configs.items():
            if config.business_type != "competitor":
                continue

            # URL match.
            if competitor.website_url:
                base = config.base_url.replace("https://", "").replace("http://", "").rstrip("/")
                target = competitor.website_url.replace("https://", "").replace("http://", "")
                if target.startswith(base):
                    self.logger.debug(
                        "Competitor '%s' matched scraper '%s' (URL)", competitor.name, scraper_id
                    )
                    return scraper_id

            # Name match.
            cfg_name = config.business_name.lower()
            cmp_name = competitor.name.lower()
            if cfg_name in cmp_name or cmp_name in cfg_name:
                self.logger.debug(
                    "Competitor '%s' matched scraper '%s' (name)", competitor.name, scraper_id
                )
                return scraper_id

        self.logger.warning("No scraper found for competitor: %s", competitor.name)
        return None

    def _save_competitor_products(
        self,
        competitor_id: int,
        products: List[ScrapedProduct],
    ) -> Dict[str, Any]:
        """
        Upsert *products* into ``competitor_products``.

        Match key: ``(competitor_id, product_name)``.  Updates price,
        size_description, category, source_url, and scraped_at on existing
        rows; inserts a new row otherwise.

        Args:
            competitor_id: FK to competitors table.
            products:      List of scraped products.

        Returns:
            ``{'new_count': int, 'updated_count': int, 'errors': List[str]}``
        """
        new_count = updated_count = 0
        errors: List[str] = []
        now = datetime.now(timezone.utc)

        for product in products:
            try:
                existing: Optional[CompetitorProduct] = (
                    self.db.query(CompetitorProduct)
                    .filter(
                        CompetitorProduct.competitor_id == competitor_id,
                        CompetitorProduct.product_name == product.product_name,
                    )
                    .first()
                )

                if existing:
                    existing.price = float(product.price)
                    existing.size_description = product.unit or existing.size_description
                    existing.category = product.category or existing.category
                    existing.source_url = product.url or existing.source_url
                    existing.scraped_at = now
                    updated_count += 1
                else:
                    self.db.add(CompetitorProduct(
                        competitor_id=competitor_id,
                        product_name=product.product_name,
                        price=float(product.price),
                        size_description=product.unit or "",
                        category=product.category,
                        source_url=product.url,
                        scraped_at=now,
                    ))
                    new_count += 1

            except Exception as exc:
                msg = f"'{product.product_name}': {exc}"
                errors.append(msg)
                self.logger.error("Error saving competitor product %s", msg)

        try:
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            errors.append(f"DB commit failed: {exc}")
            self.logger.error("Failed to commit competitor products: %s", exc)
            return {"new_count": 0, "updated_count": 0, "errors": errors}

        self.logger.info(
            "Saved competitor products: %d new, %d updated, %d errors",
            new_count, updated_count, len(errors),
        )
        return {"new_count": new_count, "updated_count": updated_count, "errors": errors}

    @staticmethod
    def _default_search_queries() -> List[str]:
        """Coffee-shop default search terms used when none are provided."""
        return [
            "cappuccino", "latte", "americano", "espresso", "mocha",
            "cold brew", "café", "coffee", "sandwich", "pastry",
        ]

    @staticmethod
    def _create_default_logger() -> logging.Logger:
        logger = logging.getLogger("scraping.manager")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger


# ---------------------------------------------------------------------------
# Internal helper — builds a failure result dict with consistent shape
# ---------------------------------------------------------------------------

def _error(**kwargs: Any) -> Dict[str, Any]:
    """Return a standardised failure result dict."""
    base: Dict[str, Any] = {
        "success": False,
        "ingredient_id": None,
        "ingredient_name": None,
        "competitor_id": None,
        "competitor_name": None,
        "scraper_id": None,
        "error": "Unknown error",
    }
    base.update(kwargs)
    return base
