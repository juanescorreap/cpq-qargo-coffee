"""Unit tests for supply chain Pydantic schemas.

Pure Python — no database connection required. Verifies validators,
defaults, and field coercions before data reaches the router layer.
"""

from datetime import date
from decimal import Decimal

import pytest

from backend.schemas.cost import IngredientCostDetail
from backend.schemas.store import StoreResponse, StoreUpdate
from backend.schemas.supply_chain import (
    DistributorCreate,
    IngredientSupplierRefCreate,
    ManufacturerCreate,
    RegionCreate,
    RegionUpdate,
    SupplyRouteAssignmentCreate,
    SupplyRoutePriceCreate,
)


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------

class TestRegionCreate:
    def test_code_uppercased(self):
        r = RegionCreate(name="Bogotá", code="bog")
        assert r.code == "BOG"

    def test_code_strips_whitespace(self):
        r = RegionCreate(name="Medellín", code="  med  ")
        assert r.code == "MED"

    def test_blank_code_raises(self):
        with pytest.raises(Exception):
            RegionCreate(name="Bogotá", code="   ")

    def test_blank_name_raises(self):
        with pytest.raises(Exception):
            RegionCreate(name="  ", code="BOG")

    def test_default_country_code_is_colombia(self):
        r = RegionCreate(name="Bogotá", code="BOG")
        assert r.country_code == "CO"

    def test_country_code_uppercased(self):
        r = RegionCreate(name="Test", code="TST", country_code="co")
        assert r.country_code == "CO"


class TestRegionUpdate:
    def test_all_fields_optional(self):
        u = RegionUpdate()
        assert u.name is None
        assert u.code is None
        assert u.is_active is None

    def test_partial_update(self):
        u = RegionUpdate(is_active=False)
        assert u.is_active is False
        assert u.name is None

    def test_code_uppercased_in_update(self):
        u = RegionUpdate(code="med")
        assert u.code == "MED"


# ---------------------------------------------------------------------------
# Manufacturer
# ---------------------------------------------------------------------------

class TestManufacturerCreate:
    def test_blank_name_raises(self):
        with pytest.raises(Exception):
            ManufacturerCreate(name="  ")

    def test_default_country_code(self):
        m = ManufacturerCreate(name="Alimentos S.A.")
        assert m.country_code == "CO"

    def test_optional_fields_none_by_default(self):
        m = ManufacturerCreate(name="Alimentos S.A.")
        assert m.tax_id is None
        assert m.website is None

    def test_all_fields_accepted(self):
        m = ManufacturerCreate(
            name="Alimentos S.A.",
            country_code="US",
            tax_id="900000001",
            website="https://alimentos.co",
        )
        assert m.tax_id == "900000001"
        assert m.website == "https://alimentos.co"


# ---------------------------------------------------------------------------
# Distributor
# ---------------------------------------------------------------------------

class TestDistributorCreate:
    def test_blank_name_raises(self):
        with pytest.raises(Exception):
            DistributorCreate(name="")

    def test_optional_contact_fields_default_none(self):
        d = DistributorCreate(name="Distribuidora Norte")
        assert d.contact_email is None
        assert d.contact_phone is None

    def test_full_fields_accepted(self):
        d = DistributorCreate(
            name="Distribuidora Norte",
            contact_email="norte@dist.co",
            contact_phone="+57 300 123 4567",
            tax_id="830000001-5",
        )
        assert d.contact_email == "norte@dist.co"


# ---------------------------------------------------------------------------
# SupplyRoutePriceCreate
# ---------------------------------------------------------------------------

class TestSupplyRoutePriceCreate:
    def _valid(self, **kwargs) -> SupplyRoutePriceCreate:
        base = dict(
            supply_route_id=1,
            list_price=Decimal("1000"),
            qargo_price=Decimal("900"),
            currency_code="COP",
            price_per_unit="por kg",
            created_by="tester",
        )
        base.update(kwargs)
        return SupplyRoutePriceCreate(**base)

    def test_valid_price_accepted(self):
        p = self._valid()
        assert p.qargo_price == Decimal("900")

    def test_qargo_price_greater_than_list_raises(self):
        with pytest.raises(Exception):
            self._valid(list_price=Decimal("1000"), qargo_price=Decimal("1100"))

    def test_qargo_price_equal_list_is_valid(self):
        p = self._valid(list_price=Decimal("1000"), qargo_price=Decimal("1000"))
        assert p.qargo_price == p.list_price

    def test_zero_list_price_raises(self):
        with pytest.raises(Exception):
            self._valid(list_price=Decimal("0"), qargo_price=Decimal("0"))

    def test_negative_price_raises(self):
        with pytest.raises(Exception):
            self._valid(list_price=Decimal("-500"), qargo_price=Decimal("-500"))

    def test_currency_code_uppercased(self):
        p = self._valid(currency_code="cop")
        assert p.currency_code == "COP"

    def test_currency_code_too_short_raises(self):
        with pytest.raises(Exception):
            self._valid(currency_code="CO")

    def test_currency_code_too_long_raises(self):
        with pytest.raises(Exception):
            self._valid(currency_code="COPS")

    def test_numeric_currency_code_raises(self):
        with pytest.raises(Exception):
            self._valid(currency_code="123")

    def test_blank_created_by_raises(self):
        with pytest.raises(Exception):
            self._valid(created_by="  ")

    def test_default_currency_is_cop(self):
        p = SupplyRoutePriceCreate(
            supply_route_id=1,
            list_price=Decimal("1000"),
            qargo_price=Decimal("800"),
            price_per_unit="por kg",
            created_by="test",
        )
        assert p.currency_code == "COP"


# ---------------------------------------------------------------------------
# SupplyRouteAssignmentCreate
# ---------------------------------------------------------------------------

class TestSupplyRouteAssignmentCreate:
    def _valid(self, **kwargs) -> SupplyRouteAssignmentCreate:
        base = dict(
            supply_route_id=1,
            region_id=1,
            priority=1,
            valid_from=date.today(),
            assigned_by="tester",
        )
        base.update(kwargs)
        return SupplyRouteAssignmentCreate(**base)

    def test_valid_assignment(self):
        a = self._valid()
        assert a.priority == 1

    def test_priority_zero_raises(self):
        with pytest.raises(Exception):
            self._valid(priority=0)

    def test_priority_negative_raises(self):
        with pytest.raises(Exception):
            self._valid(priority=-1)

    def test_blank_assigned_by_raises(self):
        with pytest.raises(Exception):
            self._valid(assigned_by="   ")

    def test_default_priority_is_one(self):
        a = SupplyRouteAssignmentCreate(
            supply_route_id=1,
            region_id=1,
            valid_from=date.today(),
            assigned_by="tester",
        )
        assert a.priority == 1


# ---------------------------------------------------------------------------
# IngredientSupplierRefCreate
# ---------------------------------------------------------------------------

class TestIngredientSupplierRefCreate:
    def test_blank_external_name_raises(self):
        with pytest.raises(Exception):
            IngredientSupplierRefCreate(
                ingredient_id=1, supply_route_id=1,
                external_name="  ", purchase_unit="kg",
            )

    def test_blank_purchase_unit_raises(self):
        with pytest.raises(Exception):
            IngredientSupplierRefCreate(
                ingredient_id=1, supply_route_id=1,
                external_name="Leche Fresca", purchase_unit="  ",
            )

    def test_optional_external_code(self):
        ref = IngredientSupplierRefCreate(
            ingredient_id=1, supply_route_id=1,
            external_name="Leche Fresca", purchase_unit="Bolsa 1L",
        )
        assert ref.external_code is None

    def test_valid_with_all_fields(self):
        ref = IngredientSupplierRefCreate(
            ingredient_id=1, supply_route_id=1,
            external_name="Leche Pasteurizada",
            external_code="SKU-1234",
            purchase_unit="Bolsa 1L",
            units_per_pack=Decimal("12"),
        )
        assert ref.external_code == "SKU-1234"
        assert ref.units_per_pack == Decimal("12")


# ---------------------------------------------------------------------------
# Updated StoreSchema (region_id + default_currency_code added in Fase A)
# ---------------------------------------------------------------------------

class TestUpdatedStoreSchema:
    def test_store_response_has_region_id_field(self):
        assert "region_id" in StoreResponse.model_fields

    def test_store_response_region_id_is_optional(self):
        field = StoreResponse.model_fields["region_id"]
        assert field.default is None or not field.is_required()

    def test_store_response_has_currency_code_field(self):
        assert "default_currency_code" in StoreResponse.model_fields

    def test_store_update_accepts_region_id(self):
        u = StoreUpdate(region_id=5)
        assert u.region_id == 5

    def test_store_update_accepts_currency_code(self):
        u = StoreUpdate(default_currency_code="USD")
        assert u.default_currency_code == "USD"

    def test_store_update_all_none_by_default(self):
        u = StoreUpdate()
        assert u.region_id is None
        assert u.default_currency_code is None


# ---------------------------------------------------------------------------
# Updated IngredientCostDetail (price_source + supply chain metadata)
# ---------------------------------------------------------------------------

class TestUpdatedIngredientCostDetail:
    def _detail(self, **kwargs) -> IngredientCostDetail:
        base = dict(
            name="Milk",
            quantity=Decimal("200"),
            unit="ml",
            unit_cost=Decimal("4.5"),
            total_cost=Decimal("900"),
        )
        base.update(kwargs)
        return IngredientCostDetail(**base)

    def test_price_source_defaults_to_base(self):
        assert self._detail().price_source == "base"

    def test_supply_route_id_defaults_none(self):
        assert self._detail().supply_route_id is None

    def test_route_scope_defaults_none(self):
        assert self._detail().route_scope is None

    def test_explicit_price_source_accepted(self):
        d = self._detail(price_source="route", supply_route_id=42, route_scope="region_default")
        assert d.price_source == "route"
        assert d.supply_route_id == 42
        assert d.route_scope == "region_default"

    def test_store_override_source_accepted(self):
        d = self._detail(price_source="store_override")
        assert d.price_source == "store_override"
