from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---------------------------------------------------------------------------
# Configuración de la aplicación
# ---------------------------------------------------------------------------
from backend.config import settings
from backend.database import Base

# Importar todos los modelos para que Alembic los detecte en autogenerate
import backend.models.ingredient   # noqa: F401
import backend.models.recipe_unit  # noqa: F401
import backend.models.store        # noqa: F401
import backend.models.product      # noqa: F401

# ---------------------------------------------------------------------------
# Objeto de configuración de Alembic
# ---------------------------------------------------------------------------
config = context.config

# Inyectar la URL desde Pydantic Settings (ignora el valor vacío de alembic.ini)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_POOLING)

# Configurar logging desde alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata de todos los modelos registrados en Base
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Migraciones en modo offline (genera SQL sin conectarse)
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Migraciones en modo online (conecta y ejecuta contra Supabase)
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # consistente con database.py
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
