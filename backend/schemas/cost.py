"""Schemas para el motor de cálculo de costos.

Flujo típico:
    Cliente  →  CostCalculationRequest  →  motor de costeo
    Motor    →  CostBreakdownResponse   →  cliente

CostBreakdownResponse desglosa el costo total en cuatro categorías
independientes (ingredients, sub_recipes, packaging, labor) para que
el cliente pueda mostrar transparencia de costos al usuario final y
facilitar análisis de márgenes por categoría.
"""

from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class CostCalculationRequest(BaseModel):
    """Parámetros de entrada para calcular el costo de un producto.

    Si size_id es None el motor usa el tamaño base del producto
    (scale_factor = 1.0). Si store_id es None no se aplican
    ajustes de costo por tienda.
    """

    product_id: int
    size_id: Optional[int] = None
    store_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Detail line items
# ---------------------------------------------------------------------------

class IngredientCostDetail(BaseModel):
    """Desglose de costo por línea de ingrediente en la receta.

    unit_cost ya incluye la merma del ingrediente (yield_percentage) y
    la merma de proceso (process_yield_loss): es el costo real por
    usage_unit después de aplicar ambos factores.

    total_cost = quantity × unit_cost
    """

    name: str
    quantity: Decimal
    unit: str
    unit_cost: Decimal
    total_cost: Decimal


class SubRecipeCostDetail(BaseModel):
    """Desglose de costo por sub-receta (batch component) referenciada.

    unit_cost es el costo completo de una porción de la sub-receta,
    calculado recursivamente por el motor expandiendo sus ingredientes.

    total_cost = quantity × unit_cost
    """

    name: str
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal


class PackagingCostDetail(BaseModel):
    """Desglose de costo por ítem de packaging asociado al tamaño.

    El packaging se costea a precio de ingrediente dividido por su
    factor de conversión (unidades por caja). total_cost = quantity
    × costo unitario del empaque.
    """

    name: str
    quantity: Decimal
    total_cost: Decimal


class LaborCostDetail(BaseModel):
    """Desglose de costo de mano de obra.

    total_cost = prep_time_minutes × cost_per_minute.
    cost_per_minute proviene del campo labor_cost_per_minute del producto.
    """

    prep_time_minutes: Decimal
    cost_per_minute: Decimal
    total_cost: Decimal

    @field_validator("prep_time_minutes", "cost_per_minute")
    @classmethod
    def non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Labor cost fields must be >= 0")
        return v


# ---------------------------------------------------------------------------
# Full breakdown response
# ---------------------------------------------------------------------------

class CostBreakdownResponse(BaseModel):
    """Respuesta completa del motor de costeo para un producto y tamaño.

    totals agrupa los subtotales por categoría para acceso directo:
        {
            "ingredients": Decimal,
            "sub_recipes":  Decimal,
            "packaging":    Decimal,
            "labor":        Decimal,
            "total":        Decimal,   # suma de las cuatro categorías
        }

    total_cost == totals["total"] y se mantiene como campo de nivel
    superior para facilitar el acceso sin parsear el dict.
    """

    product_id: int
    product_name: str
    size_id: Optional[int] = None
    size_name: Optional[str] = None
    store_id: Optional[int] = None
    store_name: Optional[str] = None

    ingredients: List[IngredientCostDetail]
    sub_recipes: List[SubRecipeCostDetail]
    packaging: List[PackagingCostDetail]
    labor: Optional[LaborCostDetail] = None

    totals: Dict[str, Decimal]
    total_cost: Decimal
