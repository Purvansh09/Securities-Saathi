from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class SecurityType(str, Enum):
    STOCK = "STOCK"
    MF = "MF"
    ETF = "ETF"
    BOND = "BOND"


class SecuritySummary(BaseModel):
    """One row in a /securities search/list result -- deliberately thin, full
    detail (metadata, timestamps) is only fetched for a single security."""

    isin: str
    name: str
    type: SecurityType
    symbol: str | None
    exchange: str | None
    sector: str | None
    is_custom: bool


class SecurityList(BaseModel):
    items: list[SecuritySummary]
    next_cursor: str | None


class SecurityDetail(BaseModel):
    id: UUID
    isin: str
    name: str
    type: SecurityType
    symbol: str | None
    exchange: str | None
    sector: str | None
    is_active: bool
    is_custom: bool
    metadata: dict[str, Any]
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LatestPrice(BaseModel):
    isin: str
    price_date: date
    price: str  # decimal-as-string (CLAUDE.md API conventions) -- never float, no precision loss
    source: str


class PricePoint(BaseModel):
    price_date: date
    price: str  # see LatestPrice.price
    source: str


class PriceHistory(BaseModel):
    isin: str
    points: list[PricePoint]
