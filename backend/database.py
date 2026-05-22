import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

# ============================================
# SUPABASE CONFIGURATION
# ============================================

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
SUPABASE_POOLER_URL = os.getenv("SUPABASE_POOLER_URL")

DATABASE_URL = SUPABASE_POOLER_URL or SUPABASE_DB_URL

if not DATABASE_URL:
    raise ValueError(
        "SUPABASE_DB_URL or SUPABASE_POOLER_URL must be configured. "
        "Get these URLs from your Supabase project."
    )

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT") is not None

# ============================================
# ENGINE CONFIGURATION
# ============================================

if SUPABASE_POOLER_URL:
    # Supabase's PgBouncer manages the actual connection pool — SQLAlchemy
    # should not add its own pool on top (would exceed Supabase's connection limits).
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
    print("✅ Using Supabase Pooler (NullPool)")
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    print("✅ Using standard connection pool")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency to obtain a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Creates all tables defined in models that inherit from Base."""
    Base.metadata.create_all(bind=engine)


# ============================================
# CONNECTION TEST
# ============================================

def test_connection() -> bool:
    """Connection test to Supabase."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ Supabase connection successful")
        return True
    except Exception as e:
        print(f"❌ Error connecting to Supabase: {e}")
        return False


if IS_PRODUCTION:
    test_connection()
