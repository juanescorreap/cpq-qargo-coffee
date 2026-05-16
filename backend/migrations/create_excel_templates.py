"""
Generate Excel templates for data migration into data/raw/.
Run from the project root: python backend/migrations/create_excel_templates.py
"""

from pathlib import Path

import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


def create_ingredients_template(output_dir: Path) -> Path:
    data = [
        {
            "name": "Whole milk Alpina",
            "category": "Dairy",
            "purchase_unit": "Box 1L",
            "purchase_price": 4500,
            "usage_unit": "ml",
            "conversion_factor": 1000,
            "yield_%": 95,
            "supplier_url": "https://",
        },
        {
            "name": "Espresso coffee",
            "category": "Coffee",
            "purchase_unit": "Bag 500g",
            "purchase_price": 25000,
            "usage_unit": "g",
            "conversion_factor": 500,
            "yield_%": 98,
            "supplier_url": "https://",
        },
        {
            "name": "Monin vanilla syrup",
            "category": "Syrups",
            "purchase_unit": "Bottle 750ml",
            "purchase_price": 28000,
            "usage_unit": "ml",
            "conversion_factor": 750,
            "yield_%": 98,
            "supplier_url": "https://",
        },
        {
            "name": "12oz paper cup",
            "category": "Packaging",
            "purchase_unit": "Pack 50 units",
            "purchase_price": 15000,
            "usage_unit": "unit",
            "conversion_factor": 50,
            "yield_%": 100,
            "supplier_url": "https://",
        },
        {
            "name": "White sugar",
            "category": "Sweeteners",
            "purchase_unit": "Bag 1kg",
            "purchase_price": 3500,
            "usage_unit": "g",
            "conversion_factor": 1000,
            "yield_%": 100,
            "supplier_url": "https://",
        },
    ]
    path = output_dir / "ingredients.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def create_conversions_template(output_dir: Path) -> Path:
    data = [
        {
            "ingredient_name": "Monin vanilla syrup",
            "recipe_unit": "pump",
            "equivalent_ml_or_g": 30,
            "notes": "Standard Monin pump",
        },
        {
            "ingredient_name": "Espresso coffee",
            "recipe_unit": "shot",
            "equivalent_ml_or_g": 30,
            "notes": "Standard shot",
        },
        {
            "ingredient_name": "White sugar",
            "recipe_unit": "teaspoon",
            "equivalent_ml_or_g": 5,
            "notes": "Level teaspoon",
        },
    ]
    path = output_dir / "conversions.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def create_products_template(output_dir: Path) -> Path:
    data = [
        {
            "name": "Cappuccino",
            "category": "hot_beverages",
            "base_size_oz": 12,
            "prep_time_min": 3,
            "labor_cost_per_min": 200,
            "is_sub_recipe": False,
        },
        {
            "name": "Latte",
            "category": "hot_beverages",
            "base_size_oz": 12,
            "prep_time_min": 3,
            "labor_cost_per_min": 200,
            "is_sub_recipe": False,
        },
        {
            "name": "Homemade vanilla syrup",
            "category": "other",
            "base_size_oz": 0,
            "prep_time_min": 0,
            "labor_cost_per_min": 0,
            "is_sub_recipe": True,
        },
    ]
    path = output_dir / "products.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def create_sizes_template(output_dir: Path) -> Path:
    data = [
        {
            "product_name": "Cappuccino",
            "size": "small",
            "volume_oz": 8,
            "scale_factor": 0.67,
            "is_default": False,
        },
        {
            "product_name": "Cappuccino",
            "size": "medium",
            "volume_oz": 12,
            "scale_factor": 1.0,
            "is_default": True,
        },
        {
            "product_name": "Cappuccino",
            "size": "large",
            "volume_oz": 16,
            "scale_factor": 1.33,
            "is_default": False,
        },
    ]
    path = output_dir / "sizes.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def create_recipes_template(output_dir: Path) -> Path:
    data = [
        {
            "product_name": "Cappuccino",
            "ingredient_name": "Espresso coffee",
            "quantity": "2 shots",
            "scales_with_size": False,
            "process_yield_%": 0,
        },
        {
            "product_name": "Cappuccino",
            "ingredient_name": "Whole milk Alpina",
            "quantity": "240 ml",
            "scales_with_size": True,
            "process_yield_%": 5,
        },
        {
            "product_name": "Cappuccino",
            "ingredient_name": "12oz paper cup",
            "quantity": "1",
            "scales_with_size": False,
            "process_yield_%": 0,
        },
    ]
    path = output_dir / "recipes.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    creators = [
        ("ingredients.xlsx", create_ingredients_template),
        ("conversions.xlsx", create_conversions_template),
        ("products.xlsx", create_products_template),
        ("sizes.xlsx", create_sizes_template),
        ("recipes.xlsx", create_recipes_template),
    ]

    print("Generating Excel migration templates...\n")
    for filename, creator in creators:
        path = creator(OUTPUT_DIR)
        print(f"  Created: {path}")

    print(f"\nAll templates written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
