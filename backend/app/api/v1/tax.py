"""GET /tax/capital-gains?fy=2026-27 -- capital gains for one financial year, classified by the rule
engine in services/tax (rule 8). Read-only; everything is derived from the ledger (rule 1)."""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.api.v1.portfolio import _s, build_positions, fetch_ledger, today_ist
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.tax import TaxBucketOut, TaxLotOut, TaxReportOut, TaxSummaryOut
from app.services.tax.engine import financial_year, fy_bounds
from app.services.tax.report import TaxedLot, taxed_lots, year_report, years_with_sales
from app.services.tax.rules import RULES_VERIFIED_ON, SOURCES

router = APIRouter(prefix="/tax", tags=["tax"])


def _rate(r) -> str | None:
    return None if r is None else f"{r.normalize():f}"


def lot_out(t: TaxedLot) -> TaxLotOut:
    m = t.match
    return TaxLotOut(
        isin=t.security.isin, symbol=t.security.symbol, name=t.security.name, type=t.security.type,
        asset_basis=t.asset.basis, classification_certain=t.asset.certain,
        acquired_on=t.acquired, sold_on=t.sold, holding_days=m.holding_days,
        quantity=_s(m.quantity), cost=_s(m.cost), proceeds=_s(m.proceeds), gain=_s(m.gain),
        term=t.tax.term, rule=t.tax.rule, rate_pct=_rate(t.tax.rate), notes=list(t.tax.notes),
    )


@router.get("/capital-gains", response_model=TaxReportOut)
async def capital_gains(
    fy: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$", description="Financial year, e.g. 2026-27"),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> TaxReportOut:
    today = today_ist()
    lots = taxed_lots(build_positions(await fetch_ledger(conn, user_id), today))
    years = years_with_sales(lots)
    fy = fy or (years[0] if years else financial_year(today))
    in_year, s = year_report(lots, fy)
    start, end = fy_bounds(fy)

    return TaxReportOut(
        fy=fy, period_from=start, period_to=end, years_available=years,
        rules_verified_on=RULES_VERIFIED_ON, sources=SOURCES,
        lots=[lot_out(t) for t in in_year],
        buckets=[
            TaxBucketOut(
                label=b.label, term=b.term, category=b.category, rate_pct=_rate(b.rate),
                gains=_s(b.gains), losses=_s(b.losses), net=_s(b.net), set_off=_s(b.set_off),
                exemption=_s(b.exemption), taxable=_s(b.taxable), tax=_s(b.tax) if b.tax is not None else None,
            )
            for b in s.buckets
        ],
        summary=TaxSummaryOut(
            exemption_limit=_s(s.exemption_limit), exemption_used=_s(s.exemption_used),
            special_rate_tax=_s(s.special_rate_tax), cess=_s(s.cess),
            total_estimated_tax=_s(s.special_rate_tax + s.cess), slab_income=_s(s.slab_income),
            short_term_loss_carried=_s(s.short_term_loss_carried), long_term_loss_carried=_s(s.long_term_loss_carried),
            notes=s.notes,
        ),
    )
