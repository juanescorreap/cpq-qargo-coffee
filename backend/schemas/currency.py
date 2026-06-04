from pydantic import BaseModel, ConfigDict, field_validator


class CurrencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    minor_unit: int
    is_active: bool


def validate_currency_code(v: str) -> str:
    """Shared validator: normalize and check an ISO 4217 3-letter code."""
    v = v.strip().upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError("currency_code must be a 3-letter ISO 4217 code (e.g. COP, USD)")
    return v


class CurrencyCreate(BaseModel):
    code: str
    name: str
    minor_unit: int = 2
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def code_valid(cls, v: str) -> str:
        return validate_currency_code(v)
