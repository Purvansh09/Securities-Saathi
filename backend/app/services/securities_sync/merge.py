"""Pure merge rules: combine the parsed sources into one row per ISIN, and one price per
(ISIN, date). Kept free of I/O so the precedence rules can be unit-tested directly."""

from datetime import date

from app.services.securities_sync.sources import (
    BhavRow,
    ParsedPrice,
    ParsedSecurity,
    SourceFormatError,
    is_equity_share_isin,
)

# Floors well below today's real sizes (~2,300 NSE equities, ~300 ETFs, ~15,000 MF ISINs).
# If a download comes back truncated, we stop before writing, instead of treating thousands
# of missing securities as delisted and deactivating them.
MIN_ROWS = {"NSE equity list": 1000, "NSE ETF list": 50, "AMFI NAVAll": 5000}


def check_minimums(counts: dict[str, int]) -> None:
    short = {name: n for name, n in counts.items() if n < MIN_ROWS.get(name, 0)}
    if short:
        detail = ", ".join(f"{name} has {n} rows (expected at least {MIN_ROWS[name]})" for name, n in short.items())
        raise SourceFormatError(f"Refusing to sync from suspiciously small sources: {detail}")


def merge_securities(
    nse_equity: list[ParsedSecurity],
    nse_etfs: list[ParsedSecurity],
    amfi: list[ParsedSecurity],
    bse_bhav: list[BhavRow] | None = None,
    sectors: dict[str, str] | None = None,
) -> list[ParsedSecurity]:
    """One row per ISIN (CLAUDE.md rule 3). Precedence: NSE lists, then AMFI, then BSE.
    A stock on both exchanges stays one row, with both symbols in metadata."""
    merged: dict[str, ParsedSecurity] = {}

    for s in nse_equity + nse_etfs:
        merged.setdefault(s.isin, s)

    for s in amfi:
        existing = merged.get(s.isin)
        if existing is None:
            merged[s.isin] = s
        else:
            # An ETF is also an AMFI scheme. Keep it as an exchange-traded ETF, but record its
            # scheme code and AMC, which the ETF list doesn't carry.
            scheme_info = {k: s.metadata[k] for k in ("scheme_code", "amc", "category") if k in s.metadata}
            existing.metadata = {**scheme_info, **existing.metadata}

    for r in bse_bhav or []:
        bse_meta = {"bse_code": r.instrument_id, "bse_symbol": r.symbol}
        existing = merged.get(r.isin)
        if existing is not None:
            existing.metadata.update(bse_meta)
        elif is_equity_share_isin(r.isin):
            # Listed only on BSE. The bhavcopy only lists what traded that day, so these rows are
            # added and refreshed but never deactivated by absence (see store.upsert_securities).
            merged[r.isin] = ParsedSecurity(
                r.isin, r.name, "STOCK", r.symbol, "BSE", metadata={**bse_meta, "bse_series": r.series}
            )

    for isin, industry in (sectors or {}).items():
        s = merged.get(isin)
        if s is not None and s.type == "STOCK":
            s.sector = industry

    return list(merged.values())


def pick_prices(
    nse_bhav: list[BhavRow] | None, bse_bhav: list[BhavRow] | None, amfi: list[ParsedPrice] | None
) -> list[ParsedPrice]:
    """One price per (ISIN, date). NSE close wins over BSE (NSE carries most of the volume);
    AMFI NAV fills in mutual funds. Within NSE, the EQ series is preferred when a security
    trades in more than one series that day."""
    chosen: dict[tuple[str, date], ParsedPrice] = {}
    nse_series: dict[tuple[str, date], str] = {}

    for r in nse_bhav or []:
        key = (r.isin, r.trade_date)
        if key not in chosen or (r.series == "EQ" and nse_series[key] != "EQ"):
            chosen[key] = ParsedPrice(r.isin, r.trade_date, r.close, "NSE")
            nse_series[key] = r.series
    for r in bse_bhav or []:
        chosen.setdefault((r.isin, r.trade_date), ParsedPrice(r.isin, r.trade_date, r.close, "BSE"))
    for p in amfi or []:
        chosen.setdefault((p.isin, p.price_date), p)

    return list(chosen.values())
