"""
YAML configuration loader for the scraping system.

Responsibilities:
  - Locate scraper configs under competitors/ and suppliers/ sub-directories.
  - Validate raw YAML against a Pydantic schema before constructing ScraperConfig.
  - Apply template inheritance when a config declares ``extends: <template_id>``.
  - Cache loaded configs with a configurable TTL.
  - Expose hot-reload (force cache invalidation for a single scraper).

Directory layout expected under config_dir::

    config_dir/
    ├── registry.yaml               # optional master registry
    ├── _templates/
    │   └── base_restaurant.yaml    # shared base configs
    ├── competitors/
    │   └── competitor_a.yaml
    └── suppliers/
        └── supplier_b.yaml
"""

import copy
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from ..core.exceptions import ConfigurationError
from ..core.exceptions import ValidationError as ScrapingValidationError
from ..core.models import ScraperConfig


# ---------------------------------------------------------------------------
# Pydantic validation schema
# ---------------------------------------------------------------------------

class _SelectorConfigSchema(BaseModel):
    """Validates a single selector definition inside the 'selectors' block."""
    selector: str
    type: str = "css"
    attribute: Optional[str] = None
    optional: bool = False
    multiple: bool = False

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in ("css", "xpath"):
            raise ValueError(f"type must be 'css' or 'xpath', got '{v}'")
        return v


class _RateLimitingSchema(BaseModel):
    enabled: bool = True
    delay_ms: int = Field(default=1_000, ge=0)
    max_requests_per_minute: int = Field(default=60, ge=1)
    max_requests_per_hour: int = Field(default=1_000, ge=1)
    backoff_strategy: str = "exponential"
    max_backoff_ms: int = Field(default=60_000, ge=0)

    @field_validator("backoff_strategy")
    @classmethod
    def _valid_strategy(cls, v: str) -> str:
        if v not in ("fixed", "exponential"):
            raise ValueError(f"backoff_strategy must be 'fixed' or 'exponential', got '{v}'")
        return v


class _BrowserSchema(BaseModel):
    headless: bool = True
    timeout_ms: int = Field(default=30_000, ge=1_000)
    viewport_width: int = Field(default=1280, ge=320)
    viewport_height: int = Field(default=800, ge=240)
    user_agent: Optional[str] = None
    extra_headers: Dict[str, str] = Field(default_factory=dict)


class _NavigationSchema(BaseModel):
    search: Dict[str, Any] = Field(default_factory=dict)
    category: Dict[str, Any] = Field(default_factory=dict)
    product: Dict[str, Any] = Field(default_factory=dict)
    pagination: Dict[str, Any] = Field(default_factory=dict)
    timeouts: Dict[str, Any] = Field(default_factory=dict)


class ScraperConfigSchema(BaseModel):
    """Full Pydantic schema for a scraper YAML config file."""

    scraper_id: str
    business_name: str
    business_type: str
    scraper_type: str
    base_url: str
    selectors: Dict[str, Any] = Field(default_factory=dict)
    navigation: _NavigationSchema = Field(default_factory=_NavigationSchema)
    browser: _BrowserSchema = Field(default_factory=_BrowserSchema)
    rate_limiting: _RateLimitingSchema = Field(default_factory=_RateLimitingSchema)
    required_fields: List[str] = Field(default_factory=list)
    enabled: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # Template inheritance — consumed by ConfigLoader, not stored in ScraperConfig.
    extends: Optional[str] = None

    @field_validator("business_type")
    @classmethod
    def _valid_business_type(cls, v: str) -> str:
        if v not in ("competitor", "supplier"):
            raise ValueError(f"business_type must be 'competitor' or 'supplier', got '{v}'")
        return v

    @field_validator("scraper_type")
    @classmethod
    def _valid_scraper_type(cls, v: str) -> str:
        allowed = {"restaurant", "retail", "marketplace", "custom"}
        if v not in allowed:
            raise ValueError(f"scraper_type must be one of {sorted(allowed)}, got '{v}'")
        return v

    @field_validator("base_url")
    @classmethod
    def _non_empty_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("base_url cannot be empty")
        return v.rstrip("/")

    @model_validator(mode="after")
    def _check_required_selectors(self) -> "ScraperConfigSchema":
        missing = [f for f in self.required_fields if f not in self.selectors]
        if missing:
            raise ValueError(
                f"required_fields references selectors not defined: {missing}"
            )
        return self


# ---------------------------------------------------------------------------
# ConfigLoader
# ---------------------------------------------------------------------------

_SEARCH_DIRS = ("competitors", "suppliers", "_templates")
_REGISTRY_FILE = "registry.yaml"
_TEMPLATES_DIR = "_templates"


class ConfigLoader:
    """
    YAML config loader with validation, template inheritance, and TTL cache.

    Supports:
      - Automatic Pydantic validation on load.
      - Template inheritance via ``extends: <template_id>`` in YAML.
      - In-memory cache with configurable TTL.
      - Force hot-reload per scraper without restarting the process.

    Example::

        loader = ConfigLoader("/app/backend/services/scraping/config")
        cfg = loader.load_config("competitor_a")
        print(cfg.base_url)

        # List all enabled scrapers
        ids = loader.list_scraper_ids(enabled_only=True)

        # Force reload after editing a YAML file
        loader.reload_config("competitor_a")
    """

    def __init__(
        self,
        config_dir: str,
        enable_cache: bool = True,
        cache_ttl_seconds: int = 300,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            config_dir:          Root directory that contains competitors/,
                                 suppliers/, and optionally _templates/.
            enable_cache:        Cache parsed configs to avoid repeated YAML I/O.
            cache_ttl_seconds:   Seconds before a cached entry is considered stale.
            logger:              Optional logger; default is created if omitted.
        """
        self.config_dir = Path(config_dir)
        self.enable_cache = enable_cache
        self.cache_ttl = cache_ttl_seconds
        self.logger = logger or self._create_default_logger()

        self._cache: Dict[str, ScraperConfig] = {}
        self._cache_timestamps: Dict[str, float] = {}

        if not self.config_dir.exists():
            raise ConfigurationError(
                f"Config directory not found: {config_dir}",
                code="CONFIG_DIR_NOT_FOUND",
                details={"path": str(config_dir)},
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_config(self, scraper_id: str, validate: bool = True) -> ScraperConfig:
        """
        Load, validate, and return a single scraper config.

        Search order:
          1. In-memory cache (when enabled and not expired).
          2. ``competitors/<scraper_id>.yaml``
          3. ``suppliers/<scraper_id>.yaml``

        Args:
            scraper_id: The ``scraper_id`` value from the YAML file.
            validate:   Run Pydantic validation (recommended; disable only for
                        debugging malformed files).

        Returns:
            Validated :class:`~..core.models.ScraperConfig`.

        Raises:
            ConfigurationError: File not found or YAML is malformed.
            ValidationError:    Pydantic schema validation fails.

        Examples:
            >>> cfg = loader.load_config("competitor_a")
            >>> cfg.base_url
            'https://competitor-a.com'
        """
        if self.enable_cache and self._is_cache_valid(scraper_id):
            self.logger.debug("Cache hit for scraper '%s'", scraper_id)
            return self._cache[scraper_id]

        path = self._find_config_file(scraper_id)
        raw = self._load_yaml_file(path)
        raw = self._apply_template_inheritance(raw)

        if validate:
            config = self.validate_config(raw)
        else:
            config = ScraperConfig.from_dict(raw)

        if self.enable_cache:
            self._cache[scraper_id] = config
            self._cache_timestamps[scraper_id] = time.monotonic()

        self.logger.info("Loaded config for scraper '%s' from %s", scraper_id, path)
        return config

    def load_registry(self) -> Dict[str, Any]:
        """
        Load the master registry file (``registry.yaml`` in config_dir).

        The registry is a YAML file listing all scrapers with their
        enabled/disabled status and optional metadata. It is optional;
        if absent, an empty dict is returned.

        Returns:
            Raw registry dict (no Pydantic validation applied).

        Examples:
            >>> registry = loader.load_registry()
            >>> print(registry["scrapers"])
        """
        registry_path = self.config_dir / _REGISTRY_FILE
        if not registry_path.exists():
            self.logger.debug("No registry.yaml found at %s", registry_path)
            return {}
        return self._load_yaml_file(registry_path)

    def list_scraper_ids(self, enabled_only: bool = True) -> List[str]:
        """
        Return the scraper IDs available in the config directory.

        IDs are derived from YAML filenames (without extension) under
        ``competitors/`` and ``suppliers/`` sub-directories. Files whose
        names start with ``_`` are treated as templates and excluded.

        Args:
            enabled_only: When True, skip scrapers whose config has
                          ``enabled: false``.

        Returns:
            Sorted list of scraper IDs.

        Examples:
            >>> loader.list_scraper_ids()
            ['competitor_a', 'competitor_b', 'supplier_x']
        """
        ids: List[str] = []
        for subdir in ("competitors", "suppliers"):
            search_path = self.config_dir / subdir
            if not search_path.exists():
                continue
            for yaml_file in sorted(search_path.glob("*.yaml")):
                if yaml_file.stem.startswith("_"):
                    continue
                scraper_id = yaml_file.stem
                if enabled_only:
                    try:
                        cfg = self.load_config(scraper_id)
                        if not cfg.enabled:
                            continue
                    except (ConfigurationError, ScrapingValidationError):
                        self.logger.warning(
                            "Skipping invalid config '%s' during listing", scraper_id
                        )
                        continue
                ids.append(scraper_id)
        return ids

    def load_all_configs(self) -> Dict[str, ScraperConfig]:
        """
        Load every valid config found in the config directory.

        Invalid configs are logged as warnings and skipped so that one bad
        file does not block the rest from loading.

        Returns:
            Mapping of ``{scraper_id: ScraperConfig}`` for all valid configs.

        Examples:
            >>> all_cfgs = loader.load_all_configs()
            >>> for sid, cfg in all_cfgs.items():
            ...     print(sid, cfg.base_url)
        """
        results: Dict[str, ScraperConfig] = {}
        for scraper_id in self.list_scraper_ids(enabled_only=False):
            try:
                results[scraper_id] = self.load_config(scraper_id)
            except (ConfigurationError, ScrapingValidationError) as exc:
                self.logger.warning("Failed to load config '%s': %s", scraper_id, exc)
        return results

    def validate_config(self, config_dict: Dict[str, Any]) -> ScraperConfig:
        """
        Run Pydantic validation on a raw config dict and return a ScraperConfig.

        Args:
            config_dict: Raw YAML-parsed dict (already template-merged).

        Returns:
            Validated :class:`~..core.models.ScraperConfig`.

        Raises:
            ValidationError: One or more Pydantic validation errors.

        Examples:
            >>> raw = {"scraper_id": "test", "business_name": "Test", ...}
            >>> cfg = loader.validate_config(raw)
        """
        try:
            schema = ScraperConfigSchema.model_validate(config_dict)
        except PydanticValidationError as exc:
            errors = [
                f"{' → '.join(str(loc) for loc in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            ]
            raise ScrapingValidationError(
                f"Config validation failed with {len(errors)} error(s)",
                code="CONFIG_VALIDATION_FAILED",
                details={"errors": errors, "scraper_id": config_dict.get("scraper_id")},
            ) from exc

        return ScraperConfig(
            scraper_id=schema.scraper_id,
            business_name=schema.business_name,
            business_type=schema.business_type,
            scraper_type=schema.scraper_type,
            base_url=schema.base_url,
            selectors=schema.selectors,
            navigation=schema.navigation.model_dump(),
            browser=schema.browser.model_dump(),
            rate_limiting=schema.rate_limiting.model_dump(),
            required_fields=schema.required_fields,
            enabled=schema.enabled,
            metadata=schema.metadata,
        )

    def reload_config(self, scraper_id: str) -> ScraperConfig:
        """
        Invalidate the cache for *scraper_id* and reload from disk.

        Args:
            scraper_id: Scraper whose config should be reloaded.

        Returns:
            Freshly loaded :class:`~..core.models.ScraperConfig`.

        Examples:
            >>> # After editing competitor_a.yaml on disk:
            >>> cfg = loader.reload_config("competitor_a")
        """
        self._cache.pop(scraper_id, None)
        self._cache_timestamps.pop(scraper_id, None)
        self.logger.info("Cache invalidated for scraper '%s', reloading…", scraper_id)
        return self.load_config(scraper_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_config_file(self, scraper_id: str) -> Path:
        """
        Locate the YAML file for *scraper_id* across known sub-directories.

        Raises:
            ConfigurationError: File not found in any search path.
        """
        for subdir in ("competitors", "suppliers", "_templates"):
            candidate = self.config_dir / subdir / f"{scraper_id}.yaml"
            if candidate.exists():
                return candidate

        searched = [str(self.config_dir / d / f"{scraper_id}.yaml") for d in _SEARCH_DIRS]
        raise ConfigurationError(
            f"Config file for scraper '{scraper_id}' not found",
            code="CONFIG_FILE_NOT_FOUND",
            details={"scraper_id": scraper_id, "searched": searched},
        )

    def _load_yaml_file(self, path: Path) -> Dict[str, Any]:
        """
        Read and parse a YAML file, raising ConfigurationError on any failure.

        Args:
            path: Absolute path to the YAML file.

        Returns:
            Parsed dict.

        Raises:
            ConfigurationError: File unreadable or YAML syntax error.
        """
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"Config file not found: {path}",
                code="CONFIG_FILE_NOT_FOUND",
                details={"path": str(path)},
            ) from exc
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"YAML syntax error in '{path}': {exc}",
                code="CONFIG_YAML_SYNTAX_ERROR",
                details={"path": str(path), "error": str(exc)},
            ) from exc

        if not isinstance(data, dict):
            raise ConfigurationError(
                f"Config file '{path}' must contain a YAML mapping at the root level",
                code="CONFIG_INVALID_STRUCTURE",
                details={"path": str(path), "got": type(data).__name__},
            )

        return data

    def _apply_template_inheritance(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolve ``extends: <template_id>`` inheritance chain and return a
        fully-merged config dict.

        Inheritance is applied recursively (a template can itself extend
        another template). Cycles are detected and raise ConfigurationError.

        The resolution strategy is: template values are the base, child values
        win on conflict (child always overrides parent).

        Args:
            config: Raw config dict that may contain an ``extends`` key.

        Returns:
            Merged dict with the ``extends`` key removed.
        """
        visited: List[str] = []
        return self._resolve_extends(config, visited)

    def _resolve_extends(
        self, config: Dict[str, Any], visited: List[str]
    ) -> Dict[str, Any]:
        """Recursive helper for :meth:`_apply_template_inheritance`."""
        template_id: Optional[str] = config.get("extends")
        if not template_id:
            return config

        if template_id in visited:
            raise ConfigurationError(
                f"Circular template inheritance detected: {' → '.join(visited)} → {template_id}",
                code="CONFIG_CIRCULAR_INHERITANCE",
                details={"chain": visited + [template_id]},
            )

        self.logger.debug(
            "Applying template '%s' for config '%s'",
            template_id,
            config.get("scraper_id", "<unknown>"),
        )
        visited = visited + [template_id]

        template_path = self.config_dir / _TEMPLATES_DIR / f"{template_id}.yaml"
        if not template_path.exists():
            raise ConfigurationError(
                f"Template '{template_id}' not found at {template_path}",
                code="CONFIG_TEMPLATE_NOT_FOUND",
                details={"template_id": template_id, "path": str(template_path)},
            )

        base = self._load_yaml_file(template_path)
        base = self._resolve_extends(base, visited)  # templates can chain

        merged = self._merge_configs(base, config)
        merged.pop("extends", None)
        return merged

    def _merge_configs(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep-merge two config dicts; *override* values win on conflict.

        Rules:
          - Both dicts at the same key → recurse.
          - Override has the key, base does not → take override value.
          - Base has the key, override does not → take base value.
          - Both are non-dict scalars or lists → override wins.

        Lists are *replaced*, not concatenated, so a child config can fully
        redefine ``required_fields`` without inheriting the parent's list.

        Args:
            base:     Template / parent config dict.
            override: Child config dict (values take priority).

        Returns:
            New merged dict (neither input is mutated).
        """
        result = copy.deepcopy(base)
        for key, override_val in override.items():
            base_val = result.get(key)
            if isinstance(base_val, dict) and isinstance(override_val, dict):
                result[key] = self._merge_configs(base_val, override_val)
            else:
                result[key] = copy.deepcopy(override_val)
        return result

    def _is_cache_valid(self, scraper_id: str) -> bool:
        """
        Return True if a cache entry exists for *scraper_id* and has not
        exceeded :attr:`cache_ttl`.
        """
        if scraper_id not in self._cache:
            return False
        age = time.monotonic() - self._cache_timestamps.get(scraper_id, 0.0)
        return age < self.cache_ttl

    @staticmethod
    def _create_default_logger() -> logging.Logger:
        logger = logging.getLogger("scraping.config_loader")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger
