import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from sqlalchemy import inspect

import backend.models  # noqa: F401 — registra todos los modelos en Base.metadata
from backend.database import Base, engine


def init_database():
    """Inicializa base de datos en producción.

    Crea todas las tablas si no existen (idempotente).
    """
    print("🗄️  Inicializando base de datos...")

    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Tablas creadas/verificadas en Supabase")

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"📋 Tablas en DB: {', '.join(sorted(tables))}")

    except Exception as e:
        print(f"❌ Error inicializando base de datos: {e}")
        raise


if __name__ == "__main__":
    init_database()
