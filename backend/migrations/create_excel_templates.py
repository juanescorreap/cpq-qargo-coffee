"""
Generate clean Excel templates for the core data migration into data/raw/.

Corrected column structure (aligned to the schema + migrate_from_excel loader):
  * ingredients: adds `canonical_unit`.
  * products:    `category` renamed to `category_slug` (value MUST be an existing
                 categories.slug, e.g. "bebidas-calientes").
  * recipes:     `quantity` is now a plain NUMBER and the unit lives in its own
                 `recipe_unit` column — no more fragile "2 shots" string parsing.

Each sheet ships ONE internally-consistent example row so the demo loads
end-to-end. Replace the example rows with real data before loading.

Run from the project root:
    python backend/migrations/create_excel_templates.py
"""

from pathlib import Path

import pandas as pd

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


def create_ingredients_template(output_dir: Path) -> Path:
    data = [
        {
            "name": "Whole milk",
            "category": "dairy",
            "purchase_unit": "Box 1L",
            "purchase_price": 4200,        # REQUIRED: without a price the cost is 0
            "usage_unit": "ml",
            "conversion_factor": 1000,     # usage_units per purchase_unit (1 L = 1000 ml)
            "yield_%": 0.98,               # fraction 0–1 (0.98 = 98 %)
            "canonical_unit": "ml",
            "supplier_url": "",
        },
        {
            "name": "Espresso",
            "category": "coffee",
            "purchase_unit": "Bag 1kg",
            "purchase_price": 52000,
            "usage_unit": "g",
            "conversion_factor": 1000,
            "yield_%": 1.0,
            "canonical_unit": "g",
            "supplier_url": "",
        },
        {
            "name": "12oz paper cup",
            "category": "packaging",
            "purchase_unit": "Pack 50 units",
            "purchase_price": 15000,
            "usage_unit": "unit",
            "conversion_factor": 50,
            "yield_%": 1.0,
            "canonical_unit": "unit",
            "supplier_url": "",
        },
    ]
    path = output_dir / "ingredients.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def create_conversions_template(output_dir: Path) -> Path:
    data = [
        {
            "ingredient_name": "Espresso",
            "recipe_unit": "shot",
            "equivalent_ml_or_g": 30,      # usage_unit per 1 recipe_unit (1 shot = 30 g)
            "notes": "Standard extraction",
        },
    ]
    path = output_dir / "conversions.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def create_products_template(output_dir: Path) -> Path:
    data = [
        {
            "name": "Caffe Latte",
            "category_slug": "bebidas-calientes",  # MUST exist in categories.slug
            "base_size_oz": 12,
            "prep_time_min": 2.5,
            "labor_cost_per_min": 120,
            "is_sub_recipe": False,
        },
    ]
    path = output_dir / "products.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def create_sizes_template(output_dir: Path) -> Path:
    data = [
        {
            "product_name": "Caffe Latte",
            "size": "Medium",
            "volume_oz": 12,
            "scale_factor": 1.0,           # base size = 1.0
            "is_default": True,            # exactly one default per product
        },
    ]
    path = output_dir / "sizes.xlsx"
    pd.DataFrame(data).to_excel(path, index=False)
    return path


def create_recipes_template(output_dir: Path) -> Path:
    data = [
        {
            "product_name": "Caffe Latte",
            "ingredient_name": "Whole milk",
            "quantity": 240,               # NUMBER only
            "recipe_unit": "",             # empty => quantity is in the ingredient usage_unit (ml)
            "scales_with_size": True,
            "process_yield_%": 0,
        },
        {
            "product_name": "Caffe Latte",
            "ingredient_name": "Espresso",
            "quantity": 2,
            "recipe_unit": "shot",         # needs a conversion row in conversions.xlsx
            "scales_with_size": False,
            "process_yield_%": 0,
        },
        {
            "product_name": "Caffe Latte",
            "ingredient_name": "12oz paper cup",
            "quantity": 1,
            "recipe_unit": "",
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
