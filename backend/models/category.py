from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.sql import func

from backend.database import Base


class Category(Base):
    """Canonical product category. slug is the shared key used by Product and CategoryMargin.

    Example:
        slug="bebidas_calientes", display_name="Hot Drinks"
        slug="bebidas_frias",     display_name="Cold Drinks"
    """

    __tablename__ = "categories"

    slug: str = Column(String(100), primary_key=True)
    display_name: str | None = Column(String(200))
    notes: str | None = Column(Text)
    created_at: object = Column(DateTime(timezone=True), server_default=func.now())
