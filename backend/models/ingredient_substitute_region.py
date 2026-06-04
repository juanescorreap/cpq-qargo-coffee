from sqlalchemy import BigInteger, Column, ForeignKey

from backend.database import Base


class IngredientSubstituteRegion(Base):
    """Junction: regions where an ingredient substitute applies.

    Replaces the former ``ingredient_substitutes.affects_regions ARRAY`` column,
    giving real referential integrity. An empty set (no rows) means the
    substitute applies globally.
    """

    __tablename__ = "ingredient_substitute_regions"

    substitute_id: int = Column(
        BigInteger,
        ForeignKey("ingredient_substitutes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    region_id: int = Column(
        BigInteger,
        ForeignKey("regions.id", ondelete="CASCADE"),
        primary_key=True,
    )
