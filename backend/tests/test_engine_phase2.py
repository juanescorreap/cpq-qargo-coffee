"""Phase 2: supplier sourcing in the cost (ENGINE_SUPPLIER_PLAN_V2 §1 Fase 2 / B1).

Verifies the engine now sources price + unit + provenance from
fn_resolve_ingredient_sourcing:

  1. When the price comes from a route AND the supplier pack->recipe conversion
     is known, that SUPPLIER conversion overrides ingredient.conversion_factor.
  2. The breakdown exposes provenance (supply_route_id / manufacturer / distributor).
  3. With no route assigned, the engine falls back to the catalogue conversion.
"""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    Ingredient,
    IngredientRecipeUnitConversion,
    Product,
    ProductSize,
    RecipeIngredient,
    RecipeUnit,
    Store,
)
from backend.models.supply_chain import (
    IngredientSupplierRef,
    Region,
    SupplierUnitConversion,
    SupplyRoute,
    SupplyRouteAssignment,
    SupplyRoutePrice,
)
from backend.services.cost_calculator import CostCalculator


def _route_scenario(db: Session, *, catalogue_cf: str, supplier_recipe_qty: str):
    """Build ingredient + store + region + route(+price) + supplier conversion.

    Recipe line uses recipe unit "p2_ml" with IngredientRecipeUnitConversion = 1
    (1 recipe-unit = 1 usage-unit), so the supplier conversion is the only factor
    that differs between catalogue and route pricing.
    """
    ing = Ingredient(
        name="P2 Milk",
        purchase_price=Decimal("999999"),     # catalogue price (should NOT be used)
        usage_unit="ml",
        conversion_factor=Decimal(catalogue_cf),
        yield_percentage=Decimal("1.00"),
    )
    unit = RecipeUnit(name="p2_ml", category="volume")
    region = Region(name="P2 Region", code="P2R")
    db.add_all([ing, unit, region])
    db.commit()

    store = Store(code="P2-STORE", name="P2 Store", region_id=region.id)
    route = SupplyRoute(ingredient_id=ing.id, is_direct=True)
    db.add_all([store, route])
    db.commit()

    # recipe unit conversion = 1 (no-op) so only supplier conversion matters
    db.add(IngredientRecipeUnitConversion(
        ingredient_id=ing.id, recipe_unit_id=unit.id,
        usage_unit_quantity=Decimal("1"),
    ))
    # assign route to region (priority 1, open)
    db.add(SupplyRouteAssignment(
        supply_route_id=route.id, region_id=region.id,
        priority=1, assigned_by="tester",
    ))
    # route price: qargo 90 per purchase unit (litro)
    db.add(SupplyRoutePrice(
        supply_route_id=route.id, list_price=Decimal("100"),
        qargo_price=Decimal("90"), currency_code="COP",
        price_per_unit="por litro", price_unit_id=unit.id, created_by="tester",
    ))
    # supplier ref + conversion: 1 litro = supplier_recipe_qty ml
    ref = IngredientSupplierRef(
        ingredient_id=ing.id, supply_route_id=route.id,
        external_name="Leche Litro", purchase_unit="litro",
    )
    db.add(ref)
    db.commit()
    db.add(SupplierUnitConversion(
        ingredient_ref_id=ref.id, recipe_unit_id=unit.id,
        purchase_qty=Decimal("1"), recipe_qty=Decimal(supplier_recipe_qty),
    ))
    db.commit()
    return ing, unit, store, route


def test_route_supplier_conversion_overrides_catalogue(test_db: Session):
    """Supplier conversion (1 L = 1000 ml) wins over catalogue cf (500).

    unit_cost via supplier = 90 / (1000/1) = 0.09 COP/ml ; 100 ml -> 9 COP.
    If catalogue cf (500) had been used: 90/500 = 0.18 -> 18 COP.
    """
    ing, unit, store, route = _route_scenario(
        test_db, catalogue_cf="500", supplier_recipe_qty="1000"
    )
    product = Product(name="P2 Product", is_sub_recipe=False)
    test_db.add(product)
    test_db.commit()
    test_db.add(RecipeIngredient(
        product_id=product.id, ingredient_id=ing.id,
        quantity=Decimal("100"), recipe_unit_id=unit.id,
        scales_with_size=False, process_yield_loss=Decimal("0"),
    ))
    test_db.commit()

    calc = CostCalculator(test_db)
    cost_store = calc.calculate_product_cost(product.id, store_id=store.id)

    assert abs(cost_store - Decimal("9")) < Decimal("0.5"), cost_store
    assert cost_store < Decimal("12")  # definitely not the catalogue-cf 18


def test_breakdown_exposes_route_provenance(test_db: Session):
    ing, unit, store, route = _route_scenario(
        test_db, catalogue_cf="500", supplier_recipe_qty="1000"
    )
    product = Product(name="P2 Prov Product", is_sub_recipe=False)
    test_db.add(product)
    test_db.commit()
    test_db.add(ProductSize(
        product_id=product.id, size_name="medium",
        scale_factor=Decimal("1.0"), is_default=True,
    ))
    test_db.add(RecipeIngredient(
        product_id=product.id, ingredient_id=ing.id,
        quantity=Decimal("100"), recipe_unit_id=unit.id,
        scales_with_size=False, process_yield_loss=Decimal("0"),
    ))
    test_db.commit()

    bd = CostCalculator(test_db).get_cost_breakdown(product.id, store_id=store.id)
    line = bd["breakdown"]["ingredients"][0]
    assert line["price_source"] == "route"
    assert line["supply_route_id"] == route.id


def test_no_route_falls_back_to_catalogue(test_db: Session):
    """Same ingredient but the recipe is priced WITHOUT a store -> catalogue cf.

    catalogue: 90000 / 1000 = 90 ... here we use a clean catalogue ingredient.
    """
    ing = Ingredient(
        name="P2 Catalogue Only",
        purchase_price=Decimal("10000"),
        usage_unit="g",
        conversion_factor=Decimal("1000"),
        yield_percentage=Decimal("1.00"),
    )
    product = Product(name="P2 Catalogue Product", is_sub_recipe=False)
    test_db.add_all([ing, product])
    test_db.commit()
    test_db.add(RecipeIngredient(
        product_id=product.id, ingredient_id=ing.id,
        quantity=Decimal("100"), scales_with_size=False,
        process_yield_loss=Decimal("0"),
    ))
    test_db.commit()

    # no store -> catalogue: 10000/1000 * 100 = 1000
    cost = CostCalculator(test_db).calculate_product_cost(product.id, store_id=None)
    assert abs(cost - Decimal("1000")) < Decimal("1")
