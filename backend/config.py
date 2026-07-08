from pathlib import Path
from typing import List, Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PRODUCTION_ORIGINS = [
    "https://cpq-qargo-coffee-production.up.railway.app",
]


class Settings(BaseSettings):
    """Central application configuration loaded from environment variables.

    Required fields (without a default) must be present in the .env file
    or in the process environment before startup; pydantic-settings will report
    the missing field explicitly at startup.

    Sensitive fields (DATABASE_URL, SECRET_KEY) must never be committed to the
    repository; they should live exclusively in .env (development) or in the
    environment variables of the deployment platform (production).

    ALLOWED_ORIGINS usage:
        - If ENVIRONMENT == "production" and ALLOWED_ORIGINS was not overridden
          in the environment, the explicit list of allowed domains defined in
          _PRODUCTION_ORIGINS is applied automatically.
        - To add domains in production without touching the code, set the
          environment variable:
            ALLOWED_ORIGINS=["https://your-domain.com","https://other.com"]
    """

    # ── Database ───────────────────────────────────────────────────────────
    # Accept both the canonical name (SUPABASE_DB_URL) and the legacy .env name
    # (DATABASE_URL) so existing local environments keep working without changes.
    SUPABASE_DB_URL: str = Field(
        validation_alias=AliasChoices("SUPABASE_DB_URL", "DATABASE_URL")
    )
    SUPABASE_POOLER_URL: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("SUPABASE_POOLER_URL", "DATABASE_URL_POOLING"),
    )

    @property
    def DATABASE_URL_POOLING(self) -> str:
        """SQLAlchemy URL: prefers the pooler URL when available."""
        return self.SUPABASE_POOLER_URL or self.SUPABASE_DB_URL

    # ── Application ────────────────────────────────────────────────────────
    DEBUG: bool = False
    SECRET_KEY: str
    ENVIRONMENT: str = "production"  # "development" | "production"

    # ── Auth (HTTP Basic gate) ─────────────────────────────────────────────
    # Enabled only when BOTH are set (set them in the deploy env). When unset,
    # auth is disabled so local dev / tests run without credentials.
    BASIC_AUTH_USER: Optional[str] = None
    BASIC_AUTH_PASSWORD: Optional[str] = None

    @property
    def auth_enabled(self) -> bool:
        return bool(self.BASIC_AUTH_USER and self.BASIC_AUTH_PASSWORD)

    # ── CORS ───────────────────────────────────────────────────────────────
    # In production this is automatically overridden with _PRODUCTION_ORIGINS
    # if not explicitly defined in the environment.
    # In .env / environment variables use JSON format:
    #   ALLOWED_ORIGINS=["https://domain.com","https://other.com"]
    ALLOWED_ORIGINS: List[str] = ["*"]

    # ── Scraping ───────────────────────────────────────────────────────────
    SCRAPING_USER_AGENT: str
    SCRAPING_DELAY_MS: int = 1000

    # ── Catalog API integration ────────────────────────────────────────────
    # External Qargo catalog API (JWT auth). Credentials live only in .env /
    # the deploy environment — never committed, never written to disk or DB.
    CATALOG_API_BASE_URL: Optional[str] = None
    CATALOG_API_EMAIL: Optional[str] = None
    CATALOG_API_PASSWORD: Optional[str] = None
    # Weekly cron for the automatic sync. Format: "day_of_week hour" (default
    # Monday 6am). Parsed by the scheduler wiring.
    CATALOG_SYNC_SCHEDULE: str = "mon 6"

    @property
    def catalog_api_enabled(self) -> bool:
        return bool(
            self.CATALOG_API_BASE_URL
            and self.CATALOG_API_EMAIL
            and self.CATALOG_API_PASSWORD
        )

    # ── Partition maintenance (0027 / #4) ──────────────────────────────────
    # Drive fn_run_partition_maintenance from the worker (app-side mirror of the
    # pg_cron partition_maintenance job). Months of recipe_cost_snapshots monthly
    # partitions to pre-create ahead of now, and how many months of snapshots to
    # retain before the maintenance run drops the older monthly partitions.
    PARTITION_AHEAD_MONTHS: int = 3
    SNAPSHOT_RETENTION_MONTHS: int = 18

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _apply_production_cors(self) -> "Settings":
        """Automatically restricts CORS in production environment.

        If ENVIRONMENT is "production" and ALLOWED_ORIGINS was not overridden
        in the environment (still the default wildcard), replaces it with the
        explicit list of allowed domains defined in _PRODUCTION_ORIGINS. This
        prevents accidentally exposing the API with open CORS in production due
        to a missing configuration.

        If additional domains are needed, define them in the ALLOWED_ORIGINS
        environment variable (JSON format) instead of modifying this file.
        """
        if self.ENVIRONMENT == "production" and self.ALLOWED_ORIGINS == ["*"]:
            self.ALLOWED_ORIGINS = _PRODUCTION_ORIGINS
        return self


settings = Settings()
