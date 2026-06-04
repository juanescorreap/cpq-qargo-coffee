from sqlalchemy import Boolean, Column, SmallInteger, String

from backend.database import Base


class Currency(Base):
    """ISO 4217 currency catalog. FK target for every monetary column.

    Seeded with COP, USD, EUR in the initial migration. ``minor_unit`` is the
    number of decimal places the currency uses (COP=0, USD/EUR=2).
    """

    __tablename__ = "currencies"

    code: str = Column(String(3), primary_key=True)
    name: str = Column(String(64), nullable=False)
    minor_unit: int = Column(SmallInteger, nullable=False, default=2)
    is_active: bool = Column(Boolean, nullable=False, default=True)
