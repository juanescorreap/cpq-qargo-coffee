"""Phase 1 structural guarantees for the cost engine (ENGINE_SUPPLIER_PLAN_V2 §1).

These tests assert the *structural* wins of the rewrite — they complement the
numeric regression tests in test_cost_calculator.py (which prove the numbers did
not change):

  1. Memoized BOM DAG: a sub-recipe shared by several parents (diamond) is
     valued exactly once, not re-expanded per path.
  2. Query budget: a bulk batch issues a number of SQL statements that does NOT
     grow with the number of products (no N+1 in the hot loop).
"""

import collections
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

import backend.services.cost_calculator as cc
from backend.database import engine
from backend.models import (
    Ingredient,
    Product,
    ProductSize,
    RecipeIngredient,
    RecipeSubRecipe,
)
from backend.services.cost_calculator import CostCalculator
from backend.services.pricing_engine import PricingEngine


def _ingredient(db: Session, name: str, price: str) -> Ingredient:
    ing = Ingredient(
        name=name,
        purchase_price=Decimal(price),
        usage_unit="g",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("1.00"),
    )
    db.add(ing)
    db.commit()
    return ing


def test_shared_subrecipe_valued_once(test_db: Session, monkeypatch):
    """Diamond BOM: P -> A, P -> B, A -> C, B -> C.

    The leaf sub-recipe C must be valued a single time thanks to the memo,
    even though two paths (via A and via B) reach it.
    """
    c_ing = _ingredient(test_db, "Phase1 Leaf Ing", "10000")

    p = Product(name="Phase1 P", is_sub_recipe=False)
    a = Product(name="Phase1 A", is_sub_recipe=True)
    b = Product(name="Phase1 B", is_sub_recipe=True)
    c = Product(name="Phase1 C", is_sub_recipe=True)
    test_db.add_all([p, a, b, c])
    test_db.commit()

    # C consumes the leaf ingredient.
    test_db.add(RecipeIngredient(
        product_id=c.id, ingredient_id=c_ing.id,
        quantity=Decimal("100"), scales_with_size=False,
        process_yield_loss=Decimal("0"),
    ))
    # Diamond edges.
    test_db.add_all([
        RecipeSubRecipe(parent_product_id=p.id, sub_recipe_id=a.id,
                        quantity=Decimal("1"), scales_with_size=False),
        RecipeSubRecipe(parent_product_id=p.id, sub_recipe_id=b.id,
                        quantity=Decimal("1"), scales_with_size=False),
        RecipeSubRecipe(parent_product_id=a.id, sub_recipe_id=c.id,
                        quantity=Decimal("1"), scales_with_size=False),
        RecipeSubRecipe(parent_product_id=b.id, sub_recipe_id=c.id,
                        quantity=Decimal("1"), scales_with_size=False),
    ])
    test_db.commit()

    # Count how many times C's leaf line is actually computed.
    calls: collections.Counter = collections.Counter()
    original = cc._PureCalculator._line_base_cost

    def spy(self, line):
        calls[line.ingredient_id] += 1
        return original(self, line)

    monkeypatch.setattr(cc._PureCalculator, "_line_base_cost", spy)

    cost = CostCalculator(test_db).calculate_product_cost(p.id)

    # Leaf computed exactly once (memo), not twice (one per diamond path).
    assert calls[c_ing.id] == 1
    # And the value is still correct: 10000/1000 * 100 = 1000 per path,
    # contributed once from each of A and B -> 2000.
    assert abs(cost - Decimal("2000")) < Decimal("1")


def test_batch_query_count_does_not_scale_with_products(test_db: Session):
    """A bulk batch must not issue O(products x sizes) queries.

    We run calculate_all_prices over a small catalog and a larger one and assert
    the statement count stays flat (bulk prefetch), proving the N+1 is gone.
    """
    def build_catalog(n: int) -> list:
        ids = []
        for k in range(n):
            ing = _ingredient(test_db, f"QC Ing {n}-{k}", "5000")
            prod = Product(name=f"QC Prod {n}-{k}", is_active=True, is_sub_recipe=False)
            test_db.add(prod)
            test_db.commit()
            test_db.add(ProductSize(
                product_id=prod.id, size_name="medium",
                scale_factor=Decimal("1.0"), is_default=True,
            ))
            test_db.add(RecipeIngredient(
                product_id=prod.id, ingredient_id=ing.id,
                quantity=Decimal("100"), scales_with_size=True,
                process_yield_loss=Decimal("0"),
            ))
            test_db.commit()
            ids.append(prod.id)
        return ids

    def count_statements(fn):
        counter = {"n": 0}

        def before(conn, cursor, statement, params, context, executemany):
            counter["n"] += 1

        event.listen(engine, "before_cursor_execute", before)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return counter["n"]

    build_catalog(3)
    engine_obj = PricingEngine(test_db)
    small = count_statements(lambda: engine_obj.calculate_all_prices(store_id=None, save_to_db=False))

    build_catalog(9)  # 3x the products
    large = count_statements(lambda: engine_obj.calculate_all_prices(store_id=None, save_to_db=False))

    # Bulk prefetch => statement count is constant w.r.t. catalog size.
    # Allow a tiny slack but it must NOT grow ~linearly with products.
    assert large <= small + 2, f"query count scaled with products: small={small} large={large}"
