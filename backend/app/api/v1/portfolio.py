"""GET /portfolio/summary, /holdings, /holdings/{isin}, /allocation.

Nothing here is stored (rule 1): every request reads the caller's live ledger in one
query, runs FIFO per security (services/fifo.py), and values the result against the
latest price (services/valuation.py). Read-only -- there is deliberately no way to
write a holding.

Rounding: values are computed at full precision and formatted to 6 decimal places, the
same scale the database stores (numeric(20,6)); rounding to paise for display is the
frontend's job (rule 4).
"""

from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.v1.pagination import ISIN_PATTERN, decode_cursor, encode_cursor
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.portfolio import (
    Allocation,
    AllocationBucketOut,
    HoldingDetail,
    HoldingList,
    HoldingOut,
    OpenLotOut,
    PortfolioSummary,
    RealizedLotOut,
)
from app.schemas.security import SecurityType
from app.services.fifo import IST, LedgerEntry, match_fifo
from app.services.valuation import Position, SecurityRef, allocate, resolve_quote, summarize, weight_pct

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

_SCALE = Decimal("0.000001")

# One pass over the caller's whole ledger. The ORDER BY is the same tiebreak as
# ledger_balance_ok() (fifo.py re-sorts identically anyway, so this is belt and braces).
_POSITIONS_QUERY = """
    select t.id, t.security_id, t.txn_type, t.quantity, t.price, t.charges,
           t.executed_at, t.created_at,
           s.isin, s.symbol, s.name, s.type, s.sector, s.metadata,
           lp.price as latest_price, lp.price_date as latest_price_date
    from public.transactions t
    join public.securities s on s.id = t.security_id
    left join public.latest_prices lp on lp.security_id = t.security_id
    where t.user_id = $1
      and t.deleted_at is null
    order by t.security_id, t.executed_at,
             case when t.txn_type = 'BUY' then 0 else 1 end, t.created_at, t.id
"""


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(_SCALE, rounding=ROUND_HALF_EVEN))


def today_ist() -> date:
    return datetime.now(IST).date()


async def _load_positions(conn: asyncpg.Connection, user_id: UUID, as_of: date) -> list[Position]:
    rows = await conn.fetch(_POSITIONS_QUERY, user_id)

    by_security: dict = defaultdict(list)
    for row in rows:
        by_security[row["security_id"]].append(row)

    positions = []
    for security_id, sec_rows in by_security.items():
        first = sec_rows[0]
        ref = SecurityRef(
            security_id=security_id,
            isin=first["isin"],
            symbol=first["symbol"],
            name=first["name"],
            type=first["type"],
            sector=first["sector"],
            metadata=first["metadata"] or {},
        )
        fifo = match_fifo(
            LedgerEntry(
                id=r["id"],
                txn_type=r["txn_type"],
                quantity=r["quantity"],
                price=r["price"],
                charges=r["charges"],
                executed_at=r["executed_at"],
                created_at=r["created_at"],
            )
            for r in sec_rows
        )
        quote = resolve_quote(ref, first["latest_price"], first["latest_price_date"], as_of)
        positions.append(Position(ref, fifo, quote))
    return positions


def _holding_fields(p: Position, portfolio_value: Decimal) -> dict:
    return {
        "isin": p.security.isin,
        "symbol": p.security.symbol,
        "name": p.security.name,
        "type": p.security.type,
        "sector": p.security.sector,
        "quantity": _s(p.quantity),
        "avg_cost": _s(p.fifo.avg_cost),
        "invested": _s(p.invested),
        "price": _s(p.quote.price) if p.quote else None,
        "price_date": p.quote.as_of if p.quote else None,
        "price_estimated": bool(p.quote and p.quote.estimated),
        "market_value": _s(p.market_value),
        "unrealized_pnl": _s(p.unrealized_pnl),
        "unrealized_pnl_pct": _s(p.unrealized_pnl_pct),
        "realized_pnl": _s(p.realized_pnl),
        "weight_pct": _s(weight_pct(p.value, portfolio_value) if p.is_open else Decimal(0)),
    }


@router.get("/summary", response_model=PortfolioSummary)
async def get_summary(
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> PortfolioSummary:
    as_of = today_ist()
    totals = summarize(await _load_positions(conn, user_id, as_of))
    return PortfolioSummary(
        as_of=as_of,
        invested=_s(totals.invested),
        current_value=_s(totals.current_value),
        unrealized_pnl=_s(totals.unrealized_pnl),
        unrealized_pnl_pct=_s(totals.unrealized_pnl_pct),
        realized_pnl=_s(totals.realized_pnl),
        holdings_count=totals.holdings_count,
        unpriced_holdings=totals.unpriced_holdings,
        estimated_holdings=totals.estimated_holdings,
    )


@router.get("/holdings", response_model=HoldingList)
async def list_holdings(
    security_type: SecurityType | None = Query(default=None, alias="type"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> HoldingList:
    offset = decode_cursor(cursor)
    positions = await _load_positions(conn, user_id, today_ist())

    # Weights are always relative to the WHOLE portfolio, even when filtering by type.
    portfolio_value = summarize(positions).current_value
    held = [
        p for p in positions
        if p.is_open and (security_type is None or p.security.type == security_type.value)
    ]
    held.sort(key=lambda p: (-p.value, p.security.isin))

    page = held[offset: offset + limit]
    return HoldingList(
        items=[HoldingOut(**_holding_fields(p, portfolio_value)) for p in page],
        next_cursor=encode_cursor(offset + limit) if offset + limit < len(held) else None,
    )


@router.get("/holdings/{isin}", response_model=HoldingDetail)
async def get_holding(
    isin: str = Path(..., pattern=ISIN_PATTERN),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> HoldingDetail:
    positions = await _load_positions(conn, user_id, today_ist())
    position = next((p for p in positions if p.security.isin == isin), None)
    if position is None:
        # Also covers ISINs the caller has never traded -- no ledger, no holding.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No transactions for this security")

    portfolio_value = summarize(positions).current_value
    return HoldingDetail(
        **_holding_fields(position, portfolio_value),
        open_lots=[
            OpenLotOut(
                buy_transaction_id=lot.buy_id,
                acquired_at=lot.acquired_at,
                quantity=_s(lot.quantity),
                unit_cost=_s(lot.unit_cost),
                cost=_s(lot.cost),
            )
            for lot in position.fifo.open_lots
        ],
        realized=[
            RealizedLotOut(
                buy_transaction_id=m.buy_id,
                sell_transaction_id=m.sell_id,
                acquired_at=m.acquired_at,
                sold_at=m.sold_at,
                quantity=_s(m.quantity),
                cost=_s(m.cost),
                proceeds=_s(m.proceeds),
                gain=_s(m.gain),
                holding_days=m.holding_days,
            )
            for m in position.fifo.matches
        ],
    )


@router.get("/allocation", response_model=Allocation)
async def get_allocation(
    by: str = Query(default="asset_class", pattern="^(asset_class|sector)$"),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> Allocation:
    total, buckets = allocate(await _load_positions(conn, user_id, today_ist()), by)
    return Allocation(
        by=by,
        total_value=_s(total),
        buckets=[
            AllocationBucketOut(
                key=b.key, value=_s(b.value), weight_pct=_s(b.weight_pct), holdings_count=b.holdings_count
            )
            for b in buckets
        ],
    )
