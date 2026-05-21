from pathlib import Path
from typing import List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PRODUCTION_ORIGINS = [
    "https://cpq-cafeterias-production.up.railway.app",
]


class Settings(BaseSettings):
    """Configuración central de la aplicación cargada desde variables de entorno.

    Los campos requeridos (sin default) deben estar presentes en el archivo .env
    o en el entorno del proceso antes de arrancar; pydantic-settings reportará el
    campo faltante explícitamente en el arranque.

    Campos sensibles (DATABASE_URL, SECRET_KEY) nunca deben commitearse al
    repositorio; deben vivir exclusivamente en .env (desarrollo) o en las
    variables de entorno de la plataforma de despliegue (producción).

    Uso de ALLOWED_ORIGINS:
        - Si ENVIRONMENT == "production" y ALLOWED_ORIGINS no fue sobreescrito
          en el entorno, se aplica automáticamente la lista de dominios
          permitidos definida en _PRODUCTION_ORIGINS.
        - Para añadir dominios en producción sin tocar el código, establece
          la variable de entorno:
            ALLOWED_ORIGINS=["https://tu-dominio.com","https://otro.com"]
    """

    # ── Base de datos ──────────────────────────────────────────────────────
    DATABASE_URL: str
    DATABASE_URL_POOLING: Optional[str] = None

    # ── Aplicación ─────────────────────────────────────────────────────────
    DEBUG: bool = False
    SECRET_KEY: str
    ENVIRONMENT: str = "production"  # "development" | "production"

    # ── CORS ───────────────────────────────────────────────────────────────
    # En producción se sobreescribe automáticamente con _PRODUCTION_ORIGINS
    # si no se define explícitamente en el entorno.
    # En .env / variables de entorno usar formato JSON:
    #   ALLOWED_ORIGINS=["https://dominio.com","https://otro.com"]
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
        """Restringe CORS automáticamente en entorno de producción.

        Si ENVIRONMENT es "production" y ALLOWED_ORIGINS no fue sobreescrito
        en el entorno (sigue siendo el wildcard por defecto), reemplaza con
        la lista explícita de dominios permitidos definida en
        _PRODUCTION_ORIGINS. Esto evita exponer la API con CORS abierto en
        producción por olvido de configuración.

        Si se necesitan dominios adicionales, defínelos en la variable de
        entorno ALLOWED_ORIGINS (formato JSON) en lugar de modificar este
        archivo.
        """
        if self.ENVIRONMENT == "production" and self.ALLOWED_ORIGINS == ["*"]:
            self.ALLOWED_ORIGINS = _PRODUCTION_ORIGINS
        return self


settings = Settings()
