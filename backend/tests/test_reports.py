"""Tests for ReportGenerator.

Covers the four public methods:
    - product_costs_report
    - margin_analysis_report
    - competitor_benchmark_report
    - price_impact_simulation

Data strategy:
    Each test builds only the records it needs within the isolated session
    from `test_db` (automatic rollback).  Fixtures from conftest.py are
    reused for the base product+recipe+size scenario.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend.models import (
    Competitor,
    CompetitorProduct,
    Ingredient,
    Product,
    ProductCompetitorMatch,
    ProductPricing,
    ProductSize,
    RecipeIngredient,
)
from backend.services.report_generator import ReportGenerator


# ============================================================================
# Helpers
# ============================================================================

def _make_pricing(
    db: Session,
    product: Product,
    size: ProductSize,
    cost: Decimal,
    price: Decimal,
    store_id=None,
) -> ProductPricing:
    """Insert a ProductPricing with the given values and return it."""
    p = ProductPricing(
        product_id=product.id,
        size_id=size.id,
        store_id=store_id,
        calculated_cost=cost,
        final_price=price,
        is_manual_price=True,
        effective_date=date.today(),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ============================================================================
# 1. product_costs_report
# ============================================================================

class TestProductCostsReport:

    def test_returns_list(self, test_db: Session):
        """Always returns a list, even with no products."""
        gen = ReportGenerator(test_db)
        result = gen.product_costs_report()
        assert isinstance(result, list)

    def test_structure_per_product(
        self,
        test_db: Session,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """Each entry has the required keys with correct types."""
        gen = ReportGenerator(test_db)
        result = gen.product_costs_report()

        products_in_report = [r for r in result if r["product_id"] == sample_product.id]
        assert len(products_in_report) == 1

        entry = products_in_report[0]
        assert entry["product_name"] == sample_product.name
        assert entry["category"] == sample_product.category
        assert isinstance(entry["sizes"], list)

    def test_size_entry_has_cost_and_breakdown(
        self,
        test_db: Session,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """Each size has a positive 'cost' and 'cost_breakdown' with the four keys."""
        gen = ReportGenerator(test_db)
        result = gen.product_costs_report()

        entry = next(r for r in result if r["product_id"] == sample_product.id)
        assert len(entry["sizes"]) >= 1

        size_entry = entry["sizes"][0]
        assert size_entry["size_name"] == sample_size.size_name
        assert isinstance(size_entry["cost"], Decimal)
        assert size_entry["cost"] > Decimal("0")

        breakdown = size_entry["cost_breakdown"]
        assert "ingredients" in breakdown
        assert "sub_recipes" in breakdown
        assert "packaging" in breakdown
        assert "labor" in breakdown

    def test_product_without_recipe_is_included_but_has_no_sizes(
        self, test_db: Session
    ):
        """An active product without a recipe appears in the report with sizes=[]."""
        product = Product(name="Test No Recipe", is_sub_recipe=False)
        test_db.add(product)
        test_db.commit()

        gen = ReportGenerator(test_db)
        result = gen.product_costs_report()

        entry = next((r for r in result if r["product_id"] == product.id), None)
        assert entry is not None
        assert entry["sizes"] == []

    def test_store_id_parameter_accepted(
        self,
        test_db: Session,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """store_id=None and a valid store_id do not raise an exception."""
        gen = ReportGenerator(test_db)
        result_global = gen.product_costs_report(store_id=None)
        result_store  = gen.product_costs_report(store_id=9999)  # non-existent store → fallback
        assert isinstance(result_global, list)
        assert isinstance(result_store, list)

    def test_inactive_products_excluded(self, test_db: Session):
        """Products with is_active=False do not appear in the report."""
        inactive = Product(name="Test Inactive", is_sub_recipe=False, is_active=False)
        test_db.add(inactive)
        test_db.commit()

        gen = ReportGenerator(test_db)
        result = gen.product_costs_report()
        ids = [r["product_id"] for r in result]
        assert inactive.id not in ids

    def test_ordered_by_name(self, test_db: Session):
        """The report returns products sorted alphabetically by name."""
        for name in ["Zebra Latte", "Americano", "Mocha"]:
            test_db.add(Product(name=name, is_sub_recipe=False))
        test_db.commit()

        gen = ReportGenerator(test_db)
        result = gen.product_costs_report()
        names = [r["product_name"] for r in result]
        assert names == sorted(names)


# ============================================================================
# 2. margin_analysis_report
# ============================================================================

class TestMarginAnalysisReport:

    def test_returns_four_keys(self, test_db: Session):
        """The report always returns the four categories, even with no data."""
        gen = ReportGenerator(test_db)
        result = gen.margin_analysis_report()
        assert set(result.keys()) == {"negative_margin", "low_margin", "healthy_margin", "high_margin"}

    def test_negative_margin_classification(self, test_db: Session):
        """A product sold below cost falls into negative_margin.

        Margin = (10 000 − 15 000) / 10 000 × 100 = −50 %
        """
        product = Product(name="Test Neg Margin", is_sub_recipe=False)
        test_db.add(product)
        test_db.commit()
        size = ProductSize(product_id=product.id, size_name="S", scale_factor=Decimal("1"), is_default=True)
        test_db.add(size)
        test_db.commit()
        _make_pricing(test_db, product, size, cost=Decimal("15000"), price=Decimal("10000"))

        gen = ReportGenerator(test_db)
        result = gen.margin_analysis_report()
        names = [i["product_name"] for i in result["negative_margin"]]
        assert "Test Neg Margin" in names

    def test_low_margin_classification(self, test_db: Session):
        """Margin between 0 % and 30 % falls into low_margin.

        Margin = (12 000 − 10 000) / 12 000 × 100 ≈ 16.67 %
        """
        product = Product(name="Test Low Margin", is_sub_recipe=False)
        test_db.add(product)
        test_db.commit()
        size = ProductSize(product_id=product.id, size_name="S", scale_factor=Decimal("1"), is_default=True)
        test_db.add(size)
        test_db.commit()
        _make_pricing(test_db, product, size, cost=Decimal("10000"), price=Decimal("12000"))

        gen = ReportGenerator(test_db)
        result = gen.margin_analysis_report()
        names = [i["product_name"] for i in result["low_margin"]]
        assert "Test Low Margin" in names

    def test_healthy_margin_classification(self, test_db: Session):
        """Margin between 30 % and 80 % falls into healthy_margin.

        Margin = (20 000 − 10 000) / 20 000 × 100 = 50 %
        """
        product = Product(name="Test Healthy Margin", is_sub_recipe=False)
        test_db.add(product)
        test_db.commit()
        size = ProductSize(product_id=product.id, size_name="S", scale_factor=Decimal("1"), is_default=True)
        test_db.add(size)
        test_db.commit()
        _make_pricing(test_db, product, size, cost=Decimal("10000"), price=Decimal("20000"))

        gen = ReportGenerator(test_db)
        result = gen.margin_analysis_report()
        names = [i["product_name"] for i in result["healthy_margin"]]
        assert "Test Healthy Margin" in names

    def test_high_margin_classification(self, test_db: Session):
        """Margin above 80 % falls into high_margin.

        Margin = (50 000 − 5 000) / 50 000 × 100 = 90 %
        """
        product = Product(name="Test High Margin", is_sub_recipe=False)
        test_db.add(product)
        test_db.commit()
        size = ProductSize(product_id=product.id, size_name="S", scale_factor=Decimal("1"), is_default=True)
        test_db.add(size)
        test_db.commit()
        _make_pricing(test_db, product, size, cost=Decimal("5000"), price=Decimal("50000"))

        gen = ReportGenerator(test_db)
        result = gen.margin_analysis_report()
        names = [i["product_name"] for i in result["high_margin"]]
        assert "Test High Margin" in names

    def test_item_structure(self, test_db: Session):
        """Each report item has the expected keys and types."""
        product = Product(name="Test Struct Margin", is_sub_recipe=False)
        test_db.add(product)
        test_db.commit()
        size = ProductSize(product_id=product.id, size_name="M", scale_factor=Decimal("1"), is_default=True)
        test_db.add(size)
        test_db.commit()
        _make_pricing(test_db, product, size, cost=Decimal("8000"), price=Decimal("16000"))

        gen = ReportGenerator(test_db)
        result = gen.margin_analysis_report()
        all_items = (
            result["negative_margin"] + result["low_margin"]
            + result["healthy_margin"] + result["high_margin"]
        )
        target = next(i for i in all_items if i["product_name"] == "Test Struct Margin")

        assert isinstance(target["product_name"], str)
        assert isinstance(target["size_name"],    str)
        assert isinstance(target["cost"],         float)
        assert isinstance(target["price"],        float)
        assert isinstance(target["margin_pct"],   float)

    def test_null_price_skipped(self, test_db: Session):
        """Pricings with final_price=0 or calculated_cost=0 do not cause division by zero."""
        product = Product(name="Test Zero Price", is_sub_recipe=False)
        test_db.add(product)
        test_db.commit()
        size = ProductSize(product_id=product.id, size_name="S", scale_factor=Decimal("1"), is_default=True)
        test_db.add(size)
        test_db.commit()

        bad = ProductPricing(
            product_id=product.id,
            size_id=size.id,
            store_id=None,
            calculated_cost=Decimal("0"),
            final_price=Decimal("0"),
            is_manual_price=True,
            effective_date=date.today(),
        )
        test_db.add(bad)
        test_db.commit()

        gen = ReportGenerator(test_db)
        result = gen.margin_analysis_report()
        all_items = (
            result["negative_margin"] + result["low_margin"]
            + result["healthy_margin"] + result["high_margin"]
        )
        names = [i["product_name"] for i in all_items]
        assert "Test Zero Price" not in names

    def test_negative_margin_sorted_ascending(self, test_db: Session):
        """negative_margin is sorted ascending by margin."""
        for cost, price in [(20000, 10000), (18000, 10000)]:
            product = Product(name=f"Test NegSort {cost}", is_sub_recipe=False)
            test_db.add(product)
            test_db.commit()
            size = ProductSize(product_id=product.id, size_name="S", scale_factor=Decimal("1"), is_default=True)
            test_db.add(size)
            test_db.commit()
            _make_pricing(test_db, product, size, Decimal(str(cost)), Decimal("10000"))

        gen = ReportGenerator(test_db)
        result = gen.margin_analysis_report()
        margins = [i["margin_pct"] for i in result["negative_margin"]]
        assert margins == sorted(margins)


# ============================================================================
# 3. competitor_benchmark_report
# ============================================================================

class TestCompetitorBenchmarkReport:

    def test_returns_list(self, test_db: Session):
        """Always returns a list, even with no matches."""
        gen = ReportGenerator(test_db)
        assert isinstance(gen.competitor_benchmark_report(), list)

    def test_empty_without_matches(self, test_db: Session):
        """With no records in ProductCompetitorMatch, the report is empty."""
        gen = ReportGenerator(test_db)
        assert gen.competitor_benchmark_report() == []

    def _build_match(
        self,
        db: Session,
        our_price: Decimal,
        comp_price: Decimal,
    ):
        """Helper that creates the full chain of entities for a benchmark."""
        competitor = Competitor(name="Test Competitor SA")
        db.add(competitor)
        db.commit()

        comp_product = CompetitorProduct(
            competitor_id=competitor.id,
            product_name="Comp Cappuccino",
            price=comp_price,
        )
        db.add(comp_product)
        db.commit()

        our_product = Product(name="Our Cappuccino Bench", is_sub_recipe=False)
        db.add(our_product)
        db.commit()

        our_size = ProductSize(
            product_id=our_product.id,
            size_name="medium",
            scale_factor=Decimal("1"),
            is_default=True,
        )
        db.add(our_size)
        db.commit()

        match = ProductCompetitorMatch(
            our_product_id=our_product.id,
            our_size_id=our_size.id,
            competitor_product_id=comp_product.id,
        )
        db.add(match)
        db.commit()

        _make_pricing(db, our_product, our_size, cost=Decimal("8000"), price=our_price)
        return our_product, our_size, competitor, comp_product

    def test_price_difference_when_more_expensive(self, test_db: Session):
        """When we are more expensive, price_difference and price_difference_pct are positive."""
        self._build_match(test_db, our_price=Decimal("15000"), comp_price=Decimal("12000"))

        gen = ReportGenerator(test_db)
        result = gen.competitor_benchmark_report()

        assert len(result) == 1
        row = result[0]
        assert row["price_difference"] > 0
        assert row["price_difference_pct"] > 0

    def test_price_difference_when_cheaper(self, test_db: Session):
        """When we are cheaper, price_difference and price_difference_pct are negative."""
        self._build_match(test_db, our_price=Decimal("10000"), comp_price=Decimal("12000"))

        gen = ReportGenerator(test_db)
        result = gen.competitor_benchmark_report()

        assert len(result) == 1
        row = result[0]
        assert row["price_difference"] < 0
        assert row["price_difference_pct"] < 0

    def test_price_difference_values(self, test_db: Session):
        """The numerical difference values are correct.

        our_price = 15 000, comp_price = 12 000
        diff     = 3 000
        diff_pct = 3 000 / 12 000 × 100 = 25 %
        """
        self._build_match(test_db, our_price=Decimal("15000"), comp_price=Decimal("12000"))

        gen = ReportGenerator(test_db)
        result = gen.competitor_benchmark_report()

        row = result[0]
        assert abs(row["price_difference"]     - 3000) < 1
        assert abs(row["price_difference_pct"] - 25.0) < 0.1

    def test_item_structure(self, test_db: Session):
        """Each item has the eight required keys."""
        self._build_match(test_db, our_price=Decimal("14000"), comp_price=Decimal("13000"))

        gen = ReportGenerator(test_db)
        result = gen.competitor_benchmark_report()

        row = result[0]
        for key in (
            "our_product", "our_size", "our_price",
            "competitor", "competitor_product", "competitor_price",
            "price_difference", "price_difference_pct",
        ):
            assert key in row, f"Missing key: {key}"

    def test_skips_match_without_pricing(self, test_db: Session):
        """A match without an associated ProductPricing does not appear in the report."""
        competitor = Competitor(name="No Pricing Comp")
        test_db.add(competitor)
        test_db.commit()

        comp_product = CompetitorProduct(
            competitor_id=competitor.id,
            product_name="Some Coffee",
            price=Decimal("12000"),
        )
        test_db.add(comp_product)
        test_db.commit()

        our_product = Product(name="Our No Pricing Product", is_sub_recipe=False)
        test_db.add(our_product)
        test_db.commit()

        our_size = ProductSize(
            product_id=our_product.id, size_name="S",
            scale_factor=Decimal("1"), is_default=True,
        )
        test_db.add(our_size)
        test_db.commit()

        match = ProductCompetitorMatch(
            our_product_id=our_product.id,
            our_size_id=our_size.id,
            competitor_product_id=comp_product.id,
        )
        test_db.add(match)
        test_db.commit()

        gen = ReportGenerator(test_db)
        result = gen.competitor_benchmark_report()
        names = [r["our_product"] for r in result]
        assert "Our No Pricing Product" not in names

    def test_sorted_by_price_difference_pct_desc(self, test_db: Session):
        """Results are sorted by price_difference_pct descending."""
        for our, comp in [(20000, 10000), (11000, 10000)]:
            self._build_match(test_db, Decimal(str(our)), Decimal(str(comp)))

        gen = ReportGenerator(test_db)
        result = gen.competitor_benchmark_report()

        pcts = [r["price_difference_pct"] for r in result]
        assert pcts == sorted(pcts, reverse=True)


# ============================================================================
# 4. price_impact_simulation
# ============================================================================

class TestPriceImpactSimulation:

    def test_invalid_ingredient_returns_error(self, test_db: Session):
        """A non-existent ingredient_id returns {'error': ...}."""
        gen = ReportGenerator(test_db)
        result = gen.price_impact_simulation(ingredient_id=999999, percent_change=Decimal("10"))
        assert "error" in result
        assert "999999" in result["error"]

    def test_ingredient_without_price_returns_error(self, test_db: Session):
        """An ingredient with purchase_price=None returns {'error': ...}."""
        ing = Ingredient(
            name="Test No Price Ing",
            usage_unit="ml",
            conversion_factor=Decimal("1000"),
        )
        test_db.add(ing)
        test_db.commit()

        gen = ReportGenerator(test_db)
        result = gen.price_impact_simulation(ing.id, Decimal("5"))
        assert "error" in result

    def test_return_structure(
        self,
        test_db: Session,
        sample_ingredient: Ingredient,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """The result has the required top-level keys."""
        gen = ReportGenerator(test_db)
        result = gen.price_impact_simulation(
            ingredient_id=sample_ingredient.id,
            percent_change=Decimal("10"),
        )

        assert "error" not in result
        assert result["ingredient"]     == sample_ingredient.name
        assert result["percent_change"] == Decimal("10")
        assert isinstance(result["affected_products"], list)

        for key in ("current_price", "new_price", "percent_change", "affected_products"):
            assert key in result

    def test_new_price_reflects_percent_change(
        self,
        test_db: Session,
        sample_ingredient: Ingredient,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """new_price = current_price × (1 + percent_change / 100)."""
        gen = ReportGenerator(test_db)

        result_up   = gen.price_impact_simulation(sample_ingredient.id, Decimal("20"))
        result_down = gen.price_impact_simulation(sample_ingredient.id, Decimal("-10"))

        base = Decimal(str(sample_ingredient.purchase_price))
        assert abs(result_up["new_price"]   - base * Decimal("1.20")) < Decimal("0.01")
        assert abs(result_down["new_price"] - base * Decimal("0.90")) < Decimal("0.01")

    def test_affected_product_appears_in_results(
        self,
        test_db: Session,
        sample_ingredient: Ingredient,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """The product that uses the ingredient appears in affected_products."""
        gen = ReportGenerator(test_db)
        result = gen.price_impact_simulation(sample_ingredient.id, Decimal("10"))

        products = [p["product"] for p in result["affected_products"]]
        assert sample_product.name in products

    def test_cost_increases_on_price_increase(
        self,
        test_db: Session,
        sample_ingredient: Ingredient,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """An increase in the ingredient price raises the product cost."""
        gen = ReportGenerator(test_db)
        result = gen.price_impact_simulation(sample_ingredient.id, Decimal("15"))

        row = next(
            p for p in result["affected_products"]
            if p["product"] == sample_product.name
        )
        assert row["new_cost"] > row["current_cost"]
        assert row["cost_increase"] > Decimal("0")
        assert row["cost_increase_pct"] > Decimal("0")

    def test_cost_decreases_on_price_reduction(
        self,
        test_db: Session,
        sample_ingredient: Ingredient,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """A reduction in the ingredient price lowers the product cost."""
        gen = ReportGenerator(test_db)
        result = gen.price_impact_simulation(sample_ingredient.id, Decimal("-20"))

        row = next(
            p for p in result["affected_products"]
            if p["product"] == sample_product.name
        )
        assert row["new_cost"] < row["current_cost"]
        assert row["cost_increase"] < Decimal("0")
        assert row["cost_increase_pct"] < Decimal("0")

    def test_does_not_persist_price_change(
        self,
        test_db: Session,
        sample_ingredient: Ingredient,
        sample_product: Product,
        sample_size: ProductSize,
        sample_recipe: RecipeIngredient,
    ):
        """The simulation does not modify the ingredient's real purchase_price in the DB."""
        original_price = Decimal(str(sample_ingredient.purchase_price))
        gen = ReportGenerator(test_db)
        gen.price_impact_simulation(sample_ingredient.id, Decimal("50"))

        test_db.refresh(sample_ingredient)
        assert Decimal(str(sample_ingredient.purchase_price)) == original_price

    def test_sorted_by_impact_descending(
        self,
        test_db: Session,
        sample_ingredient: Ingredient,
    ):
        """affected_products is sorted by cost_increase_pct descending."""
        products = []
        for qty in (Decimal("100"), Decimal("300"), Decimal("200")):
            p = Product(name=f"Test Sort {qty}", is_sub_recipe=False)
            test_db.add(p)
            test_db.commit()
            s = ProductSize(product_id=p.id, size_name="M",
                            scale_factor=Decimal("1"), is_default=True)
            test_db.add(s)
            test_db.commit()
            test_db.add(RecipeIngredient(
                product_id=p.id, ingredient_id=sample_ingredient.id,
                quantity=qty, scales_with_size=False,
                process_yield_loss=Decimal("0"),
            ))
            test_db.commit()
            products.append(p)

        gen = ReportGenerator(test_db)
        result = gen.price_impact_simulation(sample_ingredient.id, Decimal("10"))

        pcts = [float(r["cost_increase_pct"]) for r in result["affected_products"]]
        assert pcts == sorted(pcts, reverse=True)

    def test_ingredient_not_in_recipe_returns_empty_affected(self, test_db: Session):
        """An ingredient with no recipe lines returns affected_products=[]."""
        lonely_ing = Ingredient(
            name="Test Lonely Ingredient",
            purchase_price=Decimal("5000"),
            usage_unit="g",
            conversion_factor=Decimal("1000"),
            yield_percentage=Decimal("1.00"),
        )
        test_db.add(lonely_ing)
        test_db.commit()

        gen = ReportGenerator(test_db)
        result = gen.price_impact_simulation(lonely_ing.id, Decimal("10"))

        assert "error" not in result
        assert result["affected_products"] == []
