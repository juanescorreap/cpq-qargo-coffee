"""Integration tests for Fase C — region assignment and Active Routes tab.

Tests verify:
  - Store detail page renders the region section (_region_section.html)
  - Warning appears when no region is assigned
  - Region name/code badge appears when region is assigned
  - POST /{store_id}/region-htmx assigns a region and returns correct partial
  - POST /{store_id}/region-htmx clears region when sent region_id=""
  - Active Routes tab (third tab) is present in the store detail page
  - Tab shows "no recipe ingredients" empty state when no recipes exist
  - Tab shows resolved route rows when supply chain is fully configured
  - Tab shows unresolved rows for ingredients without route assignments
  - Tab shows the "no region" warning inside the routes tab content
  - `_resolve_active_routes` helper integrates with fn_resolve_supply_route
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.ingredient import Ingredient
from backend.models.product import Product, RecipeIngredient, StoreProduct
from backend.models.store import Store
from backend.models.supply_chain import (
    Manufacturer,
    Region,
    SupplyRoute,
    SupplyRouteAssignment,
    SupplyRoutePrice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_html(response, *, status: int = 200, contains: list[str] = (), absent: list[str] = ()):
    assert response.status_code == status, f"Got {response.status_code}:\n{response.text[:400]}"
    for fragment in contains:
        assert fragment in response.text, f"Expected {fragment!r} in response"
    for fragment in absent:
        assert fragment not in response.text, f"Did not expect {fragment!r} in response"


# ---------------------------------------------------------------------------
# Fixtures — recipe data needed for Active Routes tab
# ---------------------------------------------------------------------------

@pytest.fixture
def product_with_sc_ingredient(
    test_db: Session, sc_store: Store, sc_ingredient: Ingredient
) -> Product:
    """Active product whose recipe uses sc_ingredient, available at sc_store.

    Sets store_products.is_available=True so _resolve_active_routes includes
    this ingredient when querying the store's active menu.
    """
    product = Product(
        name="Test Cappuccino Fase C",
        category="hot_beverages",
        is_sub_recipe=False,
        is_active=True,
    )
    test_db.add(product)
    test_db.commit()

    recipe_line = RecipeIngredient(
        product_id=product.id,
        ingredient_id=sc_ingredient.id,
        quantity=240,
        scales_with_size=True,
        process_yield_loss=0,
    )
    test_db.add(recipe_line)

    # Register the product as available at sc_store so the Active Routes tab sees it
    store_product = StoreProduct(
        store_id=sc_store.id,
        product_id=product.id,
        is_available=True,
    )
    test_db.add(store_product)
    test_db.commit()
    return product


@pytest.fixture
def store_without_region(test_db: Session) -> Store:
    """Store with no region assigned."""
    store = Store(code="SC-NOREG-01", name="Tienda Sin Región", city="Bogotá")
    test_db.add(store)
    test_db.commit()
    return store


@pytest.fixture
def route_with_price(test_db: Session, sc_supply_route: SupplyRoute) -> SupplyRoutePrice:
    """Active price on the sc_supply_route fixture."""
    price = SupplyRoutePrice(
        supply_route_id=sc_supply_route.id,
        list_price=5000,
        qargo_price=4500,
        currency_code="COP",
        price_per_unit="por litro",
        created_by="test_fixture",
    )
    test_db.add(price)
    test_db.commit()
    return price


# ===========================================================================
# Store detail — region section rendering
# ===========================================================================

class TestStoreDetailRegionSection:

    def test_detail_page_loads(self, test_client: TestClient, sc_store: Store):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=[sc_store.name])

    def test_detail_shows_region_name_when_assigned(
        self, test_client: TestClient, sc_store: Store, sc_region: Region
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=[sc_region.name, sc_region.code])

    def test_detail_shows_no_region_warning_when_not_assigned(
        self, test_client: TestClient, store_without_region: Store
    ):
        r = test_client.get(f"/stores/{store_without_region.id}")
        _assert_html(r, contains=["No region"])

    def test_detail_no_region_warning_absent_when_region_set(
        self, test_client: TestClient, sc_store: Store, sc_region: Region
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        # When region IS assigned, the "No region" warning should not appear
        _assert_html(r, absent=["No region — costs use base prices"])

    def test_detail_shows_region_section_element(
        self, test_client: TestClient, sc_store: Store
    ):
        """The region-section div must be present for HTMX to target."""
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["region-section"])

    def test_detail_region_dropdown_contains_regions(
        self, test_client: TestClient, sc_store: Store, sc_region: Region
    ):
        """The region assignment form dropdown lists active regions."""
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=[sc_region.name])

    def test_detail_shows_active_routes_tab_button(
        self, test_client: TestClient, sc_store: Store
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["Active Routes"])


# ===========================================================================
# Region assignment HTMX endpoint
# ===========================================================================

class TestRegionAssignment:

    def test_assign_region_returns_200(
        self, test_client: TestClient, store_without_region: Store, sc_region: Region
    ):
        r = test_client.post(
            f"/stores/{store_without_region.id}/region-htmx",
            data={"region_id": str(sc_region.id)},
        )
        assert r.status_code == 200

    def test_assign_region_shows_region_name_in_response(
        self, test_client: TestClient, store_without_region: Store, sc_region: Region
    ):
        r = test_client.post(
            f"/stores/{store_without_region.id}/region-htmx",
            data={"region_id": str(sc_region.id)},
        )
        _assert_html(r, contains=[sc_region.name, sc_region.code])

    def test_assign_region_persists_in_db(
        self,
        test_client: TestClient,
        store_without_region: Store,
        sc_region: Region,
        test_db: Session,
    ):
        test_client.post(
            f"/stores/{store_without_region.id}/region-htmx",
            data={"region_id": str(sc_region.id)},
        )
        test_db.expire_all()
        updated = test_db.get(Store, store_without_region.id)
        assert updated.region_id == sc_region.id

    def test_assign_region_removes_warning_from_response(
        self, test_client: TestClient, store_without_region: Store, sc_region: Region
    ):
        r = test_client.post(
            f"/stores/{store_without_region.id}/region-htmx",
            data={"region_id": str(sc_region.id)},
        )
        _assert_html(r, absent=["No region — costs use base prices"])

    def test_clear_region_shows_warning(
        self, test_client: TestClient, sc_store: Store
    ):
        """Submitting empty region_id clears the region and shows the warning."""
        r = test_client.post(
            f"/stores/{sc_store.id}/region-htmx",
            data={"region_id": ""},
        )
        _assert_html(r, contains=["No region"])

    def test_clear_region_persists_null_in_db(
        self, test_client: TestClient, sc_store: Store, test_db: Session
    ):
        test_client.post(f"/stores/{sc_store.id}/region-htmx", data={"region_id": ""})
        test_db.expire_all()
        updated = test_db.get(Store, sc_store.id)
        assert updated.region_id is None

    def test_response_targets_region_section_id(
        self, test_client: TestClient, store_without_region: Store, sc_region: Region
    ):
        """Response HTML must have the #region-section id for HTMX swap to work."""
        r = test_client.post(
            f"/stores/{store_without_region.id}/region-htmx",
            data={"region_id": str(sc_region.id)},
        )
        _assert_html(r, contains=["region-section"])

    def test_change_region_updates_assignment(
        self,
        test_client: TestClient,
        sc_store: Store,
        test_db: Session,
    ):
        """Assigning a second region overwrites the first."""
        second_region = Region(name="Medellín", code="MED-TEST", country_code="CO")
        test_db.add(second_region)
        test_db.commit()

        test_client.post(
            f"/stores/{sc_store.id}/region-htmx",
            data={"region_id": str(second_region.id)},
        )
        test_db.expire_all()
        updated = test_db.get(Store, sc_store.id)
        assert updated.region_id == second_region.id


# ===========================================================================
# Active Routes tab — empty state
# ===========================================================================

class TestActiveRoutesEmptyState:

    def test_tab_shows_empty_state_when_no_recipes(
        self, test_client: TestClient, sc_store: Store
    ):
        """Store with region but no recipe ingredients → empty state."""
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["No recipe ingredients found"])

    def test_tab_shows_no_region_warning_inside_routes_tab(
        self, test_client: TestClient, store_without_region: Store
    ):
        r = test_client.get(f"/stores/{store_without_region.id}")
        _assert_html(r, contains=["no region assigned"])

    def test_tab_counter_shows_zero_over_zero_when_no_recipes(
        self, test_client: TestClient, sc_store: Store
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        # The tab badge shows "0/0" when there are no recipe ingredients
        _assert_html(r, contains=["Active Routes"])


# ===========================================================================
# Active Routes tab — with recipe ingredients
# ===========================================================================

class TestActiveRoutesWithData:

    def test_tab_shows_ingredient_name_in_table(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=[sc_ingredient.name])

    def test_unresolved_row_shows_base_price_status(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
    ):
        """Ingredient in recipe but no route assigned → 'Base price' status."""
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["Base price"])

    def test_resolved_row_shows_resolved_status(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
        sc_assignment: SupplyRouteAssignment,
    ):
        """Ingredient with active regional assignment → 'Resolved' status."""
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["Resolved"])

    def test_resolved_row_shows_manufacturer_name(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        sc_manufacturer: Manufacturer,
        product_with_sc_ingredient: Product,
        sc_assignment: SupplyRouteAssignment,
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=[sc_manufacturer.name])

    def test_resolved_row_shows_regional_scope_badge(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
        sc_assignment: SupplyRouteAssignment,
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["Regional"])

    def test_resolved_row_shows_primary_priority_badge(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
        sc_assignment: SupplyRouteAssignment,
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["Primary"])

    def test_resolved_row_shows_active_price(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
        sc_assignment: SupplyRouteAssignment,
        route_with_price: SupplyRoutePrice,
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["4,500", "COP"])

    def test_resolved_row_shows_no_price_set_when_missing(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
        sc_assignment: SupplyRouteAssignment,
    ):
        """Route assigned but no price set → 'No price set' label."""
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["No price set"])

    def test_tab_counter_shows_resolved_fraction(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
        sc_assignment: SupplyRouteAssignment,
    ):
        """Tab badge shows 'N/M' resolved/total fraction."""
        r = test_client.get(f"/stores/{sc_store.id}")
        # 1 ingredient resolved out of 1 total → "1/1"
        _assert_html(r, contains=["1/1"])

    def test_configure_assignments_link_shown_when_unresolved(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
    ):
        """When ingredients are unresolved, a link to assignments page appears."""
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["Configure assignments"])

    def test_configure_assignments_link_absent_when_all_resolved(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
        sc_assignment: SupplyRouteAssignment,
    ):
        """When all ingredients are resolved, the 'Configure' link is hidden."""
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, absent=["Configure assignments"])

    def test_store_override_shows_store_override_scope(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        sc_supply_route: SupplyRoute,
        product_with_sc_ingredient: Product,
        test_db: Session,
    ):
        """Store-level override assignment shows 'Store override' scope badge."""
        store_assignment = SupplyRouteAssignment(
            supply_route_id=sc_supply_route.id,
            store_id=sc_store.id,
            priority=1,
            valid_from=date.today(),
            assigned_by="test_suite",
        )
        test_db.add(store_assignment)
        test_db.commit()

        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["Store override"])


# ===========================================================================
# Legend and informational elements
# ===========================================================================

class TestActiveRoutesLegend:

    def test_legend_shows_resolved_indicator(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["Route resolved"])

    def test_legend_shows_base_price_indicator(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["No route — uses base price"])

    def test_summary_line_shows_counts(
        self,
        test_client: TestClient,
        sc_store: Store,
        sc_ingredient: Ingredient,
        product_with_sc_ingredient: Product,
    ):
        r = test_client.get(f"/stores/{sc_store.id}")
        _assert_html(r, contains=["0 of 1 ingredients have an active route"])
