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

from fastapi.testclient import TestClient

from backend.database import engine, get_db
from backend.models import Ingredient, Product, ProductSize, RecipeIngredient
from backend.models.category import Category
from backend.models.supply_chain import (
    Distributor,
    Manufacturer,
    Region,
    SupplyRoute,
    SupplyRouteAssignment,
)
from backend.models.store import Store
from sqlalchemy.orm import Session


# Category slugs referenced by product fixtures across the suite. products.category
# is now a FK to categories(slug), so these rows must exist before any product is
# created. Seeded inside each test's rolled-back transaction.
_TEST_CATEGORY_SLUGS = (
    "hot_beverages",
    "cold_beverages",
    "food",
    "bebidas_calientes",
    "bebidas_frias",
    "alimentos",
    "otros",
)


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
# Category seeding (products.category is a FK to categories(slug))
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def seed_categories(test_db: Session) -> None:
    """Ensure the category slugs used by product fixtures exist.

    Runs automatically for every test that uses ``test_db``; the inserts live
    inside the test's outer transaction and are rolled back with it.
    """
    existing = {c.slug for c in test_db.query(Category.slug).all()}
    new = [Category(slug=s) for s in _TEST_CATEGORY_SLUGS if s not in existing]
    if new:
        test_db.add_all(new)
        test_db.commit()


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


# ---------------------------------------------------------------------------
# TestClient with test_db dependency override
# ---------------------------------------------------------------------------

@pytest.fixture
def test_client(test_db: Session):
    """FastAPI TestClient wired to the isolated test session.

    All HTTP requests made through this client share the same SQLAlchemy
    session as `test_db`, so data created in either is visible to both and
    everything is rolled back automatically at the end of the test.
    """
    import backend.main as main
    from backend.main import app

    def _override_get_db():
        yield test_db

    # The app's startup event runs init_db() (Base.metadata.create_all) and
    # test_connection() on every TestClient context-enter. Against the remote
    # Supabase pooler that reflects ~49 tables per test (~10s each), which blows
    # the suite's time budget. The schema is owned by Alembic, so skip both in
    # tests — patch the names main.py imported into its own namespace.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "test_connection", lambda: True)

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        monkeypatch.undo()


# ---------------------------------------------------------------------------
# Supply chain base fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sc_region(test_db: Session) -> Region:
    region = Region(name="Bogotá", code="BOG-TEST", country_code="CO")
    test_db.add(region)
    test_db.commit()
    test_db.refresh(region)
    return region


@pytest.fixture
def sc_manufacturer(test_db: Session) -> Manufacturer:
    manufacturer = Manufacturer(name="Lácteos Test S.A.", country_code="CO", tax_id="900123456-1")
    test_db.add(manufacturer)
    test_db.commit()
    test_db.refresh(manufacturer)
    return manufacturer


@pytest.fixture
def sc_distributor(test_db: Session) -> Distributor:
    distributor = Distributor(
        name="Distribuidor Norte Test",
        country_code="CO",
        contact_email="norte@test.com",
    )
    test_db.add(distributor)
    test_db.commit()
    test_db.refresh(distributor)
    return distributor


@pytest.fixture
def sc_ingredient(test_db: Session) -> Ingredient:
    ingredient = Ingredient(
        name="Leche Entera Test SC",
        purchase_price=Decimal("4500"),
        usage_unit="ml",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("1.00"),
    )
    test_db.add(ingredient)
    test_db.commit()
    test_db.refresh(ingredient)
    return ingredient


@pytest.fixture
def sc_supply_route(
    test_db: Session, sc_ingredient: Ingredient, sc_manufacturer: Manufacturer
) -> SupplyRoute:
    route = SupplyRoute(
        ingredient_id=sc_ingredient.id,
        manufacturer_id=sc_manufacturer.id,
        is_direct=False,
        is_active=True,
    )
    test_db.add(route)
    test_db.commit()
    test_db.refresh(route)
    return route


@pytest.fixture
def sc_store(test_db: Session, sc_region: Region) -> Store:
    store = Store(
        code="SC-TEST-01",
        name="Tienda Test Supply Chain",
        city="Bogotá",
        region_id=sc_region.id,
    )
    test_db.add(store)
    test_db.commit()
    test_db.refresh(store)
    return store


@pytest.fixture
def sc_assignment(
    test_db: Session, sc_supply_route: SupplyRoute, sc_region: Region
) -> SupplyRouteAssignment:
    from datetime import date
    assignment = SupplyRouteAssignment(
        supply_route_id=sc_supply_route.id,
        region_id=sc_region.id,
        priority=1,
        valid_from=date.today(),
        assigned_by="conftest",
    )
    test_db.add(assignment)
    test_db.commit()
    test_db.refresh(assignment)
    return assignment
