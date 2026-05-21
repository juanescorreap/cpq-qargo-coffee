import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

# ============================================
# CONFIGURACIÓN DE SUPABASE
# ============================================

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
SUPABASE_POOLER_URL = os.getenv("SUPABASE_POOLER_URL")

DATABASE_URL = SUPABASE_POOLER_URL or SUPABASE_DB_URL

if not DATABASE_URL:
    raise ValueError(
        "SUPABASE_DB_URL o SUPABASE_POOLER_URL debe estar configurado. "
        "Obtén estas URLs desde tu proyecto de Supabase."
    )

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT") is not None

# ============================================
# CONFIGURACIÓN DEL ENGINE
# ============================================

if IS_PRODUCTION and SUPABASE_POOLER_URL:
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
    print("✅ Usando Supabase Pooler (NullPool)")
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    print("✅ Usando pool de conexiones estándar")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency para obtener sesión de DB."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crea todas las tablas definidas en los modelos que hereden de Base."""
    Base.metadata.create_all(bind=engine)


# ============================================
# TEST DE CONEXIÓN
# ============================================

def test_connection() -> bool:
    """Test de conexión a Supabase."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ Conexión a Supabase exitosa")
        return True
    except Exception as e:
        print(f"❌ Error conectando a Supabase: {e}")
        return False


if IS_PRODUCTION:
    test_connection()
