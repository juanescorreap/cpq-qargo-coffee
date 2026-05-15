from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.sql import func

from backend.database import Base


class Ingredient(Base):
    """Representa un ingrediente o insumo del catálogo de compras.

    Almacena tanto la información de compra (unidad y precio por la que se
    adquiere el proveedor) como la de uso en recetas (unidad y factor de
    conversión), permitiendo calcular el costo real por porción.

    El campo yield_percentage captura la merma: un ingrediente con 80 % de
    aprovechamiento incrementa su costo efectivo en un 25 %.
    """

    __tablename__ = "ingredients"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    category: str | None = Column(String(100), index=True)

    # --- Unidad de compra (cómo llega del proveedor) ---
    purchase_unit: str | None = Column(String(50))          # ej: "caja 1L"
    purchase_price: float | None = Column(Numeric(10, 2))   # precio por purchase_unit

    # --- Unidad de uso en recetas ---
    usage_unit: str | None = Column(String(50))             # ej: "ml"
    conversion_factor: float | None = Column(Numeric(10, 4))  # usage_units por purchase_unit

    # --- Merma y aprovechamiento ---
    yield_percentage: float = Column(Numeric(5, 2), default=100.00)

    # --- Scraping ---
    source_url: str | None = Column(Text)
    last_scraped: object | None = Column(DateTime(timezone=True))

    # --- Control ---
    is_active: bool = Column(Boolean, default=True, index=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )


class IngredientPriceHistory(Base):
    """Historial de cambios de precio de un ingrediente.

    Cada fila registra un precio puntual junto con su origen: puede
    provenir de un scraping automático, de una edición manual o de
    una carga masiva. Permite auditar la evolución de costos a lo largo
    del tiempo y detectar variaciones de proveedores.
    """

    __tablename__ = "ingredient_price_history"

    id: int = Column(Integer, primary_key=True, index=True)
    ingredient_id: int = Column(
        Integer, ForeignKey("ingredients.id"), nullable=False, index=True
    )
    price: float = Column(Numeric(10, 2), nullable=False)
    source: str | None = Column(String(50))  # 'scraping' | 'manual' | 'bulk_upload'
    changed_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )
