"""Shared pytest fixtures for the CPQ backend test suite.

Isolation strategy:
    Each test receives a session wrapped in a transaction that is rolled back
    on completion.  This guarantees that data created by one test does not
    contaminate subsequent ones and that the database is left in the same
    state as before the suite ran.

    Per-test flow:
        1. A connection is opened and a transaction started (BEGIN).
        2. A SQLAlchemy session is created bound to that connection.
        3. The test runs and may issue partial commits within the session;
           all of them stay inside the outer transaction.
        4. When the test finishes, ROLLBACK is issued on the outer transaction,
           undoing everything written during the test.

Usage:
    Data fixtures (sample_ingredient, sample_product, etc.) depend on
    `test_db` and are therefore also rolled back automatically.  There is no
    need to clean up anything manually in the tests.
"""

from decimal import Decimal

import pytest

from backend.database import engine
from backend.models import Ingredient, Product, ProductSize, RecipeIngredient
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Database session with automatic rollback
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """Isolated SQLAlchemy session with automatic rollback at the end of the test.

    Wraps the entire test in a database transaction that is rolled back on
    exit from the fixture, regardless of whether the test passes or fails.
    Commits issued inside the test stay inside the outer transaction and are
    never persisted to the real DB.

    Yields:
        Session: SQLAlchemy session ready to use.
    """
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)

    # Ensures that commits inside the test do not close the outer transaction
    db.begin_nested()

    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_ingredient(test_db: Session) -> Ingredient:
    """Test ingredient: whole milk in a 1 L carton.

    Representative domain values so that cost calculations in the tests
    produce verifiable numbers:
        - purchase_price=4500 COP / 1 000 ml carton
        - unit_cost = 4.5 COP/ml
        - yield 95 % → effective cost ≈ 4.74 COP/ml

    Args:
        test_db: Test session with automatic rollback.

    Returns:
        Persisted Ingredient (with id assigned by the DB).
    """
    ingredient = Ingredient(
        name="Test Whole Milk",
        category="dairy",
        purchase_unit="Box 1L",
        purchase_price=Decimal("4500"),
        usage_unit="ml",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("0.95"),
    )
    test_db.add(ingredient)
    test_db.commit()
    test_db.refresh(ingredient)
    return ingredient


@pytest.fixture
def sample_product(test_db: Session) -> Product:
    """Test product: base Cappuccino with no sizes or recipe.

    Includes preparation time and labor cost so that
    `_calculate_labor_cost` returns a non-zero value:
        - prep_time_minutes=3, labor_cost_per_minute=200
        - labor_cost = 600 COP

    Args:
        test_db: Test session with automatic rollback.

    Returns:
        Persisted Product (with id assigned by the DB).
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
    """Base size (medium 12 oz) for `sample_product`.

    scale_factor=1.0 and is_default=True make it the size that
    `calculate_product_cost` resolves when size_id=None.

    Args:
        test_db: Test session with automatic rollback.
        sample_product: Product to which the size is associated.

    Returns:
        Persisted ProductSize (with id assigned by the DB).
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
    """Recipe line: 240 ml of milk in the test Cappuccino.

    Configured with scales_with_size=True and no recipe_unit (quantity
    directly in ml) to exercise the simplest path of
    `_calculate_ingredient_cost`.

    Expected cost with `sample_ingredient` values (no store):
        unit_cost = 4 500 / 1 000 = 4.5 COP/ml
        qty_yield = 240 / 0.95  ≈ 252.63 ml
        line_cost ≈ 1 136.84 COP

    Args:
        test_db: Test session with automatic rollback.
        sample_product: Parent product of the recipe.
        sample_ingredient: Ingredient added to the recipe.

    Returns:
        Persisted RecipeIngredient (with id assigned by the DB).
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
