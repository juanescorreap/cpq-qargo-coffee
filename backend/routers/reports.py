"""Analysis report endpoints: costs, margins, benchmark and simulations.

All export endpoints return ``StreamingResponse`` with
``Content-Disposition: attachment`` so that the browser downloads the file
directly without needing an intermediate HTML endpoint.
"""

import csv
from decimal import Decimal
from io import StringIO
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services.report_generator import ReportGenerator

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/product-costs")
def product_costs_report(
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Cost report per product with breakdown by component.

    Query params:
    - **store_id**: Filter by store to use local ingredient prices.
      Omit to use global base prices.
    """
    generator = ReportGenerator(db)
    return generator.product_costs_report(store_id)


@router.get("/margin-analysis")
def margin_analysis_report(db: Session = Depends(get_db)):
    """Margin analysis across all current pricings.

    Classifies products into four categories:
    - **negative_margin**: margin < 0 %
    - **low_margin**: 0 % <= margin < 30 %
    - **healthy_margin**: 30 % <= margin <= 80 %
    - **high_margin**: margin > 80 %
    """
    generator = ReportGenerator(db)
    return generator.margin_analysis_report()


@router.get("/competitor-benchmark")
def competitor_benchmark_report(db: Session = Depends(get_db)):
    """Price benchmark of our products versus competitors.

    Only includes products with a match established in ``ProductCompetitorMatch``
    and a current global price in ``ProductPricing``.
    Ordered by percentage difference descending.
    """
    generator = ReportGenerator(db)
    return generator.competitor_benchmark_report()


@router.get("/price-impact")
def price_impact_simulation(
    ingredient_id: int = Query(..., gt=0, description="PK of the ingredient to simulate"),
    percent_change: Decimal = Query(..., ge=-100, le=10000, description="% variation (e.g. 10 = +10%, -5 = -5%)"),
    db: Session = Depends(get_db),
):
    """Simulation of cost impact from a price change on an ingredient.

    Query params:
    - **ingredient_id**: PK of the ingredient to simulate.
    - **percent_change**: % variation (e.g.: `10` = +10 %, `-5` = −5 %).

    Does not write any data to the DB.
    """
    generator = ReportGenerator(db)
    return generator.price_impact_simulation(ingredient_id, percent_change)


@router.get("/export/product-costs-csv")
def export_product_costs_csv(
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Exports the product cost report to CSV.

    Returns a ``product_costs.csv`` file with one row per product × size
    combination, including the ingredient, packaging and labor breakdown.

    Query params:
    - **store_id**: Same as in ``/product-costs``.
    """
    generator = ReportGenerator(db)
    report = generator.product_costs_report(store_id)

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Product", "Category", "Size",
        "Total Cost", "Ingredient Cost", "Sub-recipe Cost",
        "Packaging Cost", "Labor Cost",
    ])

    for product in report:
        for size in product["sizes"]:
            breakdown = size["cost_breakdown"]
            writer.writerow([
                product["product_name"],
                product["category"] or "",
                size["size_name"],
                size["cost"],
                breakdown.get("ingredients", 0),
                breakdown.get("sub_recipes", 0),
                breakdown.get("packaging", 0),
                breakdown.get("labor", 0),
            ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=product_costs.csv"},
    )


@router.get("/export/margin-analysis-csv")
def export_margin_analysis_csv(db: Session = Depends(get_db)):
    """Exports the margin analysis to CSV.

    Returns a ``margin_analysis.csv`` file with all margin categories in a
    single sheet. The ``Margin Category`` column indicates which of the four
    groups each row falls into.
    """
    generator = ReportGenerator(db)
    report = generator.margin_analysis_report()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Margin Category", "Product", "Size",
        "Cost", "Price", "Margin %",
    ])

    category_labels = {
        "negative_margin": "Negative",
        "low_margin":      "Low (< 30%)",
        "healthy_margin":  "Healthy (30–80%)",
        "high_margin":     "High (> 80%)",
    }

    for key, label in category_labels.items():
        for item in report.get(key, []):
            writer.writerow([
                label,
                item["product_name"],
                item["size_name"],
                item["cost"],
                item["price"],
                round(item["margin_pct"], 2),
            ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=margin_analysis.csv"},
    )
