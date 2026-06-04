from pathlib import Path
from typing import List, Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PRODUCTION_ORIGINS = [
    "https://cpq-cafeterias-production.up.railway.app",
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

    # ── CORS ───────────────────────────────────────────────────────────────
    # In production this is automatically overridden with _PRODUCTION_ORIGINS
    # if not explicitly defined in the environment.
    # In .env / environment variables use JSON format:
    #   ALLOWED_ORIGINS=["https://domain.com","https://other.com"]
    ALLOWED_ORIGINS: List[str] = ["*"]

    # ── Scraping ───────────────────────────────────────────────────────────
    SCRAPING_USER_AGENT: str
    SCRAPING_DELAY_MS: int = 1000

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
