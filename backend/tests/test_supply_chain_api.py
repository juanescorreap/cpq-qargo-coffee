"""HTTP integration tests for Fase A supply chain routers.

All tests use `test_client` (TestClient wired to the rollback session).
Data created through HTTP calls is visible within the same test and is
rolled back automatically after each test.

Naming convention: test_<resource>_<scenario>
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.ingredient import Ingredient
from backend.models.supply_chain import (
    Distributor,
    Manufacturer,
    Region,
    SupplyRoute,
    SupplyRouteAssignment,
    SupplyRoutePrice,
)
from backend.models.store import Store


# ===========================================================================
# Regions
# ===========================================================================

class TestRegionsRouter:
    def test_list_empty_initially(self, test_client: TestClient):
        r = test_client.get("/api/regions?is_active=true")
        assert r.status_code == 200

    def test_create_returns_201(self, test_client: TestClient):
        r = test_client.post("/api/regions", json={"name": "Bogotá", "code": "BG1"})
        assert r.status_code == 201
        body = r.json()
        assert body["code"] == "BG1"
        assert body["is_active"] is True
        assert body["country_code"] == "CO"
        assert "id" in body

    def test_code_is_uppercased_by_api(self, test_client: TestClient):
        r = test_client.post("/api/regions", json={"name": "Medellín", "code": "med1"})
        assert r.status_code == 201
        assert r.json()["code"] == "MED1"

    def test_duplicate_code_returns_409(self, test_client: TestClient):
        test_client.post("/api/regions", json={"name": "R1", "code": "DUPCODE"})
        r2 = test_client.post("/api/regions", json={"name": "R2", "code": "DUPCODE"})
        assert r2.status_code == 409

    def test_get_by_id_returns_200(self, test_client: TestClient):
        created = test_client.post("/api/regions", json={"name": "Cali", "code": "CLI"}).json()
        r = test_client.get(f"/api/regions/{created['id']}")
        assert r.status_code == 200
        assert r.json()["name"] == "Cali"

    def test_get_nonexistent_returns_404(self, test_client: TestClient):
        r = test_client.get("/api/regions/999999")
        assert r.status_code == 404

    def test_update_name(self, test_client: TestClient):
        created = test_client.post("/api/regions", json={"name": "Old Name", "code": "UPD1"}).json()
        r = test_client.put(f"/api/regions/{created['id']}", json={"name": "New Name"})
        assert r.status_code == 200
        assert r.json()["name"] == "New Name"
        assert r.json()["code"] == "UPD1"  # unchanged

    def test_update_code_conflict_returns_409(self, test_client: TestClient):
        r1 = test_client.post("/api/regions", json={"name": "R1", "code": "COD1"}).json()
        test_client.post("/api/regions", json={"name": "R2", "code": "COD2"})
        r = test_client.put(f"/api/regions/{r1['id']}", json={"code": "COD2"})
        assert r.status_code == 409

    def test_deactivate_returns_200(self, test_client: TestClient):
        created = test_client.post("/api/regions", json={"name": "To Delete", "code": "DEL1"}).json()
        r = test_client.delete(f"/api/regions/{created['id']}")
        assert r.status_code == 200

    def test_deactivated_region_shows_inactive(self, test_client: TestClient):
        created = test_client.post("/api/regions", json={"name": "Inactive", "code": "INA1"}).json()
        test_client.delete(f"/api/regions/{created['id']}")
        r = test_client.get(f"/api/regions/{created['id']}")
        assert r.json()["is_active"] is False

    def test_list_filter_by_is_active(self, test_client: TestClient):
        r_active = test_client.post("/api/regions", json={"name": "Active", "code": "ACT1"}).json()
        r_inactive = test_client.post("/api/regions", json={"name": "Inactive2", "code": "INA2"}).json()
        test_client.delete(f"/api/regions/{r_inactive['id']}")

        active_ids = [x["id"] for x in test_client.get("/api/regions?is_active=true").json()]
        inactive_ids = [x["id"] for x in test_client.get("/api/regions?is_active=false").json()]

        assert r_active["id"] in active_ids
        assert r_inactive["id"] not in active_ids
        assert r_inactive["id"] in inactive_ids

    def test_blank_name_returns_422(self, test_client: TestClient):
        r = test_client.post("/api/regions", json={"name": "", "code": "ERR"})
        assert r.status_code == 422

    def test_blank_code_returns_422(self, test_client: TestClient):
        r = test_client.post("/api/regions", json={"name": "Valid Name", "code": "  "})
        assert r.status_code == 422


# ===========================================================================
# Manufacturers
# ===========================================================================

class TestManufacturersRouter:
    def test_create_returns_201(self, test_client: TestClient):
        r = test_client.post("/api/manufacturers", json={"name": "Lácteos S.A."})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Lácteos S.A."
        assert body["country_code"] == "CO"
        assert body["is_active"] is True

    def test_create_with_all_fields(self, test_client: TestClient):
        r = test_client.post("/api/manufacturers", json={
            "name": "Industrias Test",
            "country_code": "US",
            "tax_id": "900000001",
            "website": "https://industrias.test",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["tax_id"] == "900000001"
        assert body["website"] == "https://industrias.test"

    def test_get_by_id(self, test_client: TestClient):
        created = test_client.post("/api/manufacturers", json={"name": "Get Test Mfr"}).json()
        r = test_client.get(f"/api/manufacturers/{created['id']}")
        assert r.status_code == 200
        assert r.json()["name"] == "Get Test Mfr"

    def test_get_nonexistent_returns_404(self, test_client: TestClient):
        assert test_client.get("/api/manufacturers/999999").status_code == 404

    def test_update(self, test_client: TestClient):
        created = test_client.post("/api/manufacturers", json={"name": "Old Mfr"}).json()
        r = test_client.put(f"/api/manufacturers/{created['id']}", json={"name": "New Mfr"})
        assert r.status_code == 200
        assert r.json()["name"] == "New Mfr"

    def test_deactivate(self, test_client: TestClient):
        created = test_client.post("/api/manufacturers", json={"name": "Del Mfr"}).json()
        assert test_client.delete(f"/api/manufacturers/{created['id']}").status_code == 200
        assert test_client.get(f"/api/manufacturers/{created['id']}").json()["is_active"] is False

    def test_list_filtered_by_active(self, test_client: TestClient):
        active = test_client.post("/api/manufacturers", json={"name": "Active Mfr"}).json()
        inactive = test_client.post("/api/manufacturers", json={"name": "Inactive Mfr"}).json()
        test_client.delete(f"/api/manufacturers/{inactive['id']}")

        active_ids = [x["id"] for x in test_client.get("/api/manufacturers?is_active=true").json()]
        assert active["id"] in active_ids
        assert inactive["id"] not in active_ids

    def test_blank_name_returns_422(self, test_client: TestClient):
        assert test_client.post("/api/manufacturers", json={"name": ""}).status_code == 422


# ===========================================================================
# Distributors
# ===========================================================================

class TestDistributorsRouter:
    def test_create_returns_201(self, test_client: TestClient):
        r = test_client.post("/api/distributors", json={"name": "Dist Norte"})
        assert r.status_code == 201
        assert r.json()["name"] == "Dist Norte"

    def test_create_with_contact_fields(self, test_client: TestClient):
        r = test_client.post("/api/distributors", json={
            "name": "Dist Sur",
            "contact_email": "sur@dist.co",
            "contact_phone": "+57 300 0000000",
        })
        assert r.status_code == 201
        assert r.json()["contact_email"] == "sur@dist.co"

    def test_get_by_id(self, test_client: TestClient):
        created = test_client.post("/api/distributors", json={"name": "Get Dist"}).json()
        r = test_client.get(f"/api/distributors/{created['id']}")
        assert r.status_code == 200

    def test_get_nonexistent_returns_404(self, test_client: TestClient):
        assert test_client.get("/api/distributors/999999").status_code == 404

    def test_update(self, test_client: TestClient):
        created = test_client.post("/api/distributors", json={"name": "Old Dist"}).json()
        r = test_client.put(f"/api/distributors/{created['id']}", json={"name": "Updated Dist"})
        assert r.status_code == 200
        assert r.json()["name"] == "Updated Dist"

    def test_deactivate(self, test_client: TestClient):
        created = test_client.post("/api/distributors", json={"name": "Del Dist"}).json()
        test_client.delete(f"/api/distributors/{created['id']}")
        assert test_client.get(f"/api/distributors/{created['id']}").json()["is_active"] is False

    def test_blank_name_returns_422(self, test_client: TestClient):
        assert test_client.post("/api/distributors", json={"name": "  "}).status_code == 422


# ===========================================================================
# Supply Routes
# ===========================================================================

class TestSupplyRoutesRouter:
    def test_create_with_manufacturer(
        self, test_client: TestClient, sc_ingredient: Ingredient, sc_manufacturer: Manufacturer
    ):
        r = test_client.post("/api/supply-routes", json={
            "ingredient_id": sc_ingredient.id,
            "manufacturer_id": sc_manufacturer.id,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["ingredient_id"] == sc_ingredient.id
        assert body["manufacturer_id"] == sc_manufacturer.id
        assert body["ingredient_name"] == sc_ingredient.name
        assert body["manufacturer_name"] == sc_manufacturer.name
        assert body["is_direct"] is False

    def test_create_with_distributor(
        self, test_client: TestClient, sc_ingredient: Ingredient, sc_distributor: Distributor
    ):
        r = test_client.post("/api/supply-routes", json={
            "ingredient_id": sc_ingredient.id,
            "distributor_id": sc_distributor.id,
        })
        assert r.status_code == 201
        assert r.json()["distributor_id"] == sc_distributor.id

    def test_create_direct_purchase(
        self, test_client: TestClient, sc_ingredient: Ingredient,
        sc_manufacturer: Manufacturer,
    ):
        # Direct purchase = bought straight from a manufacturer, so manufacturer_id
        # is set and distributor_id stays null (ck_supply_routes_endpoint requires
        # at least one supplier endpoint).
        r = test_client.post("/api/supply-routes", json={
            "ingredient_id": sc_ingredient.id,
            "manufacturer_id": sc_manufacturer.id,
            "is_direct": True,
        })
        assert r.status_code == 201
        assert r.json()["is_direct"] is True

    def test_direct_with_distributor_returns_422(
        self, test_client: TestClient, sc_ingredient: Ingredient, sc_distributor: Distributor
    ):
        r = test_client.post("/api/supply-routes", json={
            "ingredient_id": sc_ingredient.id,
            "distributor_id": sc_distributor.id,
            "is_direct": True,
        })
        assert r.status_code == 422

    def test_no_source_returns_422(self, test_client: TestClient, sc_ingredient: Ingredient):
        r = test_client.post("/api/supply-routes", json={
            "ingredient_id": sc_ingredient.id,
            "is_direct": False,
        })
        assert r.status_code == 422

    def test_nonexistent_ingredient_returns_404(
        self, test_client: TestClient, sc_manufacturer: Manufacturer
    ):
        r = test_client.post("/api/supply-routes", json={
            "ingredient_id": 999999,
            "manufacturer_id": sc_manufacturer.id,
        })
        assert r.status_code == 404

    def test_nonexistent_manufacturer_returns_404(
        self, test_client: TestClient, sc_ingredient: Ingredient
    ):
        r = test_client.post("/api/supply-routes", json={
            "ingredient_id": sc_ingredient.id,
            "manufacturer_id": 999999,
        })
        assert r.status_code == 404

    def test_get_by_id_includes_active_price_null(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        r = test_client.get(f"/api/supply-routes/{sc_supply_route.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == sc_supply_route.id
        assert body["active_price"] is None

    def test_get_nonexistent_returns_404(self, test_client: TestClient):
        assert test_client.get("/api/supply-routes/999999").status_code == 404

    def test_list_filter_by_ingredient(
        self, test_client: TestClient, sc_supply_route: SupplyRoute, sc_ingredient: Ingredient
    ):
        routes = test_client.get(f"/api/supply-routes?ingredient_id={sc_ingredient.id}").json()
        ids = [r["id"] for r in routes]
        assert sc_supply_route.id in ids

    def test_list_filter_by_active(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        test_client.delete(f"/api/supply-routes/{sc_supply_route.id}")
        active_ids = [r["id"] for r in test_client.get("/api/supply-routes?is_active=true").json()]
        assert sc_supply_route.id not in active_ids

    def test_deactivate(self, test_client: TestClient, sc_supply_route: SupplyRoute):
        r = test_client.delete(f"/api/supply-routes/{sc_supply_route.id}")
        assert r.status_code == 200
        assert test_client.get(f"/api/supply-routes/{sc_supply_route.id}").json()["is_active"] is False

    def test_active_price_endpoint_returns_null_when_no_price(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        r = test_client.get(f"/api/supply-routes/{sc_supply_route.id}/active-price")
        assert r.status_code == 200
        assert r.json() is None

    def test_get_refs_empty_initially(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        r = test_client.get(f"/api/supply-routes/{sc_supply_route.id}/refs")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_ref(
        self, test_client: TestClient, sc_supply_route: SupplyRoute, sc_ingredient: Ingredient
    ):
        r = test_client.post(f"/api/supply-routes/{sc_supply_route.id}/refs", json={
            "ingredient_id": sc_ingredient.id,
            "supply_route_id": sc_supply_route.id,
            "external_name": "Leche Pasteurizada 3.5%",
            "external_code": "LCH-001",
            "purchase_unit": "Bolsa 1L",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["external_name"] == "Leche Pasteurizada 3.5%"
        assert body["external_code"] == "LCH-001"
        assert body["ingredient_name"] == sc_ingredient.name

    def test_list_refs_after_creation(
        self, test_client: TestClient, sc_supply_route: SupplyRoute, sc_ingredient: Ingredient
    ):
        test_client.post(f"/api/supply-routes/{sc_supply_route.id}/refs", json={
            "ingredient_id": sc_ingredient.id,
            "supply_route_id": sc_supply_route.id,
            "external_name": "Ref Test",
            "purchase_unit": "Caja 12un",
        })
        refs = test_client.get(f"/api/supply-routes/{sc_supply_route.id}/refs").json()
        assert len(refs) >= 1
        assert any(r["external_name"] == "Ref Test" for r in refs)


# ===========================================================================
# Supply Route Assignments
# ===========================================================================

class TestSupplyRouteAssignmentsRouter:
    def _regional_payload(self, route_id: int, region_id: int, priority: int = 1) -> dict:
        return {
            "supply_route_id": route_id,
            "region_id": region_id,
            "priority": priority,
            "valid_from": str(date.today()),
            "assigned_by": "test_suite",
        }

    def _store_payload(self, route_id: int, store_id: int, priority: int = 1) -> dict:
        return {
            "supply_route_id": route_id,
            "store_id": store_id,
            "priority": priority,
            "valid_from": str(date.today()),
            "assigned_by": "test_suite",
        }

    def test_create_regional_assignment(
        self,
        test_client: TestClient,
        sc_supply_route: SupplyRoute,
        sc_region: Region,
    ):
        r = test_client.post(
            "/api/supply-route-assignments",
            json=self._regional_payload(sc_supply_route.id, sc_region.id),
        )
        assert r.status_code == 201
        body = r.json()
        assert body["region_id"] == sc_region.id
        assert body["region_name"] == sc_region.name
        assert body["priority"] == 1
        assert body["valid_until"] is None  # currently active

    def test_create_store_override(
        self,
        test_client: TestClient,
        sc_supply_route: SupplyRoute,
        sc_store: Store,
    ):
        r = test_client.post(
            "/api/supply-route-assignments",
            json=self._store_payload(sc_supply_route.id, sc_store.id),
        )
        assert r.status_code == 201
        body = r.json()
        assert body["store_id"] == sc_store.id
        assert body["store_name"] == sc_store.name
        assert body["region_id"] is None

    def test_both_region_and_store_returns_422(
        self,
        test_client: TestClient,
        sc_supply_route: SupplyRoute,
        sc_region: Region,
        sc_store: Store,
    ):
        r = test_client.post("/api/supply-route-assignments", json={
            "supply_route_id": sc_supply_route.id,
            "region_id": sc_region.id,
            "store_id": sc_store.id,
            "priority": 1,
            "valid_from": str(date.today()),
            "assigned_by": "test",
        })
        assert r.status_code == 422

    def test_neither_region_nor_store_returns_422(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        r = test_client.post("/api/supply-route-assignments", json={
            "supply_route_id": sc_supply_route.id,
            "priority": 1,
            "valid_from": str(date.today()),
            "assigned_by": "test",
        })
        assert r.status_code == 422

    def test_creating_new_regional_assignment_auto_closes_existing(
        self,
        test_client: TestClient,
        sc_supply_route: SupplyRoute,
        sc_region: Region,
        sc_ingredient: Ingredient,
        sc_manufacturer: Manufacturer,
    ):
        # Create first assignment
        first = test_client.post(
            "/api/supply-route-assignments",
            json=self._regional_payload(sc_supply_route.id, sc_region.id, priority=1),
        ).json()
        assert first["valid_until"] is None

        # Create a second route to use in the new assignment
        second_route = test_client.post("/api/supply-routes", json={
            "ingredient_id": sc_ingredient.id,
            "manufacturer_id": sc_manufacturer.id,
        }).json()

        # Assign the new route with the same region+priority → should auto-close first
        second = test_client.post(
            "/api/supply-route-assignments",
            json=self._regional_payload(second_route["id"], sc_region.id, priority=1),
        ).json()
        assert second["valid_until"] is None

        # Re-fetch the first assignment and verify it was closed
        first_refreshed = test_client.get(
            f"/api/supply-route-assignments/{first['id']}"
        ).json()
        assert first_refreshed["valid_until"] is not None

    def test_close_assignment(
        self, test_client: TestClient, sc_assignment: SupplyRouteAssignment
    ):
        r = test_client.post(
            f"/api/supply-route-assignments/{sc_assignment.id}/close",
            json={"change_reason": "test close"},
        )
        assert r.status_code == 200
        assert r.json()["valid_until"] == str(date.today())

    def test_close_already_closed_returns_409(
        self, test_client: TestClient, sc_assignment: SupplyRouteAssignment
    ):
        test_client.post(
            f"/api/supply-route-assignments/{sc_assignment.id}/close",
            json={},
        )
        r = test_client.post(
            f"/api/supply-route-assignments/{sc_assignment.id}/close",
            json={},
        )
        assert r.status_code == 409

    def test_list_active_only_excludes_closed(
        self, test_client: TestClient, sc_assignment: SupplyRouteAssignment
    ):
        test_client.post(
            f"/api/supply-route-assignments/{sc_assignment.id}/close", json={}
        )
        active = test_client.get(
            f"/api/supply-route-assignments?supply_route_id={sc_assignment.supply_route_id}&active_only=true"
        ).json()
        ids = [a["id"] for a in active]
        assert sc_assignment.id not in ids

    def test_list_all_includes_closed(
        self, test_client: TestClient, sc_assignment: SupplyRouteAssignment
    ):
        test_client.post(
            f"/api/supply-route-assignments/{sc_assignment.id}/close", json={}
        )
        all_assignments = test_client.get(
            f"/api/supply-route-assignments?supply_route_id={sc_assignment.supply_route_id}&active_only=false"
        ).json()
        ids = [a["id"] for a in all_assignments]
        assert sc_assignment.id in ids

    def test_nonexistent_route_returns_404(
        self, test_client: TestClient, sc_region: Region
    ):
        r = test_client.post("/api/supply-route-assignments", json={
            "supply_route_id": 999999,
            "region_id": sc_region.id,
            "priority": 1,
            "valid_from": str(date.today()),
            "assigned_by": "test",
        })
        assert r.status_code == 404


# ===========================================================================
# Supply Route Prices
# ===========================================================================

class TestSupplyRoutePricesRouter:
    def _price_payload(self, route_id: int, list_price: int = 5000, qargo_price: int = 4500) -> dict:
        return {
            "supply_route_id": route_id,
            "list_price": str(list_price),
            "qargo_price": str(qargo_price),
            "currency_code": "COP",
            "price_per_unit": "por litro",
            "source": "contrato_2024",
            "created_by": "test_suite",
        }

    def test_create_price(self, test_client: TestClient, sc_supply_route: SupplyRoute):
        r = test_client.post(
            "/api/supply-route-prices", json=self._price_payload(sc_supply_route.id)
        )
        assert r.status_code == 201
        body = r.json()
        assert Decimal(body["list_price"]) == 5000
        assert Decimal(body["qargo_price"]) == 4500
        assert body["currency_code"] == "COP"
        assert body["valid_until"] is None
        assert body["valid_from"] == str(date.today())

    def test_active_price_endpoint_returns_price_after_creation(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        test_client.post(
            "/api/supply-route-prices", json=self._price_payload(sc_supply_route.id)
        )
        r = test_client.get(f"/api/supply-route-prices/route/{sc_supply_route.id}/active")
        assert r.status_code == 200
        assert Decimal(r.json()["qargo_price"]) == 4500

    def test_create_second_price_closes_first(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        first = test_client.post(
            "/api/supply-route-prices", json=self._price_payload(sc_supply_route.id, 5000, 4500)
        ).json()
        assert first["valid_until"] is None

        test_client.post(
            "/api/supply-route-prices",
            json=self._price_payload(sc_supply_route.id, 5500, 5000),
        )

        # Active price should now be the second one
        active = test_client.get(
            f"/api/supply-route-prices/route/{sc_supply_route.id}/active"
        ).json()
        assert Decimal(active["qargo_price"]) == 5000

    def test_price_history_contains_both_prices(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        test_client.post("/api/supply-route-prices", json=self._price_payload(sc_supply_route.id, 5000, 4500))
        test_client.post("/api/supply-route-prices", json=self._price_payload(sc_supply_route.id, 6000, 5500))
        history = test_client.get(
            f"/api/supply-route-prices/route/{sc_supply_route.id}/history"
        ).json()
        assert len(history) == 2

    def test_first_price_closed_after_second_created(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        test_client.post("/api/supply-route-prices", json=self._price_payload(sc_supply_route.id, 5000, 4500))
        test_client.post("/api/supply-route-prices", json=self._price_payload(sc_supply_route.id, 6000, 5500))
        history = test_client.get(
            f"/api/supply-route-prices/route/{sc_supply_route.id}/history"
        ).json()
        closed = [h for h in history if h["valid_until"] is not None]
        assert len(closed) == 1

    def test_qargo_price_greater_than_list_returns_422(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        r = test_client.post("/api/supply-route-prices", json={
            "supply_route_id": sc_supply_route.id,
            "list_price": "1000",
            "qargo_price": "1500",   # > list_price
            "currency_code": "COP",
            "price_per_unit": "por kg",
            "created_by": "test",
        })
        assert r.status_code == 422

    def test_nonexistent_route_returns_404(self, test_client: TestClient):
        r = test_client.post("/api/supply-route-prices", json={
            "supply_route_id": 999999,
            "list_price": "1000",
            "qargo_price": "900",
            "currency_code": "COP",
            "price_per_unit": "por kg",
            "created_by": "test",
        })
        assert r.status_code == 404

    def test_active_price_returns_null_before_creation(
        self, test_client: TestClient, sc_supply_route: SupplyRoute
    ):
        r = test_client.get(f"/api/supply-route-prices/route/{sc_supply_route.id}/active")
        assert r.status_code == 200
        assert r.json() is None


# ===========================================================================
# Supply Chain Utility (resolve-route)
# ===========================================================================

class TestResolveRouteEndpoint:
    def test_nonexistent_ingredient_returns_404(
        self, test_client: TestClient, sc_store: Store
    ):
        r = test_client.get(
            f"/api/supply-chain/resolve-route?ingredient_id=999999&store_id={sc_store.id}"
        )
        assert r.status_code == 404

    def test_nonexistent_store_returns_404(
        self, test_client: TestClient, sc_ingredient: Ingredient
    ):
        r = test_client.get(
            f"/api/supply-chain/resolve-route?ingredient_id={sc_ingredient.id}&store_id=999999"
        )
        assert r.status_code == 404

    def test_no_route_configured_returns_resolved_false(
        self, test_client: TestClient, sc_ingredient: Ingredient, sc_store: Store
    ):
        """A store with a region but no route assignment returns resolved=False."""
        r = test_client.get(
            f"/api/supply-chain/resolve-route"
            f"?ingredient_id={sc_ingredient.id}&store_id={sc_store.id}"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"] is False
        assert body["supply_route_id"] is None

    def test_full_happy_path_resolves_regional_route(
        self,
        test_client: TestClient,
        sc_ingredient: Ingredient,
        sc_supply_route: SupplyRoute,
        sc_region: Region,
        sc_store: Store,
        sc_assignment: SupplyRouteAssignment,
    ):
        """With region assignment active, the store resolves to the regional route."""
        r = test_client.get(
            f"/api/supply-chain/resolve-route"
            f"?ingredient_id={sc_ingredient.id}&store_id={sc_store.id}"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["resolved"] is True
        assert body["supply_route_id"] == sc_supply_route.id
        assert body["scope"] == "region_default"
        assert body["priority"] == 1
        assert body["active_price"] is None  # no price set yet

    def test_route_with_price_includes_price_in_response(
        self,
        test_client: TestClient,
        sc_ingredient: Ingredient,
        sc_supply_route: SupplyRoute,
        sc_region: Region,
        sc_store: Store,
        sc_assignment: SupplyRouteAssignment,
    ):
        """After setting a price, resolve-route response includes active_price."""
        test_client.post("/api/supply-route-prices", json={
            "supply_route_id": sc_supply_route.id,
            "list_price": "5000",
            "qargo_price": "4500",
            "currency_code": "COP",
            "price_per_unit": "por litro",
            "created_by": "test",
        })
        r = test_client.get(
            f"/api/supply-chain/resolve-route"
            f"?ingredient_id={sc_ingredient.id}&store_id={sc_store.id}"
        )
        body = r.json()
        assert body["resolved"] is True
        assert body["active_price"] is not None
        assert Decimal(body["active_price"]["qargo_price"]) == 4500
        assert body["active_price"]["currency_code"] == "COP"

    def test_bulk_resolve_with_no_routes_returns_all_unresolved(
        self, test_client: TestClient, sc_ingredient: Ingredient, sc_store: Store
    ):
        r = test_client.get(
            f"/api/supply-chain/resolve-routes-bulk"
            f"?store_id={sc_store.id}&ingredient_ids={sc_ingredient.id}"
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["resolved"] is False

    def test_bulk_resolve_with_route_returns_resolved(
        self,
        test_client: TestClient,
        sc_ingredient: Ingredient,
        sc_store: Store,
        sc_assignment: SupplyRouteAssignment,
        sc_supply_route: SupplyRoute,
    ):
        r = test_client.get(
            f"/api/supply-chain/resolve-routes-bulk"
            f"?store_id={sc_store.id}&ingredient_ids={sc_ingredient.id}"
        )
        assert r.status_code == 200
        body = r.json()
        assert body[0]["resolved"] is True
        assert body[0]["supply_route_id"] == sc_supply_route.id

    def test_bulk_resolve_invalid_ids_returns_422(
        self, test_client: TestClient, sc_store: Store
    ):
        r = test_client.get(
            f"/api/supply-chain/resolve-routes-bulk"
            f"?store_id={sc_store.id}&ingredient_ids=abc,def"
        )
        assert r.status_code == 422
