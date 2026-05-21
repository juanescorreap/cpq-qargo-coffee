import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from sqlalchemy import inspect

import backend.models  # noqa: F401 — registers all models in Base.metadata
from backend.database import Base, engine


def init_database():
    """Initializes the database in production.

    Creates all tables if they do not exist (idempotent).
    """
    print("🗄️  Initializing database...")

    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Tables created/verified in Supabase")

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"📋 Tables in DB: {', '.join(sorted(tables))}")

    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        raise


if __name__ == "__main__":
    init_database()
