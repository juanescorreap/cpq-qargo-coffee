from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    String,
)
from sqlalchemy.sql import func

from backend.database import Base


class FxRate(Base):
    """Effective-dated exchange rate: 1 base_code = rate quote_code (V3).

    Source of truth for multi-currency normalization; query via
    ``fn_convert_amount(amount, from, to, date)``. The no-overlap EXCLUDE
    (no_overlap_fx) is defined in migration 0017.
    """

    __tablename__ = "fx_rates"

    __table_args__ = (
        CheckConstraint("rate > 0", name="ck_fx_rates_rate_positive"),
        CheckConstraint("base_code <> quote_code", name="ck_fx_rates_diff"),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from", name="ck_fx_rates_validity"
        ),
    )

    id: int = Column(BigInteger, Identity(always=True), primary_key=True)
    base_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    quote_code: str = Column(
        String(3),
        ForeignKey("currencies.code", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    rate: object = Column(Numeric(18, 8), nullable=False)
    valid_from: object = Column(Date, nullable=False, server_default=func.current_date())
    valid_until: object | None = Column(Date)
    source: str | None = Column(String(120))
    created_at: object = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
