"""XIRR: the annualised return of irregular cash flows (CLAUDE.md rule 9).

Why not CAGR: CAGR assumes one investment at the start and one value at the end. A real
portfolio adds and withdraws money at arbitrary dates, and XIRR weights every rupee by how long
it was actually invested. It's the same definition as Excel's XIRR: find the rate r with

    sum( amount_i / (1 + r) ** (days_i / 365) ) == 0

where days_i counts from the first flow. Sign convention: money the investor puts in is
negative, money they get back (sells, and today's value of what they still hold) is positive.

The root is found by bisection in Decimal arithmetic. It's slower than Newton's method but it
can't diverge or jump to a nonsense root: once NPV changes sign across a bracket, the rate is
guaranteed to be inside it and the bracket halves every step. Money stays Decimal (rule 4).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext

from app.services.fifo import IST

_ONE = Decimal(1)
_DAYS_PER_YEAR = Decimal(365)  # Excel's XIRR convention (actual/365)
_LOWEST_RATE = Decimal("-0.9999")  # -100% is total loss; the formula is undefined at exactly -1
_HIGHEST_RATE = Decimal(1_000_000)  # 100,000,000% a year; anything beyond is noise, not a return
_TOLERANCE = Decimal("1e-12")
_MAX_ITERATIONS = 300
RESULT_SCALE = Decimal("1e-10")


@dataclass(frozen=True)
class CashFlow:
    on: date
    amount: Decimal  # negative = invested, positive = received


@dataclass(frozen=True)
class Trade:
    txn_type: str  # BUY | SELL
    quantity: Decimal
    price: Decimal
    charges: Decimal
    executed_at: datetime


def trade_flows(trades: Iterable[Trade]) -> list[CashFlow]:
    """A BUY costs quantity x price plus charges; a SELL returns quantity x price minus
    charges. Dated by the IST calendar day of the trade (rule 11)."""
    flows = []
    for t in trades:
        gross = t.quantity * t.price
        amount = -(gross + t.charges) if t.txn_type == "BUY" else gross - t.charges
        flows.append(CashFlow(t.executed_at.astimezone(IST).date(), amount))
    return flows


def _npv(rate: Decimal, flows: list[tuple[Decimal, Decimal]]) -> Decimal:
    base = _ONE + rate
    return sum((amount / base**years for years, amount in flows), Decimal(0))


def xirr(cash_flows: list[CashFlow]) -> Decimal | None:
    """Annual rate as a fraction (0.1234 = 12.34% a year), or None when it isn't defined:
    no flows in one direction (nothing invested, or nothing received), everything on a single
    day (no time has passed, so there's no rate), or no sign change in the search range."""
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    for cf in cash_flows:
        by_day[cf.on] += cf.amount
    days = sorted(d for d, amt in by_day.items() if amt != 0)
    if len(days) < 2:
        return None
    if not any(by_day[d] < 0 for d in days) or not any(by_day[d] > 0 for d in days):
        return None

    with localcontext() as ctx:
        ctx.prec = 40  # headroom: (1 + r) ** fractional-years for large r loses digits at 28
        start = days[0]
        flows = [(Decimal((d - start).days) / _DAYS_PER_YEAR, by_day[d]) for d in days]

        lo, hi = _LOWEST_RATE, Decimal(1)
        npv_lo = _npv(lo, flows)
        # Widen the upper bound until NPV changes sign (short, very profitable periods can
        # annualise to huge rates), but never past _HIGHEST_RATE.
        while (npv_lo > 0) == (_npv(hi, flows) > 0):
            if hi >= _HIGHEST_RATE:
                return None
            hi *= 10

        for _ in range(_MAX_ITERATIONS):
            mid = (lo + hi) / 2
            npv_mid = _npv(mid, flows)
            if npv_mid == 0 or (hi - lo) / 2 < _TOLERANCE:
                break
            if (npv_mid > 0) == (npv_lo > 0):
                lo, npv_lo = mid, npv_mid
            else:
                hi = mid
        return ((lo + hi) / 2).quantize(RESULT_SCALE)
