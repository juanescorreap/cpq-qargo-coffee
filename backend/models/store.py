from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from backend.database import Base


class Store(Base):
    """Representa una tienda o punto de venta de la cadena de cafeterías."""

    __tablename__ = "stores"

    id: int = Column(Integer, primary_key=True, index=True)
    code: str = Column(String(20), unique=True, nullable=False)  # ej: "BOG-ZONA-T"
    name: str = Column(String(200), nullable=False)
    city: str | None = Column(String(100))
    is_active: bool = Column(Boolean, default=True)


class StoreIngredientPrice(Base):
    """Precio local de un ingrediente para una tienda específica.

    Permite tres cosas:
    - Precios diferentes por tienda: cada local puede tener un precio
      distinto para el mismo ingrediente según su proveedor regional.
    - Tracking de proveedores locales: registra quién suministra el
      ingrediente en esa tienda, independiente del proveedor base.
    - Override del precio base: cuando existe un registro aquí, el motor
      de costeo usa local_price en lugar del purchase_price global del
      ingrediente.
    """

    __tablename__ = "store_ingredient_prices"

    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "ingredient_id",
            name="uq_store_ingredient",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    store_id: int = Column(
        Integer, ForeignKey("stores.id"), nullable=False, index=True
    )
    ingredient_id: int = Column(
        Integer, ForeignKey("ingredients.id"), nullable=False
    )
    local_price: float | None = Column(Numeric(10, 2))
    local_supplier: str | None = Column(String(200))
    updated_at: object = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
