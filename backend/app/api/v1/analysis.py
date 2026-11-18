"""GET /analysis/xirr: annualised returns for the whole portfolio and for each security.

Cash flows come straight from the ledger (rule 1): every BUY is money in, every SELL money
out, and whatever is still held counts as one final inflow at today's value. Securities
without a price are valued at cost, the same way the dashboard totals do it, and flagged.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from app.api.v1.portfolio import _s, build_positions, fetch_ledger, today_ist
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.analysis import XirrHolding, XirrReport, XirrResult
from app.services.valuation import Position
from app.services.xirr import CashFlow, Trade, trade_flows, xirr

router = APIRouter(prefix="/analysis", tags=["analysis"])

_ZERO = Decimal(0)


def _result(flows: list[CashFlow], positions: list[Position], as_of: date) -> dict:
    current = sum((p.value for p in positions if p.is_open), _ZERO)
    all_flows = flows + ([CashFlow(as_of, current)] if current > 0 else [])
    rate = xirr(all_flows)
    first = min((f.on for f in flows), default=None)
    days = (as_of - first).days if first else 0
    return {
        "xirr_pct": _s(rate * 100) if rate is not None else None,
        "invested": _s(-sum((f.amount for f in flows if f.amount < 0), _ZERO)),
        "received": _s(sum((f.amount for f in flows if f.amount > 0), _ZERO)),
        "current_value": _s(current),
        "first_date": first,
        "days": days,
        "short_period": days < 365,
        "includes_unpriced": any(p.is_open and p.quote is None for p in positions),
    }


@router.get("/xirr", response_model=XirrReport)
async def get_xirr(
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> XirrReport:
    as_of = today_ist()
    ledger = await fetch_ledger(conn, user_id)
    positions = {p.security.security_id: p for p in build_positions(ledger, as_of)}

    all_flows: list[CashFlow] = []
    holdings: list[XirrHolding] = []
    for security_id, rows in ledger.items():
        flows = trade_flows(
            Trade(r["txn_type"], r["quantity"], r["price"], r["charges"], r["executed_at"]) for r in rows
        )
        all_flows += flows
        p = positions[security_id]
        holdings.append(
            XirrHolding(
                isin=p.security.isin,
                symbol=p.security.symbol,
                name=p.security.name,
                type=p.security.type,
                is_open=p.is_open,
                **_result(flows, [p], as_of),
            )
        )

    # Open positions first, biggest first; then closed ones.
    holdings.sort(key=lambda h: (not h.is_open, -Decimal(h.current_value), h.isin))
    return XirrReport(
        as_of=as_of,
        portfolio=XirrResult(**_result(all_flows, list(positions.values()), as_of)),
        holdings=holdings,
    )
