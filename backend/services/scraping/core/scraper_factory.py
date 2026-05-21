"""
Factory for creating and managing scraper instances.

Implements the Factory + Registry patterns so the rest of the application
can request a scraper by ID without knowing which concrete class backs it.

The registry maps ``scraper_type`` values (from YAML config) to adapter
classes.  All built-in types map to :class:`~.scraper_adapter.ConfigurableScraper`;
specialised adapters can be registered at runtime via
:meth:`ScraperFactory.register_adapter`.

Usage::

    from backend.services.scraping.utils.config_loader import ConfigLoader
    from backend.services.scraping.core.scraper_factory import ScraperFactory

    loader  = ConfigLoader("/app/backend/services/scraping/config")
    factory = ScraperFactory(loader)

    # Single scraper
    with factory.create_scraper("competitor_001") as scraper:
        products = scraper.search_products("cappuccino", limit=20)

    # All enabled scrapers (no setup — callers manage lifecycle)
    scrapers = factory.create_all_scrapers(enabled_only=True)
    for sid, s in scrapers.items():
        with s:
            result = s.run_full_scrape("coffee")
"""

import logging
from typing import Dict, List, Optional, Type

from .base_scraper import BaseScraper
from .scraper_adapter import ConfigurableScraper
from .exceptions import ConfigurationError, ScraperNotFoundError
from ..utils.config_loader import ConfigLoader


# Default mapping of scraper_type → adapter class.
# All types currently resolve to ConfigurableScraper; specialised adapters
# can be registered at runtime without touching this constant.
_DEFAULT_REGISTRY: Dict[str, Type[BaseScraper]] = {
    "custom":      ConfigurableScraper,
    "restaurant":  ConfigurableScraper,
    "retail":      ConfigurableScraper,
    "marketplace": ConfigurableScraper,
}


class ScraperFactory:
    """
    Factory for creating scrapers from configuration.

    Implements the Factory + Registry patterns:
      - **Factory**:  ``create_scraper(scraper_id)`` returns a ready-to-use
        instance without callers knowing which concrete class is used.
      - **Registry**: adapter classes are stored in an instance-level dict so
        different factories can have different registries without interference.

    Cache behaviour
    ---------------
    When ``enable_cache=True`` (default), the factory keeps a reference to
    each created scraper.  Subsequent calls to ``create_scraper`` with the
    same ID return the cached instance.

    .. warning::
        Cached scrapers are **not** lifecycle-managed by the factory.
        The caller is responsible for calling ``setup()`` / ``teardown()``
        (or using the ``with`` statement) before each use.

    Examples::

        loader  = ConfigLoader("/app/config")
        factory = ScraperFactory(loader)

        # Get one scraper
        scraper = factory.create_scraper("competitor_001")
        with scraper:
            products = scraper.search_products("coffee", limit=10)

        # Register a custom adapter for a new scraper type
        factory.register_adapter("headless_spa", MySPAScraper)

        # Inspect what is registered
        factory.get_registered_adapters()
        # {'custom': ConfigurableScraper, 'restaurant': ConfigurableScraper, ...}
    """

    def __init__(
        self,
        config_loader: ConfigLoader,
        enable_cache: bool = True,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            config_loader: Configured :class:`~..utils.config_loader.ConfigLoader`
                           instance pointing at the YAML config directory.
            enable_cache:  Cache created scrapers.  Pass ``False`` in tests or
                           when you need a fresh instance on every call.
            logger:        Optional logger; a default one is created if omitted.
        """
        self.config_loader = config_loader
        self.enable_cache = enable_cache
        self.logger = logger or self._create_default_logger()

        # Instance-level copy so register_adapter on one factory does not
        # bleed into other factory instances or the module-level constant.
        self._adapter_registry: Dict[str, Type[BaseScraper]] = dict(_DEFAULT_REGISTRY)
        self._cache: Dict[str, BaseScraper] = {}

    # ------------------------------------------------------------------
    # Core factory methods
    # ------------------------------------------------------------------

    def create_scraper(
        self,
        scraper_id: str,
        force_reload: bool = False,
    ) -> BaseScraper:
        """
        Return a configured scraper for the given *scraper_id*.

        Steps:
          1. Return cached instance if cache is valid and *force_reload* is False.
          2. Load and validate the YAML config via ConfigLoader.
          3. Resolve the adapter class from the registry.
          4. Instantiate the scraper.
          5. Store in cache (when enabled).

        Args:
            scraper_id:   ID matching the YAML filename (without ``.yaml``).
            force_reload: Bypass cache and recreate the scraper from disk.

        Returns:
            Configured :class:`~.base_scraper.BaseScraper` instance.
            **Lifecycle is not started** — call ``setup()`` or use ``with``.

        Raises:
            ScraperNotFoundError: Config file not found or YAML is invalid.
            ConfigurationError:   Adapter type unknown or instantiation failed.

        Examples::

            scraper = factory.create_scraper("competitor_001")
            with scraper:
                result = scraper.run_full_scrape("cappuccino")
        """
        if self.enable_cache and not force_reload and scraper_id in self._cache:
            self.logger.debug("Cache hit for scraper '%s'", scraper_id)
            return self._cache[scraper_id]

        self.logger.info("Creating scraper '%s'", scraper_id)

        # --- Load & validate config ---
        try:
            config = self.config_loader.load_config(scraper_id, validate=True)
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ScraperNotFoundError(
                f"Failed to load config for scraper '{scraper_id}': {exc}",
                code="SCRAPER_CONFIG_LOAD_FAILED",
                details={"scraper_id": scraper_id, "error": str(exc)},
            ) from exc

        # --- Resolve adapter ---
        adapter_class = self._get_adapter_class(config.scraper_type)

        # --- Instantiate ---
        try:
            scraper = adapter_class(config)
        except Exception as exc:
            raise ConfigurationError(
                f"Failed to instantiate scraper '{scraper_id}' "
                f"(adapter={adapter_class.__name__}): {exc}",
                code="SCRAPER_INSTANTIATION_FAILED",
                details={"scraper_id": scraper_id, "adapter": adapter_class.__name__},
            ) from exc

        # --- Cache ---
        if self.enable_cache:
            self._cache[scraper_id] = scraper

        self.logger.info(
            "Created scraper '%s' — business='%s', type=%s, adapter=%s",
            scraper_id,
            config.business_name,
            config.scraper_type,
            adapter_class.__name__,
        )
        return scraper

    def create_all_scrapers(
        self,
        enabled_only: bool = True,
    ) -> Dict[str, BaseScraper]:
        """
        Create scrapers for every config found in the config directory.

        Failures for individual scrapers are logged as errors and skipped
        so a single bad config does not block the rest.

        Args:
            enabled_only: When True, skip configs with ``enabled: false``.

        Returns:
            ``{scraper_id: scraper_instance}`` for every successfully created
            scraper.  Instances are **not** started (no ``setup()`` called).

        Examples::

            scrapers = factory.create_all_scrapers(enabled_only=True)
            for sid, s in scrapers.items():
                with s:
                    result = s.run_full_scrape("café")
                    print(sid, result.to_summary())
        """
        ids = self.config_loader.list_scraper_ids(enabled_only=enabled_only)
        self.logger.info(
            "Creating %d scraper(s) (enabled_only=%s)", len(ids), enabled_only
        )

        scrapers: Dict[str, BaseScraper] = {}
        failures: List[Dict] = []

        for scraper_id in ids:
            try:
                scrapers[scraper_id] = self.create_scraper(scraper_id)
            except (ScraperNotFoundError, ConfigurationError) as exc:
                self.logger.error(
                    "Skipping scraper '%s': %s", scraper_id, exc
                )
                failures.append({"scraper_id": scraper_id, "error": str(exc), "code": exc.code})
            except Exception as exc:
                self.logger.error(
                    "Unexpected error creating scraper '%s': %s", scraper_id, exc
                )
                failures.append({"scraper_id": scraper_id, "error": str(exc), "code": "UNEXPECTED"})

        self.logger.info(
            "Created %d/%d scrapers (%d failure(s))",
            len(scrapers), len(ids), len(failures),
        )
        if failures:
            for f in failures:
                self.logger.warning("  Failed: %(scraper_id)s — %(code)s: %(error)s", f)

        return scrapers

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def register_adapter(
        self,
        adapter_type: str,
        adapter_class: Type[BaseScraper],
    ) -> None:
        """
        Register a new adapter class for a scraper type.

        Allows extending the factory with specialised scrapers without
        modifying this file.

        Args:
            adapter_type:  String key used as ``scraper_type`` in YAML configs.
            adapter_class: Concrete class that inherits from
                           :class:`~.base_scraper.BaseScraper`.

        Raises:
            TypeError: *adapter_class* does not subclass BaseScraper.

        Examples::

            class MySPAScraper(BaseScraper):
                ...

            factory.register_adapter("headless_spa", MySPAScraper)
            scraper = factory.create_scraper("my_spa_site")
        """
        if not (isinstance(adapter_class, type) and issubclass(adapter_class, BaseScraper)):
            raise TypeError(
                f"adapter_class must be a subclass of BaseScraper, "
                f"got {adapter_class!r}"
            )
        self._adapter_registry[adapter_type] = adapter_class
        self.logger.info(
            "Registered adapter type '%s' → %s", adapter_type, adapter_class.__name__
        )

    def get_registered_adapters(self) -> Dict[str, Type[BaseScraper]]:
        """
        Return a snapshot of the current adapter registry.

        Returns:
            ``{adapter_type: adapter_class}`` dict (copy — mutations do not
            affect the registry).

        Examples::

            for name, cls in factory.get_registered_adapters().items():
                print(name, cls.__name__)
        """
        return dict(self._adapter_registry)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def clear_cache(self, scraper_id: Optional[str] = None) -> None:
        """
        Remove scrapers from the instance cache.

        Args:
            scraper_id: ID of the scraper to evict.  When ``None``, clears
                        the entire cache.

        Examples::

            factory.clear_cache("competitor_001")   # evict one
            factory.clear_cache()                   # evict all
        """
        if scraper_id is not None:
            evicted = self._cache.pop(scraper_id, None)
            if evicted is not None:
                self.logger.debug("Cache evicted: '%s'", scraper_id)
            else:
                self.logger.debug("Cache miss on clear: '%s' was not cached", scraper_id)
        else:
            count = len(self._cache)
            self._cache.clear()
            self.logger.debug("Cache cleared (%d entry/entries removed)", count)

    def list_cached_ids(self) -> List[str]:
        """
        Return the IDs of scrapers currently held in the cache.

        Returns:
            Sorted list of scraper IDs.

        Examples::

            print(factory.list_cached_ids())
            # ['competitor_001', 'supplier_x']
        """
        return sorted(self._cache.keys())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_adapter_class(self, scraper_type: str) -> Type[BaseScraper]:
        """
        Look up *scraper_type* in the registry and return the adapter class.

        Args:
            scraper_type: Value from ``ScraperConfig.scraper_type``.

        Returns:
            Adapter class mapped to *scraper_type*.

        Raises:
            ConfigurationError: No adapter registered for *scraper_type*.
        """
        adapter_class = self._adapter_registry.get(scraper_type)
        if adapter_class is None:
            available = sorted(self._adapter_registry.keys())
            raise ConfigurationError(
                f"No adapter registered for scraper_type '{scraper_type}'. "
                f"Available types: {available}",
                code="UNKNOWN_SCRAPER_TYPE",
                details={"scraper_type": scraper_type, "available": available},
            )
        return adapter_class

    def __repr__(self) -> str:
        return (
            f"ScraperFactory("
            f"cached={len(self._cache)}, "
            f"adapters={sorted(self._adapter_registry.keys())}, "
            f"cache_enabled={self.enable_cache})"
        )

    @staticmethod
    def _create_default_logger() -> logging.Logger:
        logger = logging.getLogger("scraping.factory")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger
