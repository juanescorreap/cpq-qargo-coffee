"""Initial seed data for the CPQ system.

Run with:
    python -m backend.migrations.seed_data
"""

import backend.models  # noqa: F401 — registers all models in Base.metadata
from backend.database import SessionLocal
from backend.models.category import Category
from backend.models.competitor import Competitor
from backend.models.pricing import CategoryMargin
from backend.models.recipe_unit import RecipeUnit
from backend.models.store import Store

# ---------------------------------------------------------------------------
# Recipe units
# ---------------------------------------------------------------------------

_RECIPE_UNITS = [
    # Precise volume
    {"name": "ml",         "category": "volume",  "description": "Millilitre"},
    {"name": "oz",         "category": "volume",  "description": "Fluid ounce (29.57 ml)"},
    {"name": "pump",       "category": "volume",  "description": "Syrup pump dose (~10 ml, varies by syrup)"},
    {"name": "shot",       "category": "volume",  "description": "Espresso extraction (~30 ml)"},
    {"name": "scoop",      "category": "volume",  "description": "Standard powder or ice measure"},
    # Kitchen volume
    {"name": "teaspoon",   "category": "volume",  "description": "Teaspoon (≈5 ml)"},
    {"name": "tablespoon", "category": "volume",  "description": "Tablespoon (≈15 ml)"},
    {"name": "cup",        "category": "volume",  "description": "Kitchen cup (≈240 ml)"},
    # Informal volume
    {"name": "splash",     "category": "volume",  "description": "Small splash (~15 ml)"},
    {"name": "dash",       "category": "volume",  "description": "Very small touch (~1 ml)"},
    {"name": "drizzle",    "category": "volume",  "description": "Decorative drizzle over drink"},
    # Weight
    {"name": "g",          "category": "weight",  "description": "Gram"},
    {"name": "oz_weight",  "category": "weight",  "description": "Weight ounce (28.35 g)"},
    {"name": "pinch",      "category": "weight",  "description": "Pinch (~0.3 g)"},
    {"name": "sprinkle",   "category": "weight",  "description": "Decorative sprinkle"},
    # Count
    {"name": "unit",     "category": "count",   "description": "Discrete unit (e.g.: 1 package)"},
    {"name": "lines",    "category": "count",   "description": "Decoration lines (e.g.: latte art)"},
    {"name": "each",     "category": "count",   "description": "Individual piece (e.g.: 1 piece of fruit)"},
    {"name": "slice",    "category": "count",   "description": "Slice (e.g.: 2 slices of bacon)"},
    {"name": "handful",  "category": "count",   "description": "Handful of ingredient (e.g.: arugula)"},
    {"name": "leaf",     "category": "count",   "description": "Individual leaf (e.g.: basil, mint)"},
    {"name": "bag",      "category": "count",   "description": "Bag or sachet (e.g.: tea bag)"},
    {"name": "drop",     "category": "volume",  "description": "Drop (e.g.: food colouring)"},
    {"name": "rosette",  "category": "count",   "description": "Decorative whipped cream rosette"},
    {"name": "serving",  "category": "count",   "description": "Standard portion of a product"},
]


def seed_recipe_units(db) -> None:
    created = 0
    for data in _RECIPE_UNITS:
        exists = db.query(RecipeUnit).filter(RecipeUnit.name == data["name"]).first()
        if not exists:
            db.add(RecipeUnit(**data))
            created += 1
    db.commit()
    print(f"✅ Recipe units created ({created} new, {len(_RECIPE_UNITS) - created} already existed)")


# ---------------------------------------------------------------------------
# Categories (canonical taxonomy)
# ---------------------------------------------------------------------------
# products.category and category_margins.category are BOTH FKs to
# categories.slug, so these rows MUST exist before either is loaded. The
# canonical convention is underscore slugs (see migration 0004 + CLAUDE.md);
# the Excel loader normalises hyphen/space variants to this form.

_CATEGORIES = [
    {"slug": "hot_classics",     "display_name": "Hot Classics"},
    {"slug": "iced_classics",    "display_name": "Iced Classics"},
    {"slug": "cold_brew",        "display_name": "Cold Brew"},
    {"slug": "boba_tea",         "display_name": "Boba Tea"},
    {"slug": "tea",              "display_name": "Tea"},
    {"slug": "energy_fresh",     "display_name": "Energy Fresh"},
    {"slug": "energy_smoothies", "display_name": "Energy Smoothies"},
    {"slug": "fresh_and_cool",   "display_name": "Fresh and Cool"},
    {"slug": "gelato",           "display_name": "Gelato"},
    {"slug": "bakery",           "display_name": "Bakery"},
    {"slug": "sweet_treats",     "display_name": "Sweet Treats"},
    {"slug": "taste_of_italy",   "display_name": "Taste of Italy"},
    {"slug": "breakfast",        "display_name": "Breakfast"},
    {"slug": "lunch",            "display_name": "Lunch"},
    {"slug": "bottled_drinks",   "display_name": "Bottled Drinks"},
    {"slug": "sub_recipe",       "display_name": "Sub-recipe"},
    {"slug": "beverages",        "display_name": "Beverages"},
]


def seed_categories(db) -> None:
    created = 0
    for data in _CATEGORIES:
        exists = db.query(Category).filter(Category.slug == data["slug"]).first()
        if not exists:
            db.add(Category(**data))
            created += 1
    db.commit()
    print(f"✅ Categories created ({created} new, {len(_CATEGORIES) - created} already existed)")


# ---------------------------------------------------------------------------
# Category margins
# ---------------------------------------------------------------------------

_CATEGORY_MARGINS = [
    # Beverages — benchmark: US specialty café COGS 25-33% → markup 200-300%
    {"category": "hot_classics",     "markup_percentage": 250.0},  # lattes/caps: ~$2 cost → ~$7 retail
    {"category": "iced_classics",    "markup_percentage": 220.0},  # iced drinks: ~$3 cost → ~$9.6 retail
    {"category": "cold_brew",        "markup_percentage": 200.0},  # ~$2 cost → $6 retail
    {"category": "boba_tea",         "markup_percentage": 200.0},  # ~$3 cost → $9 retail
    {"category": "tea",              "markup_percentage": 210.0},  # $1.53 cost → $4.74 (from data)
    {"category": "energy_fresh",     "markup_percentage": 150.0},  # COGS ~40%
    {"category": "energy_smoothies", "markup_percentage": 150.0},  # COGS ~40%
    {"category": "fresh_and_cool",   "markup_percentage": 200.0},  # similar to cold bevs
    {"category": "gelato",           "markup_percentage": 300.0},  # very low COGS, premium pricing
    {"category": "beverages",        "markup_percentage": 150.0},  # generic fallback
    # Food — from actual cost data + retail benchmarks
    {"category": "bakery",           "markup_percentage": 200.0},  # $1.60 cost → $4.80 (from data)
    {"category": "sweet_treats",     "markup_percentage": 170.0},  # $2.12 cost → $5.72 (from data)
    {"category": "taste_of_italy",   "markup_percentage": 80.0},   # $3.59 cost → $6.46 (from data)
    {"category": "breakfast",        "markup_percentage": 150.0},  # COGS ~40%
    {"category": "lunch",            "markup_percentage": 120.0},  # COGS ~45%
    # Retail / pass-through
    {"category": "bottled_drinks",   "markup_percentage": 120.0},  # $2.05 cost → $4.51 (from data)
    # Internal
    {"category": "sub_recipe",       "markup_percentage": 0.0},    # not sold directly
]


def seed_category_margins(db) -> None:
    created = 0
    for data in _CATEGORY_MARGINS:
        exists = (
            db.query(CategoryMargin)
            .filter(CategoryMargin.category == data["category"])
            .first()
        )
        if not exists:
            db.add(CategoryMargin(**data))
            created += 1
    db.commit()
    print(f"✅ Category margins created ({created} new, {len(_CATEGORY_MARGINS) - created} already existed)")


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

_STORES = [
    {"code": "1-FV-CA",  "name": "Fountain Valley",   "city": "Fountain Valley",  "default_currency_code": "USD"},
    {"code": "2-LB-CA",  "name": "Long Beach",        "city": "Long Beach",       "default_currency_code": "USD"},
    {"code": "3-TM-FL",  "name": "Tampa",             "city": "Tampa",            "default_currency_code": "USD"},
    {"code": "4-WC-DC",  "name": "Washington",        "city": "Washington",       "default_currency_code": "USD"},
    {"code": "5-BK-CA",  "name": "Berkeley",          "city": "Berkeley",         "default_currency_code": "USD"},
    {"code": "6-DT-MI",  "name": "Detroit",           "city": "Detroit",          "default_currency_code": "USD"},
    {"code": "7-ED-TX",  "name": "Edinburg",          "city": "Edinburg",         "default_currency_code": "USD"},
    {"code": "8-WV-OH",  "name": "Westerville",       "city": "Westerville",      "default_currency_code": "USD"},
    {"code": "10-BL-IL", "name": "Bolingbrook Boughton", "city": "Bolingbrook",  "default_currency_code": "USD"},
    {"code": "11-SA-TX", "name": "San Antonio",       "city": "San Antonio",      "default_currency_code": "USD"},
    {"code": "12-DB-MI", "name": "Dearborn",          "city": "Dearborn",         "default_currency_code": "USD"},
    {"code": "13-BL-IL", "name": "Bolingbrook Weber", "city": "Bolingbrook",      "default_currency_code": "USD"},
    {"code": "14-SC-IL", "name": "Saint Charles",     "city": "Saint Charles",    "default_currency_code": "USD"},
    {"code": "15-OP-IL", "name": "Orland Park",       "city": "Orland Park",      "default_currency_code": "USD"},
    {"code": "16-GP-TX", "name": "Grand Prairie",     "city": "Grand Prairie",    "default_currency_code": "USD"},
    {"code": "17-VG-NV", "name": "Las Vegas",         "city": "Las Vegas",        "default_currency_code": "USD"},
    {"code": "18-CN-MI", "name": "Canton",            "city": "Canton",           "default_currency_code": "USD"},
]


def seed_stores(db) -> None:
    created = 0
    for data in _STORES:
        exists = db.query(Store).filter(Store.code == data["code"]).first()
        if not exists:
            db.add(Store(**data))
            created += 1
    db.commit()
    print(f"✅ {created} stores created ({len(_STORES) - created} already existed)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    db = SessionLocal()
    try:
        seed_recipe_units(db)
        seed_categories(db)        # before margins: category_margins.category FKs here
        seed_category_margins(db)
        seed_stores(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
