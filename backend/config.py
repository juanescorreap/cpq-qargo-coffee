from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración central de la aplicación cargada desde variables de entorno.

    Los campos requeridos (sin default) deben estar presentes en el archivo .env
    o en el entorno antes de iniciar la aplicación; de lo contrario Pydantic lanza
    un error en el arranque con el campo faltante indicado explícitamente.
    """

    DATABASE_URL_POOLING: Optional[str] = None
    DEBUG: bool = False
    SECRET_KEY: str
    SCRAPING_USER_AGENT: str
    SCRAPING_DELAY_MS: int = 1000

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
