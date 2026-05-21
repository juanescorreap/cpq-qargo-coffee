"""Endpoints de reportes de análisis: costos, márgenes, benchmark y simulaciones.

Todos los endpoints de exportación devuelven ``StreamingResponse`` con
``Content-Disposition: attachment`` para que el navegador descargue el archivo
directamente sin necesidad de un endpoint HTML intermedio.
"""

import csv
from decimal import Decimal
from io import StringIO
from typing import Optional

from fastapi import APIRouter, Depends
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
    """Reporte de costos por producto con desglose por componente.

    Query params:
    - **store_id**: Filtrar por tienda para usar precios locales de ingredientes.
      Omitir para usar precios base globales.
    """
    generator = ReportGenerator(db)
    return generator.product_costs_report(store_id)


@router.get("/margin-analysis")
def margin_analysis_report(db: Session = Depends(get_db)):
    """Análisis de márgenes sobre todos los pricings vigentes.

    Clasifica los productos en cuatro categorías:
    - **negative_margin**: margen < 0 %
    - **low_margin**: 0 % ≤ margen < 30 %
    - **healthy_margin**: 30 % ≤ margen ≤ 80 %
    - **high_margin**: margen > 80 %
    """
    generator = ReportGenerator(db)
    return generator.margin_analysis_report()


@router.get("/competitor-benchmark")
def competitor_benchmark_report(db: Session = Depends(get_db)):
    """Benchmark de precios propios versus competencia.

    Solo incluye productos con match establecido en ``ProductCompetitorMatch``
    y precio global vigente en ``ProductPricing``.
    Ordenado por diferencia porcentual descendente.
    """
    generator = ReportGenerator(db)
    return generator.competitor_benchmark_report()


@router.get("/price-impact")
def price_impact_simulation(
    ingredient_id: int,
    percent_change: Decimal,
    db: Session = Depends(get_db),
):
    """Simulación del impacto en costos de un cambio de precio en un ingrediente.

    Query params:
    - **ingredient_id**: PK del ingrediente a simular.
    - **percent_change**: % de variación (ej: `10` = +10 %, `-5` = −5 %).

    No escribe ningún dato en la BD.
    """
    generator = ReportGenerator(db)
    return generator.price_impact_simulation(ingredient_id, percent_change)


@router.get("/export/product-costs-csv")
def export_product_costs_csv(
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Exporta el reporte de costos por producto a CSV.

    Devuelve un archivo ``product_costs.csv`` con una fila por combinación
    producto × tamaño, incluyendo el desglose de ingredientes, packaging y
    mano de obra.

    Query params:
    - **store_id**: Igual que en ``/product-costs``.
    """
    generator = ReportGenerator(db)
    report = generator.product_costs_report(store_id)

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Producto", "Categoría", "Tamaño",
        "Costo Total", "Costo Ingredientes", "Costo Sub-recetas",
        "Costo Packaging", "Costo Labor",
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
    """Exporta el análisis de márgenes a CSV.

    Devuelve un archivo ``margin_analysis.csv`` con todas las categorías de
    margen en una sola hoja. La columna ``Categoría Margen`` indica en cuál
    de los cuatro grupos cae cada fila.
    """
    generator = ReportGenerator(db)
    report = generator.margin_analysis_report()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Categoría Margen", "Producto", "Tamaño",
        "Costo", "Precio", "Margen %",
    ])

    category_labels = {
        "negative_margin": "Negativo",
        "low_margin":      "Bajo (< 30%)",
        "healthy_margin":  "Sano (30–80%)",
        "high_margin":     "Alto (> 80%)",
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
