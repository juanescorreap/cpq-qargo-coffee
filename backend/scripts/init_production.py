"""Production database bootstrap.

Brings the schema up to head by running the Alembic migrations, which are the
single source of truth for the schema. The migrations create objects that do
NOT live in SQLAlchemy model metadata — partitioned tables, EXCLUDE/CHECK
constraints, the ``price_amount`` domain, functions, triggers and pg_cron jobs —
so ``Base.metadata.create_all()`` must never be used to provision production
(it would build a structurally incomplete schema and silently diverge from the
migration history).

``init_database()`` raises on failure so a broken migration fails the deploy
loudly instead of letting the app serve a half-migrated schema.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

logger = logging.getLogger("init_production")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _PROJECT_ROOT / "backend" / "migrations"

# Session-level advisory lock key (arbitrary but stable) so that if more than one
# web instance boots at once, their migration runs serialize instead of racing on
# alembic_version. Session-scoped: auto-released if the process dies mid-upgrade.
_MIGRATION_LOCK_KEY = 0x6371716D6967  # ascii "cqqmig"


def _alembic_config() -> Config:
    """Alembic config pinned to absolute paths so it works regardless of the
    process's current working directory (the app may boot from an arbitrary cwd).
    The DB URL is injected by ``backend/migrations/env.py`` from Settings, so it
    is intentionally not set here."""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return cfg


def init_database() -> None:
    """Apply all pending Alembic migrations (``upgrade head``) under a cluster-wide
    advisory lock so concurrent web boots can't race. Raises on failure so a broken
    migration aborts the boot (and the deploy) instead of serving a bad schema."""
    from backend.database import engine

    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY})
        try:
            logger.info("Applying Alembic migrations (upgrade head)...")
            command.upgrade(_alembic_config(), "head")
            logger.info("Migrations applied: schema at head.")
        finally:
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY}
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_database()
