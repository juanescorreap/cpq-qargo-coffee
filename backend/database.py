import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from backend.config import settings

# ============================================
# SUPABASE CONFIGURATION
# ============================================

SUPABASE_DB_URL = settings.SUPABASE_DB_URL
SUPABASE_POOLER_URL = settings.SUPABASE_POOLER_URL

DATABASE_URL = SUPABASE_POOLER_URL or SUPABASE_DB_URL

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

# ============================================
# READ REPLICA (E2E_ARCHITECTURE_AUDIT G4)
# ============================================
# Context prefetch (load_context) is read-heavy; at scale it should hit a replica
# while writes go to the primary. If DATABASE_URL_REPLICA is unset, transparently
# fall back to the primary engine — zero behaviour change until a replica exists.

_REPLICA_URL = os.getenv("DATABASE_URL_REPLICA", "").strip()
if _REPLICA_URL:
    if _REPLICA_URL.startswith("postgres://"):
        _REPLICA_URL = _REPLICA_URL.replace("postgres://", "postgresql://", 1)
    read_engine = create_engine(_REPLICA_URL, poolclass=NullPool, echo=False)
    print("✅ Using read replica for context prefetch")
else:
    read_engine = engine  # fallback: same primary

ReadSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=read_engine)

Base = declarative_base()


def get_db():
    """Dependency to obtain a DB session (primary — reads + writes)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db():
    """Read-only session bound to the replica (or the primary as fallback).
    Use for heavy prefetch where eventual consistency is acceptable; the snapshot
    records price_valid_from so a slightly stale replica is still reproducible."""
    db = ReadSessionLocal()
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
