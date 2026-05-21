"""Initial seed data for the CPQ system.

Run with:
    python -m backend.migrations.seed_data
"""

import backend.models  # noqa: F401 — registers all models in Base.metadata
from backend.database import SessionLocal
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
# Category margins
# ---------------------------------------------------------------------------

_CATEGORY_MARGINS = [
    {"category": "bebidas_calientes", "markup_percentage": 65.0},
    {"category": "bebidas_frias",     "markup_percentage": 70.0},
    {"category": "alimentos",         "markup_percentage": 60.0},
    {"category": "otros",             "markup_percentage": 50.0},
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
    {"code": "BOG-ZONA-T",  "name": "Bogotá Zona T",              "city": "Bogotá"},
    {"code": "BOG-USAQUEN", "name": "Bogotá Usaquén",             "city": "Bogotá"},
    {"code": "BOG-CENTR",   "name": "Bogotá Centro Andino",       "city": "Bogotá"},
    {"code": "BOG-GRAN-E",  "name": "Bogotá Gran Estación",       "city": "Bogotá"},
    {"code": "BOG-EL-RET",  "name": "Bogotá El Retiro",           "city": "Bogotá"},
    {"code": "MED-EL-POB",  "name": "Medellín El Poblado",        "city": "Medellín"},
    {"code": "MED-LAUREL",  "name": "Medellín Laureles",          "city": "Medellín"},
    {"code": "MED-CENTRO",  "name": "Medellín Centro",            "city": "Medellín"},
    {"code": "CAL-CHIPIC",  "name": "Cali Chipichape",            "city": "Cali"},
    {"code": "CAL-JARD",    "name": "Cali Jardín Plaza",          "city": "Cali"},
    {"code": "BAQ-BUANA",   "name": "Barranquilla Buenavista",    "city": "Barranquilla"},
    {"code": "BAQ-MET",     "name": "Barranquilla Metrocentro",   "city": "Barranquilla"},
    {"code": "CTG-BOCAG",   "name": "Cartagena Bocagrande",       "city": "Cartagena"},
    {"code": "CTG-CABRE",   "name": "Cartagena Cabrero",          "city": "Cartagena"},
    {"code": "BUC-CABEC",   "name": "Bucaramanga Cabecera",       "city": "Bucaramanga"},
    {"code": "PEI-CIRCUN",  "name": "Pereira Circunvalar",        "city": "Pereira"},
    {"code": "MAN-CABLE",   "name": "Manizales Cable Plaza",      "city": "Manizales"},
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
        seed_category_margins(db)
        seed_stores(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
