"""XIRR response models. Money and rates are decimal strings (rule 4)."""

from datetime import date

from pydantic import BaseModel

from app.schemas.security import SecurityType


class XirrResult(BaseModel):
    xirr_pct: str | None  # annualised, already x100; None when undefined (see services/xirr.py)
    invested: str  # total paid on buys, charges included
    received: str  # total from sells, net of charges
    current_value: str  # what's still held, at market (or cost where unpriced)
    first_date: date | None
    days: int  # from the first trade to the valuation date
    # Under a year, annualising exaggerates: +5% in 30 days reads as ~80% a year. The UI says so.
    short_period: bool
    includes_unpriced: bool  # some holding was valued at cost because it has no price


class XirrHolding(XirrResult):
    isin: str
    symbol: str | None
    name: str
    type: SecurityType
    is_open: bool


class XirrReport(BaseModel):
    as_of: date
    portfolio: XirrResult
    holdings: list[XirrHolding]
