"""Integration tests for Fase B — supply chain admin UI.

Strategy:
  - All tests use `test_client` (TestClient bound to the rollback session).
  - GET page tests verify status 200 and key HTML landmarks (headings, table
    column headers, empty-state messages).
  - HTMX mutation tests (POST/DELETE) verify status 200, that the HTML
    response contains the expected content, AND that the database state
    changed correctly (via test_db queries after the request).
  - Error path tests verify the error message text appears in the response
    WITHOUT creating an invalid record in the database.

Naming: test_<section>_<scenario>
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.ingredient import Ingredient
from backend.models.supply_chain import (
    Distributor,
    IngredientSupplierRef,
    Manufacturer,
    Region,
    SupplyRoute,
    SupplyRouteAssignment,
    SupplyRoutePrice,
)
from backend.models.store import Store


# ===========================================================================
# Helper
# ===========================================================================

def _form(data: dict) -> dict:
    """Encode dict as form data (all values as strings)."""
    return {k: str(v) for k, v in data.items()}


def _assert_html(response, *, status: int = 200, contains: list[str] = (), absent: list[str] = ()):
    assert response.status_code == status, f"Got {response.status_code}: {response.text[:300]}"
    for fragment in contains:
        assert fragment in response.text, f"Expected '{fragment}' in response, got:\n{response.text[:500]}"
    for fragment in absent:
        assert fragment not in response.text, f"Did not expect '{fragment}' in response"


# ===========================================================================
# Regions UI
# ===========================================================================

class TestRegionsUI:

    def test_page_loads(self, test_client: TestClient):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["Regions", "Geographic operating units"])

    def test_empty_state_shown_when_no_regions(self, test_client: TestClient):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["No regions yet"])

    def test_page_shows_existing_region(self, test_client: TestClient, sc_region: Region):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=[sc_region.code, sc_region.name])

    def test_create_region_returns_200(self, test_client: TestClient):
        r = test_client.post("/supply-chain/regions/htmx",
                             data=_form({"name": "Cartagena", "code": "CTG", "country_code": "CO"}))
        assert r.status_code == 200

    def test_create_region_appears_in_response(self, test_client: TestClient):
        r = test_client.post("/supply-chain/regions/htmx",
                             data=_form({"name": "Cartagena Test", "code": "CTGT"}))
        _assert_html(r, contains=["Cartagena Test", "CTGT"])

    def test_create_region_code_is_uppercased(self, test_client: TestClient, test_db: Session):
        test_client.post("/supply-chain/regions/htmx",
                         data=_form({"name": "Barranquilla", "code": "baq"}))
        region = test_db.query(Region).filter(Region.code == "BAQ").first()
        assert region is not None
        assert region.name == "Barranquilla"

    def test_create_region_persists_in_db(self, test_client: TestClient, test_db: Session):
        test_client.post("/supply-chain/regions/htmx",
                         data=_form({"name": "Cúcuta", "code": "CUC"}))
        test_db.expire_all()
        region = test_db.query(Region).filter(Region.code == "CUC").first()
        assert region is not None
        assert region.is_active is True

    def test_duplicate_code_shows_error(self, test_client: TestClient, sc_region: Region):
        r = test_client.post("/supply-chain/regions/htmx",
                             data=_form({"name": "Duplicate", "code": sc_region.code}))
        _assert_html(r, contains=["already exists"])

    def test_duplicate_code_does_not_create_record(self, test_client: TestClient,
                                                    sc_region: Region, test_db: Session):
        before = test_db.query(Region).filter(Region.code == sc_region.code).count()
        test_client.post("/supply-chain/regions/htmx",
                         data=_form({"name": "Dup", "code": sc_region.code}))
        test_db.expire_all()
        after = test_db.query(Region).filter(Region.code == sc_region.code).count()
        assert after == before

    def test_blank_name_shows_error(self, test_client: TestClient):
        r = test_client.post("/supply-chain/regions/htmx",
                             data=_form({"name": "   ", "code": "ERR"}))
        _assert_html(r, contains=["required"])

    def test_deactivate_region(self, test_client: TestClient, sc_region: Region, test_db: Session):
        r = test_client.delete(f"/supply-chain/regions/htmx/{sc_region.id}")
        assert r.status_code == 200
        test_db.expire_all()
        updated = test_db.get(Region, sc_region.id)
        assert updated.is_active is False

    def test_deactivated_region_shows_inactive(self, test_client: TestClient, sc_region: Region):
        test_client.delete(f"/supply-chain/regions/htmx/{sc_region.id}")
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["Inactive"])

    def test_active_count_in_response(self, test_client: TestClient, sc_region: Region):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["region"])  # "1 region" or "N regions"


# ===========================================================================
# Manufacturers UI
# ===========================================================================

class TestManufacturersUI:

    def test_page_loads(self, test_client: TestClient):
        r = test_client.get("/supply-chain/manufacturers")
        _assert_html(r, contains=["Manufacturers", "Companies that physically produce"])

    def test_empty_state_shown(self, test_client: TestClient):
        r = test_client.get("/supply-chain/manufacturers")
        _assert_html(r, contains=["No manufacturers yet"])

    def test_page_shows_existing_manufacturer(self, test_client: TestClient,
                                               sc_manufacturer: Manufacturer):
        r = test_client.get("/supply-chain/manufacturers")
        _assert_html(r, contains=[sc_manufacturer.name])

    def test_create_manufacturer(self, test_client: TestClient, test_db: Session):
        r = test_client.post("/supply-chain/manufacturers/htmx",
                             data=_form({"name": "Alimentos del Valle S.A."}))
        _assert_html(r, contains=["Alimentos del Valle S.A."])
        test_db.expire_all()
        m = test_db.query(Manufacturer).filter(Manufacturer.name == "Alimentos del Valle S.A.").first()
        assert m is not None

    def test_create_manufacturer_with_nit(self, test_client: TestClient, test_db: Session):
        test_client.post("/supply-chain/manufacturers/htmx",
                         data=_form({"name": "NIT Test Mfr", "tax_id": "900000001-1",
                                     "country_code": "CO"}))
        test_db.expire_all()
        m = test_db.query(Manufacturer).filter(Manufacturer.name == "NIT Test Mfr").first()
        assert m is not None
        assert m.tax_id == "900000001-1"

    def test_blank_name_shows_error(self, test_client: TestClient):
        r = test_client.post("/supply-chain/manufacturers/htmx",
                             data=_form({"name": "   "}))
        _assert_html(r, contains=["required"])

    def test_blank_name_does_not_persist(self, test_client: TestClient, test_db: Session):
        count_before = test_db.query(Manufacturer).count()
        test_client.post("/supply-chain/manufacturers/htmx", data=_form({"name": ""}))
        test_db.expire_all()
        assert test_db.query(Manufacturer).count() == count_before

    def test_deactivate_manufacturer(self, test_client: TestClient,
                                     sc_manufacturer: Manufacturer, test_db: Session):
        r = test_client.delete(f"/supply-chain/manufacturers/htmx/{sc_manufacturer.id}")
        assert r.status_code == 200
        test_db.expire_all()
        assert test_db.get(Manufacturer, sc_manufacturer.id).is_active is False

    def test_deactivated_shows_inactive(self, test_client: TestClient, sc_manufacturer: Manufacturer):
        test_client.delete(f"/supply-chain/manufacturers/htmx/{sc_manufacturer.id}")
        r = test_client.get("/supply-chain/manufacturers")
        _assert_html(r, contains=["Inactive"])


# ===========================================================================
# Distributors UI
# ===========================================================================

class TestDistributorsUI:

    def test_page_loads(self, test_client: TestClient):
        r = test_client.get("/supply-chain/distributors")
        _assert_html(r, contains=["Distributors", "Intermediaries"])

    def test_empty_state_shown(self, test_client: TestClient):
        r = test_client.get("/supply-chain/distributors")
        _assert_html(r, contains=["No distributors yet"])

    def test_page_shows_existing_distributor(self, test_client: TestClient,
                                             sc_distributor: Distributor):
        r = test_client.get("/supply-chain/distributors")
        _assert_html(r, contains=[sc_distributor.name])

    def test_create_distributor(self, test_client: TestClient, test_db: Session):
        r = test_client.post("/supply-chain/distributors/htmx",
                             data=_form({"name": "Dist Sureste Ltda."}))
        _assert_html(r, contains=["Dist Sureste Ltda."])
        test_db.expire_all()
        d = test_db.query(Distributor).filter(Distributor.name == "Dist Sureste Ltda.").first()
        assert d is not None

    def test_create_with_contact_info(self, test_client: TestClient, test_db: Session):
        test_client.post("/supply-chain/distributors/htmx",
                         data=_form({
                             "name": "Dist Con Contacto",
                             "contact_email": "hola@dist.co",
                             "contact_phone": "+57 300 0000000",
                         }))
        test_db.expire_all()
        d = test_db.query(Distributor).filter(Distributor.name == "Dist Con Contacto").first()
        assert d is not None
        assert d.contact_email == "hola@dist.co"
        assert d.contact_phone == "+57 300 0000000"

    def test_blank_name_shows_error(self, test_client: TestClient):
        r = test_client.post("/supply-chain/distributors/htmx", data=_form({"name": "   "}))
        _assert_html(r, contains=["required"])

    def test_deactivate_distributor(self, test_client: TestClient,
                                    sc_distributor: Distributor, test_db: Session):
        test_client.delete(f"/supply-chain/distributors/htmx/{sc_distributor.id}")
        test_db.expire_all()
        assert test_db.get(Distributor, sc_distributor.id).is_active is False


# ===========================================================================
# Supply Routes UI — List
# ===========================================================================

class TestRoutesListUI:

    def test_page_loads(self, test_client: TestClient):
        r = test_client.get("/supply-chain/routes")
        _assert_html(r, contains=["Supply Routes", "Abstract paths"])

    def test_empty_state_shown(self, test_client: TestClient):
        r = test_client.get("/supply-chain/routes")
        _assert_html(r, contains=["No supply routes"])

    def test_page_shows_existing_route(self, test_client: TestClient,
                                       sc_supply_route: SupplyRoute,
                                       sc_ingredient: Ingredient,
                                       sc_manufacturer: Manufacturer):
        r = test_client.get("/supply-chain/routes")
        _assert_html(r, contains=[sc_ingredient.name, sc_manufacturer.name])

    def test_filter_by_ingredient_shows_route(self, test_client: TestClient,
                                               sc_supply_route: SupplyRoute,
                                               sc_ingredient: Ingredient):
        r = test_client.get(f"/supply-chain/routes?ingredient_id={sc_ingredient.id}")
        _assert_html(r, contains=[sc_ingredient.name])

    def test_filter_by_wrong_ingredient_shows_empty(self, test_client: TestClient,
                                                     sc_supply_route: SupplyRoute,
                                                     sc_ingredient: Ingredient,
                                                     test_db: Session):
        # create a second ingredient with no routes
        other = Ingredient(name="Other Ingredient SC", conversion_factor=1, yield_percentage=1)
        test_db.add(other)
        test_db.commit()
        r = test_client.get(f"/supply-chain/routes?ingredient_id={other.id}")
        _assert_html(r, contains=["No supply routes for this ingredient"])

    def test_create_route_with_manufacturer(self, test_client: TestClient,
                                            sc_ingredient: Ingredient,
                                            sc_manufacturer: Manufacturer,
                                            test_db: Session):
        r = test_client.post("/supply-chain/routes/htmx", data=_form({
            "ingredient_id": sc_ingredient.id,
            "source_type": "manufacturer",
            "manufacturer_id": sc_manufacturer.id,
        }))
        assert r.status_code == 200
        test_db.expire_all()
        route = (test_db.query(SupplyRoute)
                 .filter(SupplyRoute.ingredient_id == sc_ingredient.id,
                         SupplyRoute.manufacturer_id == sc_manufacturer.id)
                 .first())
        assert route is not None
        assert route.is_direct is False

    def test_create_route_with_distributor(self, test_client: TestClient,
                                           sc_ingredient: Ingredient,
                                           sc_distributor: Distributor,
                                           test_db: Session):
        r = test_client.post("/supply-chain/routes/htmx", data=_form({
            "ingredient_id": sc_ingredient.id,
            "source_type": "distributor",
            "distributor_id": sc_distributor.id,
        }))
        assert r.status_code == 200
        test_db.expire_all()
        route = (test_db.query(SupplyRoute)
                 .filter(SupplyRoute.ingredient_id == sc_ingredient.id,
                         SupplyRoute.distributor_id == sc_distributor.id)
                 .first())
        assert route is not None

    def test_create_direct_route(self, test_client: TestClient,
                                 sc_ingredient: Ingredient, test_db: Session):
        r = test_client.post("/supply-chain/routes/htmx", data=_form({
            "ingredient_id": sc_ingredient.id,
            "source_type": "direct",
        }))
        assert r.status_code == 200
        test_db.expire_all()
        route = (test_db.query(SupplyRoute)
                 .filter(SupplyRoute.ingredient_id == sc_ingredient.id,
                         SupplyRoute.is_direct == True)
                 .first())
        assert route is not None

    def test_create_with_no_source_shows_error(self, test_client: TestClient,
                                               sc_ingredient: Ingredient):
        # source_type = manufacturer but no manufacturer_id → error
        r = test_client.post("/supply-chain/routes/htmx", data=_form({
            "ingredient_id": sc_ingredient.id,
            "source_type": "manufacturer",
            "manufacturer_id": "",
        }))
        _assert_html(r, contains=["Select manufacturer"])

    def test_create_with_no_source_does_not_persist(self, test_client: TestClient,
                                                     sc_ingredient: Ingredient,
                                                     test_db: Session):
        count_before = test_db.query(SupplyRoute).count()
        test_client.post("/supply-chain/routes/htmx", data=_form({
            "ingredient_id": sc_ingredient.id,
            "source_type": "manufacturer",
            "manufacturer_id": "",
        }))
        test_db.expire_all()
        assert test_db.query(SupplyRoute).count() == count_before

    def test_deactivate_route(self, test_client: TestClient,
                              sc_supply_route: SupplyRoute, test_db: Session):
        r = test_client.delete(f"/supply-chain/routes/htmx/{sc_supply_route.id}")
        assert r.status_code == 200
        test_db.expire_all()
        assert test_db.get(SupplyRoute, sc_supply_route.id).is_active is False


# ===========================================================================
# Supply Routes UI — Detail
# ===========================================================================

class TestRouteDetailUI:

    def test_detail_page_loads(self, test_client: TestClient, sc_supply_route: SupplyRoute):
        r = test_client.get(f"/supply-chain/routes/{sc_supply_route.id}")
        assert r.status_code == 200

    def test_detail_shows_ingredient_name(self, test_client: TestClient,
                                          sc_supply_route: SupplyRoute,
                                          sc_ingredient: Ingredient):
        r = test_client.get(f"/supply-chain/routes/{sc_supply_route.id}")
        _assert_html(r, contains=[sc_ingredient.name])

    def test_detail_shows_manufacturer_name(self, test_client: TestClient,
                                            sc_supply_route: SupplyRoute,
                                            sc_manufacturer: Manufacturer):
        r = test_client.get(f"/supply-chain/routes/{sc_supply_route.id}")
        _assert_html(r, contains=[sc_manufacturer.name])

    def test_detail_shows_no_price_warning(self, test_client: TestClient,
                                           sc_supply_route: SupplyRoute):
        r = test_client.get(f"/supply-chain/routes/{sc_supply_route.id}")
        _assert_html(r, contains=["No active price set"])

    def test_detail_shows_tab_buttons(self, test_client: TestClient,
                                      sc_supply_route: SupplyRoute):
        r = test_client.get(f"/supply-chain/routes/{sc_supply_route.id}")
        _assert_html(r, contains=["Price", "Supplier Refs", "Conversions"])

    def test_detail_shows_breadcrumb(self, test_client: TestClient,
                                     sc_supply_route: SupplyRoute):
        r = test_client.get(f"/supply-chain/routes/{sc_supply_route.id}")
        _assert_html(r, contains=["Supply Routes"])

    def test_detail_nonexistent_route_redirects_to_list(self, test_client: TestClient):
        r = test_client.get("/supply-chain/routes/999999")
        # Should return the list page with an error or redirect
        assert r.status_code == 200


# ===========================================================================
# Route Prices HTMX
# ===========================================================================

class TestRoutePricesUI:

    def _price_form(self, route_id: int, list_price: int = 5000,
                    qargo_price: int = 4500) -> dict:
        return _form({
            "list_price": str(list_price),
            "qargo_price": str(qargo_price),
            "currency_code": "COP",
            "price_per_unit": "per liter",
            "source": "test-invoice",
            "created_by": "test_suite",
        })

    def test_set_price_returns_200(self, test_client: TestClient,
                                   sc_supply_route: SupplyRoute):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                             data=self._price_form(sc_supply_route.id))
        assert r.status_code == 200

    def test_set_price_shows_amount_in_response(self, test_client: TestClient,
                                                sc_supply_route: SupplyRoute):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                             data=self._price_form(sc_supply_route.id, 5000, 4500))
        _assert_html(r, contains=["4,500", "COP"])

    def test_set_price_persists_in_db(self, test_client: TestClient,
                                      sc_supply_route: SupplyRoute, test_db: Session):
        test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                         data=self._price_form(sc_supply_route.id))
        test_db.expire_all()
        price = (test_db.query(SupplyRoutePrice)
                 .filter(SupplyRoutePrice.supply_route_id == sc_supply_route.id,
                         SupplyRoutePrice.valid_until.is_(None))
                 .first())
        assert price is not None
        assert float(price.qargo_price) == 4500

    def test_second_price_closes_first(self, test_client: TestClient,
                                       sc_supply_route: SupplyRoute, test_db: Session):
        test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                         data=self._price_form(sc_supply_route.id, 5000, 4500))
        test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                         data=self._price_form(sc_supply_route.id, 6000, 5500))
        test_db.expire_all()
        active = (test_db.query(SupplyRoutePrice)
                  .filter(SupplyRoutePrice.supply_route_id == sc_supply_route.id,
                          SupplyRoutePrice.valid_until.is_(None))
                  .all())
        assert len(active) == 1
        assert float(active[0].qargo_price) == 5500

    def test_second_price_shows_current_badge(self, test_client: TestClient,
                                              sc_supply_route: SupplyRoute):
        test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                         data=self._price_form(sc_supply_route.id, 5000, 4500))
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                             data=self._price_form(sc_supply_route.id, 6000, 5500))
        _assert_html(r, contains=["current"])

    def test_qargo_greater_than_list_shows_error(self, test_client: TestClient,
                                                 sc_supply_route: SupplyRoute):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                             data=_form({
                                 "list_price": "1000",
                                 "qargo_price": "1500",
                                 "currency_code": "COP",
                                 "price_per_unit": "per kg",
                                 "created_by": "test",
                             }))
        _assert_html(r, contains=["cannot exceed"])

    def test_qargo_greater_than_list_does_not_persist(self, test_client: TestClient,
                                                       sc_supply_route: SupplyRoute,
                                                       test_db: Session):
        count_before = test_db.query(SupplyRoutePrice).count()
        test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                         data=_form({
                             "list_price": "1000", "qargo_price": "1500",
                             "currency_code": "COP", "price_per_unit": "per kg",
                             "created_by": "test",
                         }))
        test_db.expire_all()
        assert test_db.query(SupplyRoutePrice).count() == count_before

    def test_blank_created_by_shows_error(self, test_client: TestClient,
                                          sc_supply_route: SupplyRoute):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                             data=_form({
                                 "list_price": "1000", "qargo_price": "900",
                                 "currency_code": "COP", "price_per_unit": "per kg",
                                 "created_by": "   ",
                             }))
        _assert_html(r, contains=["required"])

    def test_price_history_section_shown(self, test_client: TestClient,
                                         sc_supply_route: SupplyRoute):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/prices/htmx",
                             data=self._price_form(sc_supply_route.id))
        _assert_html(r, contains=["Price history"])


# ===========================================================================
# Route Refs HTMX
# ===========================================================================

class TestRouteRefsUI:

    def _ref_form(self, ingredient_id: int) -> dict:
        return _form({
            "ingredient_id": ingredient_id,
            "external_name": "Leche Pasteurizada 3.5%",
            "external_code": "LCH-001",
            "purchase_unit": "Bolsa 1L",
            "units_per_pack": "12",
        })

    def test_add_ref_returns_200(self, test_client: TestClient,
                                  sc_supply_route: SupplyRoute, sc_ingredient: Ingredient):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/refs/htmx",
                             data=self._ref_form(sc_ingredient.id))
        assert r.status_code == 200

    def test_add_ref_shows_external_name(self, test_client: TestClient,
                                          sc_supply_route: SupplyRoute,
                                          sc_ingredient: Ingredient):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/refs/htmx",
                             data=self._ref_form(sc_ingredient.id))
        _assert_html(r, contains=["Leche Pasteurizada 3.5%", "LCH-001"])

    def test_add_ref_persists_in_db(self, test_client: TestClient,
                                     sc_supply_route: SupplyRoute,
                                     sc_ingredient: Ingredient, test_db: Session):
        test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/refs/htmx",
                         data=self._ref_form(sc_ingredient.id))
        test_db.expire_all()
        ref = (test_db.query(IngredientSupplierRef)
               .filter(IngredientSupplierRef.supply_route_id == sc_supply_route.id)
               .first())
        assert ref is not None
        assert ref.external_name == "Leche Pasteurizada 3.5%"
        assert ref.external_code == "LCH-001"

    def test_blank_external_name_shows_error(self, test_client: TestClient,
                                              sc_supply_route: SupplyRoute,
                                              sc_ingredient: Ingredient):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/refs/htmx",
                             data=_form({
                                 "ingredient_id": sc_ingredient.id,
                                 "external_name": "   ",
                                 "purchase_unit": "Bolsa 1L",
                             }))
        _assert_html(r, contains=["required"])

    def test_blank_external_name_does_not_persist(self, test_client: TestClient,
                                                   sc_supply_route: SupplyRoute,
                                                   sc_ingredient: Ingredient,
                                                   test_db: Session):
        count_before = test_db.query(IngredientSupplierRef).count()
        test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/refs/htmx",
                         data=_form({
                             "ingredient_id": sc_ingredient.id,
                             "external_name": "   ",
                             "purchase_unit": "Bolsa 1L",
                         }))
        test_db.expire_all()
        assert test_db.query(IngredientSupplierRef).count() == count_before

    def test_blank_purchase_unit_shows_error(self, test_client: TestClient,
                                              sc_supply_route: SupplyRoute,
                                              sc_ingredient: Ingredient):
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/refs/htmx",
                             data=_form({
                                 "ingredient_id": sc_ingredient.id,
                                 "external_name": "Leche Fresca",
                                 "purchase_unit": "   ",
                             }))
        _assert_html(r, contains=["required"])

    def test_multiple_refs_all_shown(self, test_client: TestClient,
                                      sc_supply_route: SupplyRoute,
                                      sc_ingredient: Ingredient,
                                      test_db: Session):
        # uq_isr_refs is UNIQUE(ingredient_id, supply_route_id): one ref per
        # ingredient per route. Multiple refs on a route = different ingredients.
        from decimal import Decimal
        ingredient_b = Ingredient(
            name="Segundo Ingrediente Ref",
            purchase_price=Decimal("1000"),
            usage_unit="g",
            conversion_factor=Decimal("1000"),
            yield_percentage=Decimal("1.00"),
        )
        test_db.add(ingredient_b)
        test_db.commit()
        test_db.refresh(ingredient_b)

        test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/refs/htmx",
                         data=_form({
                             "ingredient_id": sc_ingredient.id,
                             "external_name": "Ref A", "purchase_unit": "Caja",
                         }))
        r = test_client.post(f"/supply-chain/routes/{sc_supply_route.id}/refs/htmx",
                              data=_form({
                                  "ingredient_id": ingredient_b.id,
                                  "external_name": "Ref B", "purchase_unit": "Bolsa",
                              }))
        _assert_html(r, contains=["Ref A", "Ref B"])


# ===========================================================================
# Assignments UI
# ===========================================================================

class TestAssignmentsUI:

    def test_page_loads(self, test_client: TestClient):
        r = test_client.get("/supply-chain/assignments")
        _assert_html(r, contains=["Route Assignments", "Resolution order"])

    def test_empty_state_shown(self, test_client: TestClient):
        r = test_client.get("/supply-chain/assignments")
        _assert_html(r, contains=["No active assignments"])

    def test_page_shows_existing_assignment(self, test_client: TestClient,
                                            sc_assignment: SupplyRouteAssignment,
                                            sc_ingredient: Ingredient,
                                            sc_region: Region):
        r = test_client.get("/supply-chain/assignments")
        _assert_html(r, contains=[sc_ingredient.name, sc_region.name])

    def test_create_regional_assignment(self, test_client: TestClient,
                                        sc_supply_route: SupplyRoute,
                                        sc_region: Region, test_db: Session):
        r = test_client.post("/supply-chain/assignments/htmx", data=_form({
            "supply_route_id": sc_supply_route.id,
            "scope_type": "region",
            "region_id": sc_region.id,
            "priority": "1",
            "valid_from": str(date.today()),
            "assigned_by": "test_suite",
        }))
        assert r.status_code == 200
        test_db.expire_all()
        a = (test_db.query(SupplyRouteAssignment)
             .filter(SupplyRouteAssignment.supply_route_id == sc_supply_route.id,
                     SupplyRouteAssignment.region_id == sc_region.id,
                     SupplyRouteAssignment.valid_until.is_(None))
             .first())
        assert a is not None

    def test_create_store_assignment(self, test_client: TestClient,
                                     sc_supply_route: SupplyRoute,
                                     sc_store: Store, test_db: Session):
        r = test_client.post("/supply-chain/assignments/htmx", data=_form({
            "supply_route_id": sc_supply_route.id,
            "scope_type": "store",
            "store_id": sc_store.id,
            "priority": "1",
            "valid_from": str(date.today()),
            "assigned_by": "test_suite",
        }))
        assert r.status_code == 200
        test_db.expire_all()
        a = (test_db.query(SupplyRouteAssignment)
             .filter(SupplyRouteAssignment.supply_route_id == sc_supply_route.id,
                     SupplyRouteAssignment.store_id == sc_store.id,
                     SupplyRouteAssignment.valid_until.is_(None))
             .first())
        assert a is not None

    def test_create_without_scope_shows_error(self, test_client: TestClient,
                                               sc_supply_route: SupplyRoute):
        # scope_type=region but no region_id
        r = test_client.post("/supply-chain/assignments/htmx", data=_form({
            "supply_route_id": sc_supply_route.id,
            "scope_type": "region",
            "region_id": "",
            "priority": "1",
            "valid_from": str(date.today()),
            "assigned_by": "test",
        }))
        _assert_html(r, contains=["Select a region"])

    def test_create_without_scope_does_not_persist(self, test_client: TestClient,
                                                    sc_supply_route: SupplyRoute,
                                                    test_db: Session):
        count_before = test_db.query(SupplyRouteAssignment).count()
        test_client.post("/supply-chain/assignments/htmx", data=_form({
            "supply_route_id": sc_supply_route.id,
            "scope_type": "region",
            "region_id": "",
            "priority": "1",
            "valid_from": str(date.today()),
            "assigned_by": "test",
        }))
        test_db.expire_all()
        assert test_db.query(SupplyRouteAssignment).count() == count_before

    def test_blank_assigned_by_shows_error(self, test_client: TestClient,
                                           sc_supply_route: SupplyRoute,
                                           sc_region: Region):
        r = test_client.post("/supply-chain/assignments/htmx", data=_form({
            "supply_route_id": sc_supply_route.id,
            "scope_type": "region",
            "region_id": sc_region.id,
            "priority": "1",
            "valid_from": str(date.today()),
            "assigned_by": "  ",
        }))
        _assert_html(r, contains=["required"])

    def test_new_assignment_auto_closes_existing_same_priority(
        self,
        test_client: TestClient,
        sc_assignment: SupplyRouteAssignment,
        sc_supply_route: SupplyRoute,
        sc_region: Region,
        sc_ingredient: Ingredient,
        sc_manufacturer: Manufacturer,
        test_db: Session,
    ):
        # sc_assignment is priority=1 for sc_region. Create a second route and assign it.
        second_route = SupplyRoute(
            ingredient_id=sc_ingredient.id,
            manufacturer_id=sc_manufacturer.id,
            is_active=True,
        )
        test_db.add(second_route)
        test_db.commit()

        test_client.post("/supply-chain/assignments/htmx", data=_form({
            "supply_route_id": second_route.id,
            "scope_type": "region",
            "region_id": sc_region.id,
            "priority": "1",
            "valid_from": str(date.today()),
            "assigned_by": "test_suite",
        }))
        test_db.expire_all()

        # Original assignment should now be closed
        original = test_db.get(SupplyRouteAssignment, sc_assignment.id)
        assert original.valid_until is not None

        # New assignment should be active
        new = (test_db.query(SupplyRouteAssignment)
               .filter(SupplyRouteAssignment.supply_route_id == second_route.id,
                       SupplyRouteAssignment.valid_until.is_(None))
               .first())
        assert new is not None

    def test_close_assignment_returns_200(self, test_client: TestClient,
                                          sc_assignment: SupplyRouteAssignment):
        r = test_client.post(f"/supply-chain/assignments/htmx/{sc_assignment.id}/close",
                             data=_form({"change_reason": "test closure"}))
        assert r.status_code == 200

    def test_close_assignment_persists_valid_until(self, test_client: TestClient,
                                                   sc_assignment: SupplyRouteAssignment,
                                                   test_db: Session):
        test_client.post(f"/supply-chain/assignments/htmx/{sc_assignment.id}/close",
                         data=_form({"change_reason": ""}))
        test_db.expire_all()
        updated = test_db.get(SupplyRouteAssignment, sc_assignment.id)
        assert updated.valid_until == date.today()

    def test_closed_assignment_shows_in_page(self, test_client: TestClient,
                                              sc_assignment: SupplyRouteAssignment,
                                              sc_ingredient: Ingredient):
        test_client.post(f"/supply-chain/assignments/htmx/{sc_assignment.id}/close",
                         data=_form({}))
        # After closing, that assignment is no longer active — page shows empty
        r = test_client.get("/supply-chain/assignments")
        _assert_html(r, contains=["No active assignments"])

    def test_assignment_shows_region_badge(self, test_client: TestClient,
                                           sc_assignment: SupplyRouteAssignment):
        r = test_client.get("/supply-chain/assignments")
        _assert_html(r, contains=["REGION"])

    def test_priority_badge_shown(self, test_client: TestClient,
                                   sc_assignment: SupplyRouteAssignment):
        r = test_client.get("/supply-chain/assignments")
        _assert_html(r, contains=["Primary"])


# ===========================================================================
# Navbar Supply Chain dropdown
# ===========================================================================

class TestNavbarUI:

    def test_any_page_has_supply_chain_in_nav(self, test_client: TestClient):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["Supply Chain"])

    def test_navbar_has_routes_link(self, test_client: TestClient):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["/supply-chain/routes"])

    def test_navbar_has_assignments_link(self, test_client: TestClient):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["/supply-chain/assignments"])

    def test_navbar_has_manufacturers_link(self, test_client: TestClient):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["/supply-chain/manufacturers"])

    def test_navbar_has_distributors_link(self, test_client: TestClient):
        r = test_client.get("/supply-chain/regions")
        _assert_html(r, contains=["/supply-chain/distributors"])
