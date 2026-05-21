"""SQLAlchemy models for the CPQ cafeteria system.

Module structure:

    ingredient  — Ingredient, IngredientPriceHistory
                  Ingredients with purchase price history.

    recipe_unit — RecipeUnit, IngredientRecipeUnitConversion
                  Recipe units (pump, shot) and their conversion factors
                  to the ingredient's consumption unit.

    store       — Store, StoreIngredientPrice
                  Stores and ingredient prices per store.

    product     — Product, ProductSize, RecipeIngredient, RecipeSubRecipe,
                  SizePackaging, StoreProduct
                  Product catalog, size variants, recipes,
                  batch components, packaging and availability per store.

    pricing     — CategoryMargin, ProductPricing, ProductPriceHistory
                  Per-category margins, current prices with per-store override
                  support, and price change history.

    competitor  — Competitor, CompetitorProduct, ProductCompetitorMatch
                  Monitored competitors, products scraped from their menus,
                  and manual matches against own products.

    modifier    — Modifier, ProductModifierCost
                  Recipe modifications (substitutions, additions) and their
                  pre-calculated cost impact per product.

All models inherit from `backend.database.Base`. Importing this package
is sufficient for `Base.metadata` to contain all tables before calling
`create_all()`.
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
