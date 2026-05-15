from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.sql import func

from backend.database import Base


class Modifier(Base):
    """Modificación aplicable a un producto que afecta costo e ingredientes.

    Cada fila representa un cambio atómico sobre un ingrediente específico.
    Una sustitución compleja (ej: cambiar leche normal por leche de almendras)
    requiere DOS registros: uno que resta la leche normal y otro que suma la
    leche de almendras.

    Tipos soportados:
        - 'substitution': reemplaza un ingrediente por otro (par de registros).
        - 'addition':     agrega un ingrediente extra a la receta base.
        - 'extra_shot':   atajo semántico para shots adicionales de espresso.

    quantity_change usa la misma unidad que usage_unit del ingrediente.
    Valores negativos restan, positivos suman.

    Ejemplos:
        "Leche de almendras en vez de normal" → dos registros:
            affects_ingredient_id=leche_normal,    quantity_change=-240
            affects_ingredient_id=leche_almendras, quantity_change=+240

        "Shot extra de espresso" →
            affects_ingredient_id=espresso, quantity_change=+1,
            type='extra_shot'
    """

    __tablename__ = "modifiers"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    type: str | None = Column(String(50))
    affects_ingredient_id: int | None = Column(
        Integer, ForeignKey("ingredients.id"), nullable=True
    )
    quantity_change: float | None = Column(Numeric(10, 4))
    is_active: bool = Column(Boolean, default=True)


class ProductModifierCost(Base):
    """Impacto en costo de aplicar un modifier a un producto específico.

    El motor de costeo pre-calcula el delta de costo de cada modifier por
    producto y lo almacena aquí. Esto evita recalcular en tiempo de consulta
    y permite auditar cómo cambia el impacto cuando varían los precios de
    ingredientes.

    calculated_at registra cuándo se hizo el último cálculo, facilitando
    detectar registros desactualizados tras una actualización de precios.
    """

    __tablename__ = "product_modifier_costs"

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(Integer, ForeignKey("products.id"), nullable=False)
    modifier_id: int = Column(Integer, ForeignKey("modifiers.id"), nullable=False)
    cost_impact: float = Column(Numeric(10, 2), nullable=False)
    calculated_at: object = Column(
        DateTime(timezone=True), server_default=func.now()
    )
