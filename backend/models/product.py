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


class Product(Base):
    """Product or sub-recipe from the cafeteria catalog.

    A product can be a final beverage (e.g.: Cappuccino) or a reusable batch
    component (e.g.: "Homemade vanilla syrup") marked with is_sub_recipe=True.
    In that case other products can reference it as an ingredient in their
    recipes.

    Labor cost is calculated as prep_time_minutes * labor_cost_per_minute and
    added to the ingredient cost to obtain the total cost per portion.

    Example:
        name="Cappuccino", category="bebidas_calientes", base_size_oz=12,
        prep_time_minutes=3.5, labor_cost_per_minute=150
    """

    __tablename__ = "products"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    category: str | None = Column(String(100), index=True)  # e.g.: "bebidas_calientes"
    base_size_oz: float | None = Column(Numeric(6, 2))      # reference size for scaling
    prep_time_minutes: float | None = Column(Numeric(5, 2))
    labor_cost_per_minute: float = Column(Numeric(6, 2), default=0)
    is_sub_recipe: bool = Column(Boolean, default=False, index=True)
    is_active: bool = Column(Boolean, default=True)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now())


class ProductSize(Base):
    """Size variant of a product with its scale factor.

    The costing engine multiplies each ingredient quantity by scale_factor to
    calculate the cost for the requested size. The base size always has
    scale_factor=1.0.

    Example for Cappuccino with base_size_oz=12:
        size_name="small",  volume_oz=8,  scale_factor=0.67
        size_name="medium", volume_oz=12, scale_factor=1.0   ← base
        size_name="large",  volume_oz=16, scale_factor=1.33
    """

    __tablename__ = "product_sizes"

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "size_name",
            name="uq_product_size_name",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False, index=True
    )
    size_name: str | None = Column(String(50))      # "small" | "medium" | "large"
    volume_oz: float | None = Column(Numeric(6, 2))
    scale_factor: float = Column(Numeric(5, 3), default=1.0)
    is_default: bool = Column(Boolean, default=False)


class RecipeIngredient(Base):
    """Ingredient line within a product's recipe.

    Supports two quantity modes depending on recipe_unit_id:

    - recipe_unit_id IS NULL: quantity is expressed directly in the
      ingredient's usage_unit (e.g.: quantity=240 → 240 ml of milk).

    - recipe_unit_id IS NOT NULL: quantity is in that recipe_unit and the
      engine looks up the conversion in IngredientRecipeUnitConversion to
      translate it to usage_units before calculating the cost
      (e.g.: quantity=2, recipe_unit=pump → 2 × 30 ml = 60 ml of syrup).

    scales_with_size controls whether the quantity is multiplied by the
    scale_factor of the requested size. Set to False for ingredients that are
    fixed regardless of size:
        - espresso shots: always 2, regardless of whether it's medium or large
        - standard toppings: 1 fixed unit per drink

    process_yield_loss captures additional waste that occurs during
    preparation, distinct from the ingredient's purchase waste:
        - milk foam: 10% of the volume is lost when steaming
        - fruit for juice: 20% loss when pressing

    Examples:
        "2 pumps of vanilla syrup" → quantity=2, recipe_unit_id=[pump_id]
        "240 ml of milk"           → quantity=240, recipe_unit_id=None
        "2 espresso shots"         → quantity=2, recipe_unit_id=[shot_id],
                                     scales_with_size=False
    """

    __tablename__ = "recipe_ingredients"

    id: int = Column(Integer, primary_key=True, index=True)
    product_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False, index=True
    )
    ingredient_id: int = Column(
        Integer, ForeignKey("ingredients.id"), nullable=False
    )
    quantity: float = Column(Numeric(10, 4), nullable=False)
    recipe_unit_id: int | None = Column(
        Integer, ForeignKey("recipe_units.id"), nullable=True
    )
    scales_with_size: bool = Column(Boolean, default=True)
    process_yield_loss: float = Column(Numeric(5, 2), default=0)
    notes: str | None = Column(Text)


class RecipeSubRecipe(Base):
    """Reference to a sub-recipe (batch component) within a product's recipe.

    Allows reusing batch preparations across multiple drinks without duplicating
    ingredients. The costing engine recursively expands the sub-recipe to
    calculate the unit cost of the quantity used.

    Example:
        "Homemade vanilla syrup" (is_sub_recipe=True) used in:
            - Vanilla Latte       → quantity=2 (pumps resolved in the sub-recipe)
            - Vanilla Frappuccino → quantity=3
    """

    __tablename__ = "recipe_sub_recipes"

    id: int = Column(Integer, primary_key=True, index=True)
    parent_product_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False, index=True
    )
    sub_recipe_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False
    )
    quantity: float = Column(Numeric(10, 4), nullable=False)
    scales_with_size: bool = Column(Boolean, default=True)


class SizePackaging(Base):
    """Packaging associated with a product size.

    Links packaging supplies (modeled as ingredients) to the specific size of
    a drink. Allows costing cups, lids, sleeves, straws, and napkins
    differentiated by size.

    Example:
        Medium Cappuccino (12 oz):
            packaging_ingredient="Kraft cup 12oz", quantity=1
            packaging_ingredient="Flat lid",        quantity=1
            packaging_ingredient="Cardboard sleeve", quantity=1
    """

    __tablename__ = "size_packaging"

    id: int = Column(Integer, primary_key=True, index=True)
    size_id: int = Column(
        Integer, ForeignKey("product_sizes.id"), nullable=False, index=True
    )
    packaging_ingredient_id: int = Column(
        Integer, ForeignKey("ingredients.id"), nullable=False
    )
    quantity: float = Column(Numeric(6, 2), default=1)


class StoreProduct(Base):
    """Availability of a product at a specific store.

    Allows managing:
    - Per-store menu: not all stores offer all products.
    - Seasonal products: available only within a date range
      (e.g.: Pumpkin Spice Latte between October and December).

    When seasonal_start_date and seasonal_end_date are NULL the product
    is permanently available if is_available=True.
    """

    __tablename__ = "store_products"

    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "product_id",
            name="uq_store_product",
        ),
    )

    id: int = Column(Integer, primary_key=True, index=True)
    store_id: int = Column(
        Integer, ForeignKey("stores.id"), nullable=False, index=True
    )
    product_id: int = Column(
        Integer, ForeignKey("products.id"), nullable=False
    )
    is_available: bool = Column(Boolean, default=True)
    seasonal_start_date: object | None = Column(Date, nullable=True)
    seasonal_end_date: object | None = Column(Date, nullable=True)
