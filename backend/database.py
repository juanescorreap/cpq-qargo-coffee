from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings

engine = create_engine(
    settings.DATABASE_URL_POOLING,
    poolclass=NullPool,
    echo=False,
    connect_args={
        "options": "-c timezone=America/Bogota"
    },
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency de FastAPI que provee una sesión de base de datos por request.

    Yields:
        Session: Sesión de SQLAlchemy. Se cierra automáticamente al finalizar el request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crea todas las tablas definidas en los modelos que hereden de Base.

    Debe llamarse al iniciar la aplicación. No recrea tablas que ya existan.
    """
    Base.metadata.create_all(bind=engine)


def test_connection() -> bool:
    """Verifica que la conexión a Supabase esté disponible.

    Ejecuta una consulta mínima contra la base de datos y reporta el resultado.

    Returns:
        bool: True si la conexión es exitosa, False en caso de error.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Conexión a Supabase exitosa.")
        return True
    except Exception as e:
        print(f"Error al conectar con Supabase: {e}")
        return False
