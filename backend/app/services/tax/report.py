"""Glue between the portfolio (positions with FIFO matches) and the tax engine. Used by the tax API
and by the realised P/L statement, so both show the same classification for every sale."""

from dataclasses import dataclass
from datetime import date

from app.services.fifo import IST, LotMatch
from app.services.tax.engine import AssetClass, LotTax, YearSummary, classify_lot, classify_security, financial_year, summarize_year
from app.services.valuation import Position, SecurityRef


@dataclass(frozen=True)
class TaxedLot:
    security: SecurityRef
    asset: AssetClass
    match: LotMatch
    acquired: date  # IST calendar date
    sold: date
    fy: str
    tax: LotTax


def taxed_lots(positions: list[Position]) -> list[TaxedLot]:
    out = []
    for p in positions:
        sec = p.security
        asset = classify_security(sec.type, sec.name, sec.metadata, sec.exchange)
        for m in p.fifo.matches:
            acquired = m.acquired_at.astimezone(IST).date()
            sold = m.sold_at.astimezone(IST).date()
            out.append(TaxedLot(sec, asset, m, acquired, sold, financial_year(sold), classify_lot(asset, acquired, sold)))
    out.sort(key=lambda t: (t.match.sold_at, t.security.isin))
    return out


def years_with_sales(lots: list[TaxedLot]) -> list[str]:
    return sorted({t.fy for t in lots}, reverse=True)


def year_report(lots: list[TaxedLot], fy: str) -> tuple[list[TaxedLot], YearSummary]:
    in_year = [t for t in lots if t.fy == fy]
    return in_year, summarize_year(fy, [(t.tax, t.match.gain) for t in in_year])
