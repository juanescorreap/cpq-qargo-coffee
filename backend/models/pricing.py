from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from backend.database import Base


class CategoryMargin(Base):
    """Markup por defecto para cada categoría de producto.

    El motor de precios consulta esta tabla cuando un producto no tiene
    markup_override en ProductPricing. La categoría debe coincidir
    exactamente con el campo category de Product.

    Ejemplo:
        category="bebidas_calientes", markup_percentage=65.00
        category="bebidas_frias",     markup_percentage=70.00
        category="alimentos",         markup_percentage=55.00
    """

    __tablename__ = "category_margins"

    id: int = Column(Integer, primary_key=True, index=True)
    category: str = Column(String(100), unique=True, nullable=False)
    markup_percentage: float = Column(Numeric(5, 2), nullable=False)
    notes: str | None = Column(Text)


class ProductPricing(Base):
    """Precio actual de un producto para un tamaño y tienda dados.

    store_id nullable permite definir un precio global (NULL) que aplica
    a todas las tiendas, o un precio específico por tienda que lo sobreescribe.

    El motor calcula calculated_cost a partir de la receta y lo almacena aquí
    para auditoría. El precio final se determina así:
        - Si is_manual_price=True: se usa final_price tal como fue ingresado.
        - Si markup_override IS NOT NULL: final_price = calculated_cost * (1 + markup_override/100).
        - Si markup_override IS NULL: se usa el markup de CategoryMargin para la categoría.

    effective_date permite programar cambios de precio a futuro: el motor
    siempre toma el registro con la effective_date más reciente <= hoy.

    Ejemplo:
        product_id=5 (Cappuccino), size_id=2 (mediano), store_id=NULL,
        calculated_cost=12.50, markup_override=NULL, final_price=28.00
    """

    __tablename__ = "product_pricing"

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "size_id",
            "store_id",
            "effective_date",
            name="uq_product_pricing",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False, index=True
    )
    size_id: int = Column(Integer, ForeignKey("product_sizes.id"), nullable=False)
    store_id: int | None = Column(
        Integer, ForeignKey("stores.id"), nullable=True, index=True
    )
    calculated_cost: float = Column(Numeric(10, 2), nullable=False)
    markup_override: float | None = Column(Numeric(5, 2), nullable=True)
    final_price: float = Column(Numeric(10, 2), nullable=False)
    is_manual_price: bool = Column(Boolean, default=False)
    effective_date: object = Column(
        Date, nullable=False, server_default=func.current_date()
    )


class ProductPriceHistory(Base):
    """Historial de cambios de precio para análisis y auditoría.

    Cada vez que ProductPricing se actualiza, el motor inserta una fila aquí
    con el snapshot completo. Permite reconstruir la evolución de costos y
    márgenes en el tiempo para reportes de rentabilidad.

    markup_used registra el markup efectivamente aplicado (ya sea el override
    o el de CategoryMargin) para que el historial sea autocontenido sin
    necesidad de recalcular.

    Ejemplo de uso:
        Analizar cómo subió el costo del Cappuccino durante un trimestre
        por alza en el precio del café, y cuándo se ajustó el precio final.
    """

    __tablename__ = "product_price_history"

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(Integer, ForeignKey("products.id"), nullable=False)
    size_id: int = Column(Integer, ForeignKey("product_sizes.id"), nullable=False)
    store_id: int | None = Column(
        Integer, ForeignKey("stores.id"), nullable=True
    )
    cost: float = Column(Numeric(10, 2), nullable=False)
    price: float = Column(Numeric(10, 2), nullable=False)
    markup_used: float = Column(Numeric(5, 2), nullable=False)
    changed_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )
