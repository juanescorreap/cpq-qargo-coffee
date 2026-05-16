"""Fixtures de pytest compartidas para el suite de tests del backend CPQ.

Estrategia de aislamiento:
    Cada test recibe una sesión envuelta en una transacción que se revierte al
    finalizar. Esto garantiza que los datos creados por un test no contaminen
    los siguientes y que la base de datos quede en el mismo estado que antes de
    ejecutar la suite.

    Flujo por test:
        1. Se abre una conexión y se inicia una transacción (BEGIN).
        2. Se crea una sesión SQLAlchemy enlazada a esa conexión.
        3. El test corre y puede hacer commits parciales dentro de la sesión;
           todos quedan dentro de la transacción externa.
        4. Al finalizar el test se hace ROLLBACK sobre la transacción externa,
           deshaciendo todo lo escrito durante el test.

Uso:
    Los fixtures de datos (sample_ingredient, sample_product, etc.) dependen de
    `test_db` y por tanto también se revierten automáticamente. No es necesario
    limpiar nada a mano en los tests.
"""

from decimal import Decimal

import pytest

from backend.database import engine
from backend.models import Ingredient, Product, ProductSize, RecipeIngredient
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Sesión de base de datos con rollback automático
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """Sesión SQLAlchemy aislada con rollback automático al finalizar el test.

    Envuelve el test completo en una transacción de base de datos que se
    revierte al salir del fixture, sin importar si el test pasa o falla.
    Los commits emitidos dentro del test quedan dentro de la transacción
    externa y no llegan a persistirse en la BD real.

    Yields:
        Session: Sesión SQLAlchemy lista para usar.
    """
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)

    # Asegura que los commits dentro del test no cierren la transacción externa
    db.begin_nested()

    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Fixtures de datos
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_ingredient(test_db: Session) -> Ingredient:
    """Ingrediente de prueba: leche entera en caja de 1 L.

    Valores representativos del dominio real para que los cálculos de costo
    en los tests produzcan números verificables:
        - purchase_price=4500 COP / caja 1 000 ml
        - unit_cost = 4.5 COP/ml
        - yield 95 % → costo efectivo ≈ 4.74 COP/ml

    Args:
        test_db: Sesión de prueba con rollback automático.

    Returns:
        Ingredient persistido (con id asignado por la BD).
    """
    ingredient = Ingredient(
        name="Test Whole Milk",
        category="dairy",
        purchase_unit="Box 1L",
        purchase_price=Decimal("4500"),
        usage_unit="ml",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("95"),
    )
    test_db.add(ingredient)
    test_db.commit()
    test_db.refresh(ingredient)
    return ingredient


@pytest.fixture
def sample_product(test_db: Session) -> Product:
    """Producto de prueba: Cappuccino base sin tamaños ni receta.

    Incluye tiempo de preparación y costo de mano de obra para que
    `_calculate_labor_cost` retorne un valor distinto de cero:
        - prep_time_minutes=3, labor_cost_per_minute=200
        - labor_cost = 600 COP

    Args:
        test_db: Sesión de prueba con rollback automático.

    Returns:
        Product persistido (con id asignado por la BD).
    """
    product = Product(
        name="Test Cappuccino",
        category="hot_beverages",
        base_size_oz=Decimal("12"),
        prep_time_minutes=Decimal("3"),
        labor_cost_per_minute=Decimal("200"),
        is_sub_recipe=False,
    )
    test_db.add(product)
    test_db.commit()
    test_db.refresh(product)
    return product


@pytest.fixture
def sample_size(test_db: Session, sample_product: Product) -> ProductSize:
    """Tamaño base (mediano 12 oz) para `sample_product`.

    scale_factor=1.0 e is_default=True lo convierte en el tamaño que
    `calculate_product_cost` resuelve cuando size_id=None.

    Args:
        test_db: Sesión de prueba con rollback automático.
        sample_product: Producto al que se asocia el tamaño.

    Returns:
        ProductSize persistido (con id asignado por la BD).
    """
    size = ProductSize(
        product_id=sample_product.id,
        size_name="medium",
        volume_oz=Decimal("12"),
        scale_factor=Decimal("1.0"),
        is_default=True,
    )
    test_db.add(size)
    test_db.commit()
    test_db.refresh(size)
    return size


@pytest.fixture
def sample_recipe(
    test_db: Session,
    sample_product: Product,
    sample_ingredient: Ingredient,
) -> RecipeIngredient:
    """Línea de receta: 240 ml de leche en el Cappuccino de prueba.

    Configurada con scales_with_size=True y sin recipe_unit (cantidad
    directa en ml) para ejercitar el camino más simple de
    `_calculate_ingredient_cost`.

    Costo esperado con los valores de `sample_ingredient` (sin tienda):
        unit_cost = 4 500 / 1 000 = 4.5 COP/ml
        qty_yield = 240 / 0.95  ≈ 252.63 ml
        line_cost ≈ 1 136.84 COP

    Args:
        test_db: Sesión de prueba con rollback automático.
        sample_product: Producto padre de la receta.
        sample_ingredient: Ingrediente que se agrega a la receta.

    Returns:
        RecipeIngredient persistido (con id asignado por la BD).
    """
    recipe_line = RecipeIngredient(
        product_id=sample_product.id,
        ingredient_id=sample_ingredient.id,
        quantity=Decimal("240"),
        recipe_unit_id=None,
        scales_with_size=True,
        process_yield_loss=Decimal("0"),
    )
    test_db.add(recipe_line)
    test_db.commit()
    test_db.refresh(recipe_line)
    return recipe_line
