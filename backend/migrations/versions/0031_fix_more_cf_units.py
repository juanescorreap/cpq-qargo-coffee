"""Fix additional conversion_factor unit mismatches missed in migration 0030.

Same root cause: cf stored in lb/oz while usage_unit_quantity in grams,
or cf=1 "unit" while uuq expects a gram-based weight.

Groups:
  CONFIRMED — purchase unit clearly states lb or oz → convert to grams
  INFERRED  — purchase_unit='unit'; gram weight inferred from uuq and ingredient type;
              flag for invoice confirmation.

Revision ID: 0031_fix_more_cf
Revises: 0030_fix_cf_units
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_fix_more_cf"
down_revision = "0030_fix_cf_units"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# CONFIRMED — lb/oz clearly stated in purchase_unit
# Same pattern as FIX1 in 0030, just missed in the original scan.
# ---------------------------------------------------------------------------
_CONFIRMED = [
    # (ingredient_id, old_cf, new_cf, note)
    (54, "4.000000",   "1814",  "Arugula Bag 4 lb: lb→g (4×453.592=1814)"),
    (64, "1.000000",   "454",   "Smoked Salmon lb: lb→g (1×453.592=454)"),
    (65, "1.000000",   "454",   "Chicken Breast Grilled lb: lb→g"),
    (45, "48.000000",  "1361",  "Tapioca Pearls Case 3 lb=48 oz: oz→g (48×28.35=1361)"),
    (8,  "32.000000",  "907",   "Greek Yogurt Container 32 oz: oz→g (32×28.35=907)"),
    (43, "10.000000",  "4536",  "Icing Sugar Box 10 lb: lb→g (10×453.592=4536)"),
]

# ---------------------------------------------------------------------------
# INFERRED — purchase_unit='unit'; gram weight inferred, not invoice-confirmed
# Reasoning per ingredient:
#   Cream Cheese (4):  '100 units' = 100 individual 1-oz portions → 100×28.35=2835≈2800g
#   Turkey Bacon (62): uuq=20g/slice, cf=1 "unit"=1 slice → cf must be 20g to balance formula
#   Proscuitto (66):   same pattern as Turkey Bacon; uuq=20g/slice, cf→20
#   Vanilla Gelato (9):  purchased per oz-serving; uuq=28.35g/oz → cf=28.35 (1 oz serving)
#   Chocolate Gelato (10): same as Vanilla Gelato
#   Avocado (57):      1 avocado ≈ 150g usable flesh; uuq=14g/tbsp → cf=150
# ---------------------------------------------------------------------------
_INFERRED = [
    (4,  "100.000000", "2800",  "Cream Cheese 100 units: 100×1-oz portions→2800g; confirm with invoice"),
    (62, "1.000000",   "20",    "Turkey Bacon unit=1 slice: uuq=20g→cf=20; confirm portion weight"),
    (66, "1.000000",   "20",    "Proscuitto unit=1 slice: uuq=20g→cf=20; confirm portion weight"),
    (9,  "1.000000",   "28.35", "Vanilla Gelato unit=1-oz serving: cf→28.35g; confirm purchase unit"),
    (10, "1.000000",   "28.35", "Chocolate Gelato unit=1-oz serving: cf→28.35g; confirm purchase unit"),
    (57, "1.000000",   "150",   "Avocado unit=1 avocado≈150g usable: cf→150; confirm avg yield"),
]


def upgrade() -> None:
    conn = op.get_bind()

    for ing_id, _old, new_cf, _note in _CONFIRMED + _INFERRED:
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

    for ing_id, old_cf, _new, _note in _CONFIRMED + _INFERRED:
        conn.execute(
            sa.text(
                "UPDATE public.ingredients "
                "SET conversion_factor = :cf "
                "WHERE id = :id"
            ),
            {"cf": old_cf, "id": ing_id},
        )
