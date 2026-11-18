"""Downloadable statements (CLAUDE.md API conventions: "File downloads stream, they do not return JSON").

  GET /reports/holdings?format=xlsx|pdf
  GET /reports/realised?fy=2026-27&format=xlsx|pdf

Built from the same ledger, FIFO, valuation, XIRR and tax code as the screens.
"""

import io
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.v1.analysis import _result as xirr_result
from app.api.v1.portfolio import build_positions, fetch_ledger, today_ist
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.services.fifo import IST
from app.services.statements import Column, Statement, Table, inr, pct, to_pdf, to_xlsx
from app.services.tax.engine import financial_year, fy_bounds
from app.services.tax.report import taxed_lots, year_report, years_with_sales
from app.services.tax.rules import RULES_VERIFIED_ON
from app.services.valuation import allocate, summarize, weight_pct
from app.services.xirr import Trade, trade_flows

router = APIRouter(prefix="/reports", tags=["reports"])

_ZERO = Decimal(0)
_TYPE = {"STOCK": "Stock", "ETF": "ETF", "MF": "Mutual fund", "BOND": "Bond"}
_MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def _stream(st: Statement, fmt: str) -> StreamingResponse:
    content = to_xlsx(st) if fmt == "xlsx" else to_pdf(st)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=_MEDIA[fmt],
        headers={"Content-Disposition": f'attachment; filename="{st.filename}.{fmt}"'},
    )


async def _owner(conn: asyncpg.Connection, user_id: UUID) -> str:
    return await conn.fetchval("select coalesce(full_name, '') from public.profiles where id = $1", user_id) or ""


def _generated() -> str:
    return f"generated {datetime.now(IST):%d %b %Y, %I:%M %p} IST"


@router.get("/holdings")
async def holdings_statement(
    format: Literal["xlsx", "pdf"] = Query(default="xlsx"),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> StreamingResponse:
    today = today_ist()
    ledger = await fetch_ledger(conn, user_id)
    positions = build_positions(ledger, today)
    totals = summarize(positions)

    # XIRR per security and for the whole portfolio, same function as GET /analysis/xirr.
    flows_by_sec = {
        sid: trade_flows(Trade(r["txn_type"], r["quantity"], r["price"], r["charges"], r["executed_at"]) for r in rows)
        for sid, rows in ledger.items()
    }
    by_id = {p.security.security_id: p for p in positions}
    sec_xirr = {sid: xirr_result(f, [by_id[sid]], today)["xirr_pct"] for sid, f in flows_by_sec.items()}
    all_flows = [f for fl in flows_by_sec.values() for f in fl]
    port_xirr = xirr_result(all_flows, positions, today)["xirr_pct"] if positions else None

    held = sorted((p for p in positions if p.is_open), key=lambda p: (-p.value, p.security.isin))
    rows = []
    for p in held:
        s = p.security
        rows.append([
            s.symbol or s.name, s.isin, _TYPE.get(s.type, s.type), p.quantity, p.fifo.avg_cost, p.invested,
            p.quote.price if p.quote else None,
            (p.quote.as_of if p.quote else None),
            p.value, p.unrealized_pnl, p.unrealized_pnl_pct,
            Decimal(sec_xirr[s.security_id]) if sec_xirr.get(s.security_id) else None,
            weight_pct(p.value, totals.current_value),
        ])

    total_value, buckets = allocate(positions, "asset_class")
    st = Statement(
        title="Holdings statement",
        subtitle=f"As of {today:%d %b %Y} ({_generated()})",
        owner=await _owner(conn, user_id),
        summary=[
            ("Current value", inr(totals.current_value)),
            ("Invested", inr(totals.invested)),
            ("Unrealised P/L", f"{inr(totals.unrealized_pnl, sign=True)} ({pct(totals.unrealized_pnl_pct)})"),
            ("Realised P/L to date", inr(totals.realized_pnl, sign=True)),
            ("XIRR (annualised)", pct(Decimal(port_xirr)) if port_xirr else "-"),
            ("Holdings", str(totals.holdings_count)),
        ],
        tables=[
            Table(
                "Holdings",
                [
                    Column("Security", width=24), Column("ISIN", width=15), Column("Type", width=11),
                    Column("Quantity", "qty", 11), Column("Avg cost", "inr", 12), Column("Invested", "inr", 14),
                    Column("Price", "inr", 12), Column("Price date", "date", 12), Column("Value", "inr", 14),
                    Column("Unrealised P/L", "inr", 14), Column("P/L %", "pct", 9), Column("XIRR %", "pct", 9),
                    Column("Weight %", "share", 9),
                ],
                rows,
                totals=["Total", "", "", None, None, totals.invested, None, None, totals.current_value,
                        totals.unrealized_pnl, totals.unrealized_pnl_pct, Decimal(port_xirr) if port_xirr else None,
                        Decimal(100) if held else None],
            ),
            Table(
                "Totals by asset class",
                [Column("Asset class", width=24), Column("Holdings", "int", 10), Column("Value", "inr", 16), Column("Weight %", "share", 10)],
                [[_TYPE.get(b.key, b.key), b.holdings_count, b.value, b.weight_pct] for b in buckets],
                totals=["Total", sum(b.holdings_count for b in buckets), total_value, Decimal(100) if buckets else None],
            ),
        ],
        notes=[
            n for n in [
                f"{totals.unpriced_holdings} holding(s) have no price yet and are shown at cost." if totals.unpriced_holdings else "",
                f"{totals.estimated_holdings} bond(s) are valued at face value plus accrued interest (estimated)." if totals.estimated_holdings else "",
                "XIRR is annualised; for holdings under a year old it can look much larger than the actual gain.",
            ] if n
        ],
        filename=f"holdings-statement-{today:%Y-%m-%d}",
    )
    return _stream(st, format)


@router.get("/realised")
async def realised_statement(
    fy: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    format: Literal["xlsx", "pdf"] = Query(default="xlsx"),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> StreamingResponse:
    today = today_ist()
    lots = taxed_lots(build_positions(await fetch_ledger(conn, user_id), today))
    years = years_with_sales(lots)
    fy = fy or (years[0] if years else financial_year(today))
    in_year, s = year_report(lots, fy)
    start, end = fy_bounds(fy)

    rows = [
        [
            t.security.symbol or t.security.name, t.security.isin, t.acquired, t.sold, t.match.holding_days,
            t.match.quantity, t.match.cost, t.match.proceeds, t.match.gain,
            "Long" if t.tax.term == "LONG" else "Short", t.tax.rule,
            t.tax.rate if t.tax.rate is not None else "Slab",
        ]
        for t in in_year
    ]
    total_gain = sum((t.match.gain for t in in_year), _ZERO)
    st_net = sum((b.net for b in s.buckets if b.term == "SHORT"), _ZERO)
    lt_net = sum((b.net for b in s.buckets if b.term == "LONG"), _ZERO)
    lot_notes = sorted({n for t in in_year for n in t.tax.notes})

    st = Statement(
        title="Realised P/L and capital gains",
        subtitle=f"Financial year {fy} ({start:%d %b %Y} to {end:%d %b %Y}), {_generated()}",
        owner=await _owner(conn, user_id),
        summary=[
            ("Total realised gain", inr(total_gain, sign=True)),
            ("Short-term (net)", inr(st_net, sign=True)),
            ("Long-term (net)", inr(lt_net, sign=True)),
            ("s.112A exemption used", f"{inr(s.exemption_used)} of {inr(s.exemption_limit)}"),
            ("Estimated tax (incl. 4% cess)", inr(s.special_rate_tax + s.cess)),
            ("Gains taxed at your slab", inr(s.slab_income)),
            ("Short-term loss carried", inr(s.short_term_loss_carried)),
            ("Long-term loss carried", inr(s.long_term_loss_carried)),
        ],
        tables=[
            Table(
                "Sales matched to purchases (FIFO)",
                [
                    Column("Security", width=20), Column("ISIN", width=14), Column("Bought", "date", 11),
                    Column("Sold", "date", 11), Column("Days", "int", 7), Column("Quantity", "qty", 10),
                    Column("Cost", "inr", 13), Column("Proceeds", "inr", 13), Column("Gain", "inr", 13),
                    Column("Term", width=7), Column("Rule", width=24), Column("Rate %", width=8),
                ],
                rows,
                totals=["Total", "", None, None, None, None, sum((t.match.cost for t in in_year), _ZERO),
                        sum((t.match.proceeds for t in in_year), _ZERO), total_gain, "", "", ""] if in_year else None,
            ),
            Table(
                "Tax computation",
                [
                    Column("Category", width=26), Column("Gains", "inr", 13), Column("Losses", "inr", 13),
                    Column("Losses set off", "inr", 13), Column("Exemption", "inr", 13), Column("Taxable", "inr", 13),
                    Column("Tax", "inr", 13),
                ],
                [[b.label, b.gains, b.losses, b.set_off, b.exemption, b.taxable, b.tax if b.tax is not None else "Slab"] for b in s.buckets],
                totals=["Total (before cess)", None, None, None, s.exemption_used, None, s.special_rate_tax] if s.buckets else None,
            ),
        ],
        notes=(["No sales in this financial year."] if not in_year else [])
        + s.notes
        + lot_notes
        + [f"Tax rules as published by the Income Tax Department, last checked {RULES_VERIFIED_ON:%d %b %Y}."],
        filename=f"capital-gains-FY{fy}",
    )
    return _stream(st, format)
