"""Portfolio response models. Every money/quantity/percentage field is a decimal string
(rule 4); fields are None where the number genuinely doesn't exist yet (no price)."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.security import SecurityType


class HoldingOut(BaseModel):
    isin: str
    symbol: str | None
    name: str
    type: SecurityType
    sector: str | None
    quantity: str
    avg_cost: str | None  # per unit, buy charges included; None once fully sold
    invested: str  # FIFO cost basis of the units still held
    price: str | None
    price_date: date | None
    price_estimated: bool  # bond valued at face + accrued interest (rule 10)
    market_value: str | None  # None when there's no price -- the holding is carried at cost
    unrealized_pnl: str | None
    unrealized_pnl_pct: str | None
    realized_pnl: str  # to date, from FIFO-matched sells of this security
    weight_pct: str  # share of the whole portfolio's current value


class HoldingList(BaseModel):
    items: list[HoldingOut]
    next_cursor: str | None


class OpenLotOut(BaseModel):
    buy_transaction_id: UUID
    acquired_at: datetime
    quantity: str
    unit_cost: str
    cost: str


class RealizedLotOut(BaseModel):
    buy_transaction_id: UUID
    sell_transaction_id: UUID
    acquired_at: datetime
    sold_at: datetime
    quantity: str
    cost: str
    proceeds: str
    gain: str
    holding_days: int


class HoldingDetail(HoldingOut):
    open_lots: list[OpenLotOut]
    realized: list[RealizedLotOut]


class PortfolioSummary(BaseModel):
    as_of: date
    invested: str
    current_value: str  # market value where priced, cost where not
    unrealized_pnl: str
    unrealized_pnl_pct: str | None
    realized_pnl: str  # includes securities since fully sold
    holdings_count: int
    unpriced_holdings: int  # carried at cost -- the UI should say so
    estimated_holdings: int


class AllocationBucketOut(BaseModel):
    key: str
    value: str
    weight_pct: str
    holdings_count: int


class Allocation(BaseModel):
    by: Literal["asset_class", "sector"]
    total_value: str
    buckets: list[AllocationBucketOut]
