"""Fix conversion_factor unit inconsistencies for volumetric/bulk ingredients.

Root cause: conversion_factor was stored in L (or fl.oz) while
usage_unit_quantity was stored in mL — a 1000× (or 29.6×) error
in the cost formula (unit_price / conversion_factor) × (qty × uuq).

Fix: normalize conversion_factor to mL for all affected ingredients so
both sides of the formula use the same physical unit.

Groups:
  1000×  — cf was in L, uuq in mL  → multiply cf by 1000
  ~29.6× — cf was in fl.oz, uuq in mL → multiply cf by 29.5735

Fix 2 (inferred): additional ingredients where cf was in lb or weight-oz
while uuq was in g. Corrected by best physical reading; flag for invoice
confirmation.

Revision ID: 0030_fix_ingredient_conversion_factors
Revises: 0029_fix_sra_exclude_constraint
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_fix_cf_units"
down_revision = "0029_fix_sra_exclude_constraint"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Fix 1 — mathematically confirmed (before/after in comments)
# ---------------------------------------------------------------------------
# 1000× group: cf was in L, now in mL. Physical values used where exact is
# known; otherwise ×1000 of the stored approximation.
_FIX1_1000X = [
    # (ingredient_id, old_cf, new_cf, note)
    (1,  "3.780000",  "3785",   "Milk 1 gal: L→mL, matches Ice Cubes convention"),
    (52, "3.780000",  "3785",   "Water 1 gal: L→mL"),
    (13, "5.678120",  "5678",   "Brewed Coffee 1.5 gal: L→mL (1.5 × 3785.41 = 5678)"),
    (2,  "11.356200", "11356",  "Almond Milk Case 12×32 oz: L→mL (384 fl.oz × 29.57)"),
    (3,  "11.356200", "11356",  "Coconut Milk Case 12×32 oz: L→mL"),
    (37, "5.000000",  "5000",   "Balsamic Vinegar 5 L: exact → 5000 mL"),
    (24, "1.890000",  "1893",   "White Lotus 0.5 gal: L→mL (0.5 × 3785.41 = 1893)"),
    (25, "1.890000",  "1893",   "Strawberry Fruit Fusion 0.5 gal"),
    (26, "1.890000",  "1893",   "Blue Raspberry Fruit Fusion 0.5 gal"),
    (27, "1.890000",  "1893",   "Mango Passion Fruit Fusion 0.5 gal"),
    (28, "1.890000",  "1893",   "Pina Colada Fruit Fusion 0.5 gal"),
    (29, "1.890000",  "1893",   "Watermelon Fruit Fusion 0.5 gal"),
    (30, "1.890000",  "1893",   "Orange Creamsicle Fruit Fusion 0.5 gal"),
    (31, "1.890000",  "1893",   "Cream Base 0.5 gal"),
]

# ~29.6× group: cf was in fl.oz, now in mL (cf × 29.5735)
_FIX1_29X = [
    (33, "64.000000",   "1893",  "Chocolate Sauce Bottle 64 fl.oz"),
    (34, "608.000000",  "17981", "Pesto Canister 38 lb (608 oz-equiv × 29.57)"),
    (35, "420.000000",  "12421", "Mustard Case 420 fl.oz"),
    (36, "2048.000000", "60567", "Mayonnaise Case 4×4 gal = 2048 fl.oz"),
]

# ---------------------------------------------------------------------------
# Fix 2 — inferred (no invoice confirmation; marked in notes below)
# ---------------------------------------------------------------------------
# lb-group: cf was in lb, uuq in g → convert to g (1 lb = 453.592 g)
# oz-group: cf was in weight-oz, uuq in g → convert to g (1 oz = 28.3495 g)
# unit-group: cf=1, uuq=17 g/slice → set cf=17 to match uuq
_FIX2 = [
    (5,  "1.000000",  "17",      "Cheese Cheddar Sliced: purchase=unit=slice, cf→17 matches uuq=17"),
    (6,  "1.000000",  "17",      "Cheese Mozzarella Sliced: same"),
    (41, "3.000000",  "1361",    "Chocolate Powder 3 lb: lb→g (3×453.6=1361)"),
    (40, "3.000000",  "1361",    "Vanilla Powder 3 lb: lb→g"),
    (47, "48.000000", "1361",    "Honey Oat Granola Case 4×12 oz: oz→g (48×28.35=1361)"),
    (48, "5.000000",  "2268",    "Chia Seeds Bag 5 lb: lb→g (5×453.6=2268)"),
]


def upgrade() -> None:
    conn = op.get_bind()

    for ing_id, _old, new_cf, _note in _FIX1_1000X + _FIX1_29X + _FIX2:
        conn.execute(
            sa.text(
                "UPDATE public.ingredients "
                "SET conversion_factor = :cf "
                "WHERE id = :id"
            ),
            {"cf": new_cf, "id": ing_id},
        )


def downgrade() -> None:
    conn = op.get_bind()

    all_fixes = _FIX1_1000X + _FIX1_29X + _FIX2
    for ing_id, old_cf, _new, _note in all_fixes:
        conn.execute(
            sa.text(
                "UPDATE public.ingredients "
                "SET conversion_factor = :cf "
                "WHERE id = :id"
            ),
            {"cf": old_cf, "id": ing_id},
        )
