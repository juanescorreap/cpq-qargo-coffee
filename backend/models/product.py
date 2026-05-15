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
    """Producto o sub-receta del catálogo de la cafetería.

    Un producto puede ser una bebida final (ej: Cappuccino) o un componente
    batch reutilizable (ej: "Syrup de vainilla casero") marcado con
    is_sub_recipe=True. En ese caso otros productos pueden referenciarlo
    como ingrediente en sus recetas.

    El costo de labor se calcula como prep_time_minutes * labor_cost_per_minute
    y se suma al costo de ingredientes para obtener el costo total por porción.

    Ejemplo:
        name="Cappuccino", category="bebidas_calientes", base_size_oz=12,
        prep_time_minutes=3.5, labor_cost_per_minute=150
    """

    __tablename__ = "products"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String(200), nullable=False)
    category: str | None = Column(String(100), index=True)  # ej: "bebidas_calientes"
    base_size_oz: float | None = Column(Numeric(6, 2))      # tamaño de referencia para scaling
    prep_time_minutes: float | None = Column(Numeric(5, 2))
    labor_cost_per_minute: float = Column(Numeric(6, 2), default=0)
    is_sub_recipe: bool = Column(Boolean, default=False, index=True)
    is_active: bool = Column(Boolean, default=True)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now())


class ProductSize(Base):
    """Variante de tamaño de un producto con su factor de escala.

    El motor de costeo multiplica cada cantidad de ingrediente por scale_factor
    para calcular el costo del tamaño solicitado. El tamaño base siempre tiene
    scale_factor=1.0.

    Ejemplo para Cappuccino con base_size_oz=12:
        size_name="pequeño", volume_oz=8,  scale_factor=0.67
        size_name="mediano", volume_oz=12, scale_factor=1.0   ← base
        size_name="grande",  volume_oz=16, scale_factor=1.33
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
    size_name: str | None = Column(String(50))      # "pequeño" | "mediano" | "grande"
    volume_oz: float | None = Column(Numeric(6, 2))
    scale_factor: float = Column(Numeric(5, 3), default=1.0)
    is_default: bool = Column(Boolean, default=False)


class RecipeIngredient(Base):
    """Línea de ingrediente dentro de la receta de un producto.

    Soporta dos modos de cantidad según recipe_unit_id:

    - recipe_unit_id IS NULL: quantity está expresada directamente en la
      usage_unit del ingrediente (ej: quantity=240 → 240 ml de leche).

    - recipe_unit_id IS NOT NULL: quantity está en esa recipe_unit y el
      motor busca la conversión en IngredientRecipeUnitConversion para
      traducirla a usage_units antes de calcular el costo
      (ej: quantity=2, recipe_unit=pump → 2 × 30 ml = 60 ml de jarabe).

    scales_with_size controla si la cantidad se multiplica por el
    scale_factor del tamaño pedido. Usarlo en False para ingredientes
    fijos independientemente del tamaño:
        - shots de espresso: siempre 2, sin importar si es mediano o grande
        - toppings estándar: 1 unidad fija por bebida

    process_yield_loss captura merma adicional que ocurre durante la
    preparación, distinta a la merma de compra del ingrediente:
        - espuma de leche: 10 % del volumen se pierde al vaporizar
        - fruta para jugo: 20 % de pérdida al exprimir

    Ejemplos:
        "2 pumps de jarabe de vainilla" → quantity=2, recipe_unit_id=[pump_id]
        "240 ml de leche"               → quantity=240, recipe_unit_id=None
        "2 shots de espresso"           → quantity=2, recipe_unit_id=[shot_id],
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
    """Referencia a una sub-receta (batch component) dentro de la receta de un producto.

    Permite reutilizar preparaciones batch en múltiples bebidas sin duplicar
    ingredientes. El motor de costeo expande la sub-receta recursivamente para
    calcular el costo unitario de la cantidad usada.

    Ejemplo:
        "Jarabe de vainilla casero" (is_sub_recipe=True) usado en:
            - Vanilla Latte      → quantity=2 (pumps resueltos en la sub-receta)
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
    """Packaging asociado a un tamaño de producto.

    Vincula insumos de empaque (modelados como ingredientes) al tamaño específico
    de una bebida. Permite costear vasos, tapas, mangas, pajillas y servilletas
    de forma diferenciada por tamaño.

    Ejemplo:
        Cappuccino mediano (12 oz):
            packaging_ingredient="Vaso kraft 12oz", quantity=1
            packaging_ingredient="Tapa plana",       quantity=1
            packaging_ingredient="Manga cartón",     quantity=1
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
    """Disponibilidad de un producto en una tienda específica.

    Permite gestionar:
    - Menú por tienda: no todas las tiendas ofrecen todos los productos.
    - Productos estacionales: disponibles solo en un rango de fechas
      (ej: Pumpkin Spice Latte entre octubre y diciembre).

    Cuando seasonal_start_date y seasonal_end_date son NULL el producto
    está disponible de forma permanente si is_available=True.
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
