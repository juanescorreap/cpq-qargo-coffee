"""Seed inicial de datos de referencia para el sistema CPQ.

Ejecutar con:
    python -m backend.migrations.seed_data
"""

import backend.models  # noqa: F401 — registra todos los modelos en Base.metadata
from backend.database import SessionLocal
from backend.models.competitor import Competitor
from backend.models.pricing import CategoryMargin
from backend.models.recipe_unit import RecipeUnit
from backend.models.store import Store

# ---------------------------------------------------------------------------
# Recipe units
# ---------------------------------------------------------------------------

_RECIPE_UNITS = [
    # Volumen precisas
    {"name": "ml",         "category": "volume",  "description": "Mililitro"},
    {"name": "oz",         "category": "volume",  "description": "Onza líquida (29.57 ml)"},
    {"name": "pump",       "category": "volume",  "description": "Dosis de bomba de jarabe (~10 ml, varía por jarabe)"},
    {"name": "shot",       "category": "volume",  "description": "Extracción de espresso (~30 ml)"},
    {"name": "scoop",      "category": "volume",  "description": "Medidor estándar de polvo o hielo"},
    # Volumen cocina
    {"name": "teaspoon",   "category": "volume",  "description": "Cucharadita (≈5 ml)"},
    {"name": "tablespoon", "category": "volume",  "description": "Cucharada (≈15 ml)"},
    {"name": "cup",        "category": "volume",  "description": "Taza de cocina (≈240 ml)"},
    # Volumen informales
    {"name": "splash",     "category": "volume",  "description": "Chorrito pequeño (~15 ml)"},
    {"name": "dash",       "category": "volume",  "description": "Toque muy pequeño (~1 ml)"},
    {"name": "drizzle",    "category": "volume",  "description": "Hilo decorativo sobre bebida"},
    # Peso
    {"name": "g",          "category": "weight",  "description": "Gramo"},
    {"name": "oz_weight",  "category": "weight",  "description": "Onza de peso (28.35 g)"},
    {"name": "pinch",      "category": "weight",  "description": "Pizca (~0.3 g)"},
    {"name": "sprinkle",   "category": "weight",  "description": "Espolvoreado decorativo"},
    # Conteo
    {"name": "unit",     "category": "count",   "description": "Unidad discreta (ej: 1 empaque)"},
    {"name": "lines",    "category": "count",   "description": "Líneas de decoración (ej: arte latte)"},
    {"name": "each",     "category": "count",   "description": "Pieza individual (ej: 1 trozo de fruta)"},
    {"name": "slice",    "category": "count",   "description": "Rebanada o loncha (ej: 2 slices de tocino)"},
    {"name": "handful",  "category": "count",   "description": "Puñado de ingrediente (ej: arúgula)"},
    {"name": "leaf",     "category": "count",   "description": "Hoja individual (ej: albahaca, menta)"},
    {"name": "bag",      "category": "count",   "description": "Bolsa o sobre (ej: bolsita de té)"},
    {"name": "drop",     "category": "volume",  "description": "Gota (ej: colorante alimentario)"},
    {"name": "rosette",  "category": "count",   "description": "Roseta decorativa de crema batida"},
    {"name": "serving",  "category": "count",   "description": "Porción estándar de un producto"},
]


def seed_recipe_units(db) -> None:
    created = 0
    for data in _RECIPE_UNITS:
        exists = db.query(RecipeUnit).filter(RecipeUnit.name == data["name"]).first()
        if not exists:
            db.add(RecipeUnit(**data))
            created += 1
    db.commit()
    print(f"✅ Recipe units created ({created} nuevas, {len(_RECIPE_UNITS) - created} ya existían)")


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
    print(f"✅ Category margins created ({created} nuevas, {len(_CATEGORY_MARGINS) - created} ya existían)")


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
    print(f"✅ {created} stores created ({len(_STORES) - created} ya existían)")


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
