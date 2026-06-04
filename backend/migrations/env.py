from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---------------------------------------------------------------------------
# Application configuration
# ---------------------------------------------------------------------------
from backend.config import settings
from backend.database import Base

# Import all models so that Alembic detects them in autogenerate
import backend.models.currency    # noqa: F401
import backend.models.ingredient   # noqa: F401
import backend.models.ingredient_substitute_region  # noqa: F401
import backend.models.recipe_unit  # noqa: F401
import backend.models.store        # noqa: F401
import backend.models.category     # noqa: F401
import backend.models.product      # noqa: F401
import backend.models.pricing      # noqa: F401
import backend.models.competitor   # noqa: F401
import backend.models.modifier      # noqa: F401
import backend.models.supply_chain  # noqa: F401

# ---------------------------------------------------------------------------
# Alembic configuration object
# ---------------------------------------------------------------------------
config = context.config

# Inject the URL from Pydantic Settings (ignores the empty value in alembic.ini)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_POOLING)

# Configure logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata for all models registered in Base
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline migrations (generates SQL without connecting)
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
# Online migrations (connects and runs against Supabase)
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # consistent with database.py
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
