"""Turns FIFO results + prices into portfolio numbers. Pure, like fifo.py: no DB, and the
valuation date is always passed in rather than read from the clock.

Two situations the numbers have to stay honest about:
- No price yet. Common right now (the price refresh job isn't built) and later for any
  thinly traded security. Such a holding is carried at cost -- market_value is None,
  it contributes zero unrealised P/L, and it's counted in `unpriced_holdings` so the UI
  can say so -- rather than silently dropping out of the totals.
- Bonds (rule 10). Retail bonds rarely have a daily price, so without one they're valued
  at face value plus accrued interest and flagged `estimated`.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Hashable, Iterable

from app.services.fifo import FifoResult

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
DEFAULT_BOND_FACE_VALUE = Decimal(100)  # Indian G-Secs and most listed bonds quote per Rs 100 face


@dataclass(frozen=True)
class SecurityRef:
    security_id: Hashable
    isin: str
    symbol: str | None
    name: str
    type: str
    sector: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PriceQuote:
    price: Decimal
    as_of: date
    estimated: bool


# --- bonds ------------------------------------------------------------------------------

def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _days_30_360(start: date, end: date) -> int:
    """30/360 bond basis -- the day count Indian G-Secs use for accrued interest."""
    d1 = min(start.day, 30)
    d2 = 30 if (end.day == 31 and d1 == 30) else end.day
    return (end.year - start.year) * 360 + (end.month - start.month) * 30 + (d2 - d1)


def estimate_bond_price(metadata: dict[str, Any], as_of: date) -> Decimal | None:
    """Face value + accrued interest since the last coupon date, per unit.

    Coupon dates are taken to fall on the maturity date's day/month, stepping back
    12/frequency months at a time (semi-annual by default, as for G-Secs). Returns None
    when the metadata doesn't carry enough to estimate from.
    """
    try:
        coupon_pct = Decimal(str(metadata["coupon_pct"]))
        maturity = date.fromisoformat(str(metadata["maturity_date"]))
        face = Decimal(str(metadata.get("face_value", DEFAULT_BOND_FACE_VALUE)))
        frequency = int(metadata.get("coupon_frequency", 2))
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None
    if frequency not in (1, 2, 4, 12) or coupon_pct < 0 or face <= 0:
        return None

    if as_of >= maturity:
        return face  # redeemed at par; nothing further accrues

    step = 12 // frequency
    n = 1
    last_coupon = _add_months(maturity, -step)
    while last_coupon > as_of:
        n += 1
        last_coupon = _add_months(maturity, -step * n)  # always from maturity, so no day drift

    accrued = face * coupon_pct / _HUNDRED * Decimal(_days_30_360(last_coupon, as_of)) / Decimal(360)
    return face + accrued


def resolve_quote(
    security: SecurityRef,
    latest_price: Decimal | None,
    latest_price_date: date | None,
    as_of: date,
) -> PriceQuote | None:
    if latest_price is not None and latest_price_date is not None:
        return PriceQuote(latest_price, latest_price_date, estimated=False)
    if security.type == "BOND":
        estimate = estimate_bond_price(security.metadata, as_of)
        if estimate is not None:
            return PriceQuote(estimate, as_of, estimated=True)
    return None


# --- positions --------------------------------------------------------------------------

@dataclass(frozen=True)
class Position:
    security: SecurityRef
    fifo: FifoResult
    quote: PriceQuote | None

    @property
    def quantity(self) -> Decimal:
        return self.fifo.quantity

    @property
    def is_open(self) -> bool:
        return self.quantity > 0

    @property
    def invested(self) -> Decimal:
        return self.fifo.cost_basis

    @property
    def market_value(self) -> Decimal | None:
        if self.quote is None or not self.is_open:
            return None
        return self.quantity * self.quote.price

    @property
    def value(self) -> Decimal:
        """What the position counts for in totals and weights: market value when priced,
        cost otherwise."""
        mv = self.market_value
        return mv if mv is not None else self.invested

    @property
    def unrealized_pnl(self) -> Decimal | None:
        mv = self.market_value
        return None if mv is None else mv - self.invested

    @property
    def unrealized_pnl_pct(self) -> Decimal | None:
        pnl = self.unrealized_pnl
        if pnl is None or self.invested == 0:
            return None
        return pnl / self.invested * _HUNDRED

    @property
    def realized_pnl(self) -> Decimal:
        return self.fifo.realized_pnl


def weight_pct(value: Decimal, total: Decimal) -> Decimal:
    return _ZERO if total == 0 else value / total * _HUNDRED


@dataclass(frozen=True)
class PortfolioTotals:
    invested: Decimal
    current_value: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_pct: Decimal | None
    realized_pnl: Decimal
    holdings_count: int
    unpriced_holdings: int
    estimated_holdings: int


def summarize(positions: Iterable[Position]) -> PortfolioTotals:
    positions = list(positions)
    open_positions = [p for p in positions if p.is_open]

    invested = sum((p.invested for p in open_positions), _ZERO)
    current_value = sum((p.value for p in open_positions), _ZERO)
    unrealized = current_value - invested  # unpriced holdings contribute exactly zero

    return PortfolioTotals(
        invested=invested,
        current_value=current_value,
        unrealized_pnl=unrealized,
        unrealized_pnl_pct=None if invested == 0 else unrealized / invested * _HUNDRED,
        # realised P/L includes securities that have since been fully sold
        realized_pnl=sum((p.realized_pnl for p in positions), _ZERO),
        holdings_count=len(open_positions),
        unpriced_holdings=sum(1 for p in open_positions if p.quote is None),
        estimated_holdings=sum(1 for p in open_positions if p.quote is not None and p.quote.estimated),
    )


@dataclass(frozen=True)
class AllocationBucket:
    key: str
    value: Decimal
    weight_pct: Decimal
    holdings_count: int


UNCLASSIFIED = "Unclassified"


def allocate(positions: Iterable[Position], by: str) -> tuple[Decimal, list[AllocationBucket]]:
    """Group open positions by asset class (security type) or sector, weighted by value."""
    if by not in ("asset_class", "sector"):
        raise ValueError(f"unknown allocation dimension {by!r}")

    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for p in positions:
        if not p.is_open:
            continue
        key = p.security.type if by == "asset_class" else (p.security.sector or UNCLASSIFIED)
        totals[key] = totals.get(key, _ZERO) + p.value
        counts[key] = counts.get(key, 0) + 1

    grand_total = sum(totals.values(), _ZERO)
    buckets = [
        AllocationBucket(key, value, weight_pct(value, grand_total), counts[key])
        for key, value in totals.items()
    ]
    buckets.sort(key=lambda b: (-b.value, b.key))
    return grand_total, buckets
