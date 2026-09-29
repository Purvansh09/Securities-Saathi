"""FIFO lot matching (CLAUDE.md rule 6).

Pure: no database, no clock, no I/O -- just a list of ledger entries in, open lots and
realised matches out. Everything portfolio-related (holdings, average cost, realised and
unrealised P/L, and later the tax engine's holding periods) is derived from this.

Conventions:
- Ordering matches `ledger_balance_ok()` in the Phase 1 migration exactly: executed_at,
  then BUY before SELL on the same timestamp, then created_at, then id. If the two ever
  disagreed, the DB could accept a sequence that FIFO then can't match.
- Buy charges are part of the lot's cost basis. Sell charges reduce the sale's proceeds.
  When a sale consumes part of a lot (or spans several lots), cost and sell charges are
  split pro rata by quantity, and the final slice takes whatever is left, so the pieces
  always add back up to the exact totals -- no rounding residue.
- Pro-rata splits are quantized to 10 decimal places (_SPLIT_SCALE). That is internal
  precision, not display rounding (rule 4 -- callers still round for display): a split
  like 31/3 is otherwise a 40-digit value, and summing several of those later at Python's
  default 28-digit precision silently rounds (31 came back as 30.999...). At a fixed
  scale every later sum is exact, and the remainder slice keeps totals exact too.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Hashable, Iterable

IST = timezone(timedelta(hours=5, minutes=30))
_ZERO = Decimal(0)
_SPLIT_SCALE = Decimal("1e-10")


def _split(total: Decimal, part: Decimal, whole: Decimal) -> Decimal:
    return (total * part / whole).quantize(_SPLIT_SCALE, rounding=ROUND_HALF_EVEN)


class FifoError(ValueError):
    """The entries can't be matched -- e.g. a SELL with no open lots left. The ledger guard
    (services/ledger.py) should make this unreachable for data read from the database."""


@dataclass(frozen=True)
class LedgerEntry:
    id: Hashable
    txn_type: str  # "BUY" or "SELL"
    quantity: Decimal
    price: Decimal
    charges: Decimal
    executed_at: datetime
    created_at: datetime | None = None


@dataclass
class OpenLot:
    buy_id: Hashable
    acquired_at: datetime
    quantity: Decimal  # still held from this buy
    cost: Decimal  # cost basis of the quantity still held, buy charges included

    @property
    def unit_cost(self) -> Decimal:
        return self.cost / self.quantity


@dataclass(frozen=True)
class LotMatch:
    """One slice of a SELL matched against one BUY lot."""

    buy_id: Hashable
    sell_id: Hashable
    acquired_at: datetime
    sold_at: datetime
    quantity: Decimal
    cost: Decimal
    proceeds: Decimal  # net of this slice's share of the sell charges

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.cost

    @property
    def acquired_on(self) -> date:
        return self.acquired_at.astimezone(IST).date()

    @property
    def sold_on(self) -> date:
        return self.sold_at.astimezone(IST).date()

    @property
    def holding_days(self) -> int:
        # Calendar days between IST trade dates (rule 11). The tax engine will apply its
        # own month-based thresholds to acquired_on/sold_on rather than rely on this.
        return (self.sold_on - self.acquired_on).days


@dataclass
class FifoResult:
    open_lots: list[OpenLot] = field(default_factory=list)
    matches: list[LotMatch] = field(default_factory=list)

    @property
    def quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self.open_lots), _ZERO)

    @property
    def cost_basis(self) -> Decimal:
        return sum((lot.cost for lot in self.open_lots), _ZERO)

    @property
    def avg_cost(self) -> Decimal | None:
        qty = self.quantity
        return self.cost_basis / qty if qty else None

    @property
    def realized_pnl(self) -> Decimal:
        return sum((m.gain for m in self.matches), _ZERO)


def ordering_key(entry: LedgerEntry) -> tuple:
    return (
        entry.executed_at,
        0 if entry.txn_type == "BUY" else 1,
        entry.created_at or entry.executed_at,
        str(entry.id),  # Postgres orders uuids bytewise == lowercase-hex string order
    )


def _validate(entry: LedgerEntry) -> None:
    if entry.txn_type not in ("BUY", "SELL"):
        raise FifoError(f"unknown txn_type {entry.txn_type!r} on {entry.id}")
    if entry.quantity <= 0:
        raise FifoError(f"quantity must be > 0 on {entry.id}")
    if entry.price < 0 or entry.charges < 0:
        raise FifoError(f"price and charges must be >= 0 on {entry.id}")


def match_fifo(entries: Iterable[LedgerEntry]) -> FifoResult:
    """Match every SELL against the oldest open BUY lots, for ONE user + security."""
    lots: deque[OpenLot] = deque()
    matches: list[LotMatch] = []

    # Headroom beyond Decimal's default 28 significant digits for the pro-rata splits.
    with localcontext() as ctx:
        ctx.prec = 40

        for entry in sorted(entries, key=ordering_key):
            _validate(entry)

            if entry.txn_type == "BUY":
                lots.append(
                    OpenLot(
                        buy_id=entry.id,
                        acquired_at=entry.executed_at,
                        quantity=entry.quantity,
                        cost=entry.quantity * entry.price + entry.charges,
                    )
                )
                continue

            net_proceeds = entry.quantity * entry.price - entry.charges
            to_sell = entry.quantity
            proceeds_left = net_proceeds

            while to_sell > 0:
                if not lots:
                    raise FifoError(
                        f"SELL {entry.id} exceeds the quantity held by {to_sell}"
                    )
                lot = lots[0]
                take = min(to_sell, lot.quantity)

                cost = lot.cost if take == lot.quantity else _split(lot.cost, take, lot.quantity)
                proceeds = (
                    proceeds_left if take == to_sell else _split(net_proceeds, take, entry.quantity)
                )

                matches.append(
                    LotMatch(
                        buy_id=lot.buy_id,
                        sell_id=entry.id,
                        acquired_at=lot.acquired_at,
                        sold_at=entry.executed_at,
                        quantity=take,
                        cost=cost,
                        proceeds=proceeds,
                    )
                )

                lot.quantity -= take
                lot.cost -= cost
                if lot.quantity == 0:
                    lots.popleft()
                to_sell -= take
                proceeds_left -= proceeds

    return FifoResult(open_lots=list(lots), matches=matches)
