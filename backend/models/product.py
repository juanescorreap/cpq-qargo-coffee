from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
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
        name="Cappuccino", category="bebidas-calientes", base_size_oz=12,
        prep_time_minutes=3.5, labor_cost_per_minute=150
    """

    __tablename__ = "products"

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    name: str = Column(String(180), nullable=False)
    category: str | None = Column(
        String(80),
        ForeignKey("categories.slug", onupdate="CASCADE", ondelete="SET NULL"),
        index=True,
    )
    base_size_oz: float | None = Column(Numeric(10, 3))      # reference size for scaling
    prep_time_minutes: float | None = Column(Numeric(8, 2))
    labor_cost_per_minute: float | None = Column(Numeric(14, 4))  # price_amount
    is_sub_recipe: bool = Column(Boolean, nullable=False, default=False)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
            name="uq_product_sizes_name",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    product_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    size_name: str = Column(String(60), nullable=False)      # "small" | "medium" | "large"
    volume_oz: float | None = Column(Numeric(10, 3))
    scale_factor: float | None = Column(Numeric(14, 6), default=1.0)
    is_default: bool = Column(Boolean, nullable=False, default=False)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
    """

    __tablename__ = "recipe_ingredients"

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "ingredient_id",
            name="uq_recipe_ingredients",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    product_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: float = Column(Numeric(14, 6), nullable=False)
    recipe_unit_id: int | None = Column(
        BigInteger, ForeignKey("recipe_units.id", ondelete="SET NULL"), nullable=True
    )
    scales_with_size: bool = Column(Boolean, nullable=False, default=True)
    process_yield_loss: float | None = Column(Numeric(6, 3), default=0)
    notes: str | None = Column(Text)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RecipeSubRecipe(Base):
    """Reference to a sub-recipe (batch component) within a product's recipe.

    Allows reusing batch preparations across multiple drinks without duplicating
    ingredients. The costing engine recursively expands the sub-recipe to
    calculate the unit cost of the quantity used.
    """

    __tablename__ = "recipe_sub_recipes"

    __table_args__ = (
        UniqueConstraint(
            "parent_product_id",
            "sub_recipe_id",
            name="uq_recipe_sub_recipes",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    parent_product_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sub_recipe_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: float = Column(Numeric(14, 6), nullable=False)
    scales_with_size: bool = Column(Boolean, nullable=False, default=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SizePackaging(Base):
    """Packaging associated with a product size.

    Links packaging supplies (modeled as ingredients) to the specific size of
    a drink. Allows costing cups, lids, sleeves, straws, and napkins
    differentiated by size.
    """

    __tablename__ = "size_packaging"

    __table_args__ = (
        UniqueConstraint(
            "size_id",
            "packaging_ingredient_id",
            name="uq_size_packaging",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    size_id: int = Column(
        BigInteger, ForeignKey("product_sizes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    packaging_ingredient_id: int = Column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: float = Column(Numeric(14, 6), nullable=False, default=1)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
            name="uq_store_products",
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    store_id: int = Column(
        BigInteger, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: int = Column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    is_available: bool = Column(Boolean, nullable=False, default=True)
    seasonal_start_date: object | None = Column(Date, nullable=True)
    seasonal_end_date: object | None = Column(Date, nullable=True)
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
