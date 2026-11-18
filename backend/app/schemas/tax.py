"""Tax report models. Money and rates are decimal strings (rule 4)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.security import SecurityType


class TaxLotOut(BaseModel):
    isin: str
    symbol: str | None
    name: str
    type: SecurityType
    asset_basis: str  # why it got this treatment
    classification_certain: bool
    acquired_on: date
    sold_on: date
    holding_days: int
    quantity: str
    cost: str
    proceeds: str
    gain: str
    term: Literal["SHORT", "LONG"]
    rule: str
    rate_pct: str | None  # None = slab rate
    notes: list[str]


class TaxBucketOut(BaseModel):
    label: str
    term: Literal["SHORT", "LONG"]
    category: Literal["EQUITY", "OTHER"]
    rate_pct: str | None
    gains: str
    losses: str
    net: str
    set_off: str  # other buckets' losses absorbed by this one
    exemption: str
    taxable: str
    tax: str | None  # None for slab-rate buckets


class TaxSummaryOut(BaseModel):
    exemption_limit: str
    exemption_used: str
    special_rate_tax: str
    cess: str
    total_estimated_tax: str  # special-rate tax + cess
    slab_income: str
    short_term_loss_carried: str
    long_term_loss_carried: str
    notes: list[str]


class TaxReportOut(BaseModel):
    fy: str
    period_from: date
    period_to: date
    years_available: list[str]
    rules_verified_on: date
    sources: list[str]
    lots: list[TaxLotOut]
    buckets: list[TaxBucketOut]
    summary: TaxSummaryOut
