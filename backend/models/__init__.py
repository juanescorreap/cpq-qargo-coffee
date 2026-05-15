"""Modelos SQLAlchemy del sistema CPQ para cafeterías.

Estructura de módulos:

    ingredient  — Ingredient, IngredientPriceHistory
                  Insumos con historial de precios de compra.

    recipe_unit — RecipeUnit, IngredientRecipeUnitConversion
                  Unidades de receta (pump, shot) y sus factores de conversión
                  a la unidad de consumo del ingrediente.

    store       — Store, StoreIngredientPrice
                  Tiendas y precios de ingredientes por tienda.

    product     — Product, ProductSize, RecipeIngredient, RecipeSubRecipe,
                  SizePackaging, StoreProduct
                  Catálogo de productos, variantes de tamaño, recetas,
                  componentes batch, packaging y disponibilidad por tienda.

    pricing     — CategoryMargin, ProductPricing, ProductPriceHistory
                  Márgenes por categoría, precios actuales con soporte de
                  override por tienda, e historial de cambios de precio.

    competitor  — Competitor, CompetitorProduct, ProductCompetitorMatch
                  Competidores monitoreados, productos scrapeados de sus menús
                  y matches manuales contra productos propios.

    modifier    — Modifier, ProductModifierCost
                  Modificaciones de receta (sustituciones, adiciones) y su
                  impacto pre-calculado en el costo por producto.

Todos los modelos heredan de `backend.database.Base`. Importar este paquete
es suficiente para que `Base.metadata` contenga todas las tablas antes de
llamar a `create_all()`.
"""

from backend.models.competitor import (
    Competitor,
    CompetitorProduct,
    ProductCompetitorMatch,
)
from backend.models.ingredient import Ingredient, IngredientPriceHistory
from backend.models.modifier import Modifier, ProductModifierCost
from backend.models.pricing import CategoryMargin, ProductPriceHistory, ProductPricing
from backend.models.product import (
    Product,
    ProductSize,
    RecipeIngredient,
    RecipeSubRecipe,
    SizePackaging,
    StoreProduct,
)
from backend.models.recipe_unit import IngredientRecipeUnitConversion, RecipeUnit
from backend.models.store import Store, StoreIngredientPrice

__all__ = [
    "CategoryMargin",
    "Competitor",
    "CompetitorProduct",
    "Ingredient",
    "IngredientPriceHistory",
    "IngredientRecipeUnitConversion",
    "Modifier",
    "Product",
    "ProductCompetitorMatch",
    "ProductModifierCost",
    "ProductPriceHistory",
    "ProductPricing",
    "ProductSize",
    "RecipeIngredient",
    "RecipeSubRecipe",
    "RecipeUnit",
    "SizePackaging",
    "Store",
    "StoreIngredientPrice",
    "StoreProduct",
]
