from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings


def _resolve_database_url() -> str:
    """Selecciona y normaliza la URL de conexión a la base de datos.

    Orden de prioridad:
    1. DATABASE_URL_POOLING (Supabase PgBouncer, puerto 6543) — preferida en
       producción porque reduce el número de conexiones abiertas contra
       el servidor Postgres subyacente.
    2. DATABASE_URL (conexión directa, puerto 5432) — fallback para desarrollo
       local o cuando no se configure el pooler.

    Además aplica el fix necesario para Railway y algunas versiones antiguas
    de Heroku que emiten ``postgres://`` en lugar del esquema estándar
    ``postgresql://`` que SQLAlchemy requiere.

    Returns:
        URL lista para pasarle a ``create_engine``.
    """
    url = settings.DATABASE_URL_POOLING or settings.DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


_DATABASE_URL = _resolve_database_url()

engine = create_engine(
    _DATABASE_URL,
    pool_pre_ping=True,   # descarta conexiones caídas antes de usarlas
    pool_recycle=300,     # renueva conexiones cada 5 min (evita timeouts de Supabase)
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
        Session: Sesión de SQLAlchemy. Se cierra automáticamente al finalizar
        el request, tanto en caso de éxito como de excepción.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crea todas las tablas definidas en los modelos que hereden de Base.

    Debe llamarse al iniciar la aplicación. No recrea tablas que ya existan
    (``CREATE TABLE IF NOT EXISTS`` semántico vía SQLAlchemy).
    """
    Base.metadata.create_all(bind=engine)


def test_connection() -> bool:
    """Verifica que la conexión a la base de datos esté disponible.

    Ejecuta una consulta mínima (``SELECT 1``) y reporta el resultado.
    Usado en el lifespan de FastAPI para dar feedback inmediato en el arranque.

    Returns:
        bool: ``True`` si la conexión es exitosa, ``False`` en caso de error.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("Conexión a la base de datos exitosa.")
        return True
    except Exception as e:
        print(f"Error al conectar con la base de datos: {e}")
        return False
