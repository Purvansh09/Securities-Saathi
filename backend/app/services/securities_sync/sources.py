"""Pure parsers for the public files the daily sync reads. No network, no DB: text in, rows out.

Every parser checks that the columns it relies on are present and raises SourceFormatError if
not (CLAUDE.md: "make the sync fail loudly if expected columns disappear"). Publishers change
these files without notice -- AMFI added separate Plan/Option columns to NAVAll.txt, and BSE's
scrip-list API is now behind bot protection -- so a loud failure is far better than silently
writing half-parsed data into the securities master.

Formats verified against the live files on 2026-09-29.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")  # same check as the DB constraint


class SourceFormatError(Exception):
    """A source file no longer looks the way this code expects. Stop the sync, don't guess."""


@dataclass
class ParsedSecurity:
    isin: str
    name: str
    type: str  # STOCK | ETF | MF | BOND
    symbol: str | None
    exchange: str  # NSE | BSE | AMFI
    sector: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedPrice:
    isin: str
    price_date: date
    price: Decimal
    source: str  # NSE | BSE | AMFI


def _csv_rows(text: str, required: set[str], source: str) -> list[dict[str, str]]:
    """Read a CSV with a header row; header names are stripped (NSE pads some with spaces)."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise SourceFormatError(f"{source}: file is empty") from None
    missing = required - set(header)
    if missing:
        raise SourceFormatError(f"{source}: expected columns missing: {sorted(missing)} (got {header})")
    return [
        {k: (v or "").strip() for k, v in zip(header, row)}
        for row in reader
        if row and any(cell.strip() for cell in row)
    ]


def _valid_isin(value: str) -> bool:
    return bool(ISIN_RE.fullmatch(value))


def _decimal(value: str) -> Decimal | None:
    try:
        d = Decimal(value.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d >= 0 else None


# --- NSE ------------------------------------------------------------------------------------


def parse_nse_equity_list(text: str) -> list[ParsedSecurity]:
    """EQUITY_L.csv: every equity listed on NSE (all series: EQ, BE, BZ, SME...)."""
    rows = _csv_rows(text, {"SYMBOL", "NAME OF COMPANY", "SERIES", "ISIN NUMBER"}, "NSE EQUITY_L")
    out = []
    for r in rows:
        if not _valid_isin(r["ISIN NUMBER"]):
            continue
        out.append(
            ParsedSecurity(
                isin=r["ISIN NUMBER"],
                name=r["NAME OF COMPANY"],
                type="STOCK",
                symbol=r["SYMBOL"],
                exchange="NSE",
                metadata={"nse_symbol": r["SYMBOL"], "nse_series": r["SERIES"]},
            )
        )
    return out


def parse_nse_etf_list(text: str) -> list[ParsedSecurity]:
    """eq_etfseclist.csv. `SecurityName` is an abbreviated code (e.g. NIPINDETFNIFTYBEES), so the
    display name comes from `Underlying Asset` when that already reads like a fund name."""
    required = {"Symbol", "Underlying Asset", "SecurityName", "ISINNumber"}
    rows = _csv_rows(text, required, "NSE ETF list")
    out = []
    for r in rows:
        if not _valid_isin(r["ISINNumber"]):
            continue
        underlying = r["Underlying Asset"]
        looks_like_name = re.search(r"\b(ETF|BeES|Fund)\b", underlying, re.IGNORECASE)
        name = underlying if looks_like_name else f"{r['Symbol']} ETF ({underlying or r['SecurityName']})"
        meta = {"nse_symbol": r["Symbol"], "underlying": underlying}
        if r.get("ETF Underlying"):
            meta["underlying_class"] = r["ETF Underlying"]
        out.append(ParsedSecurity(r["ISINNumber"], name, "ETF", r["Symbol"], "NSE", metadata=meta))
    return out


def parse_nifty500_sectors(text: str) -> dict[str, str]:
    """ind_nifty500list.csv -> {ISIN: industry}. The BSE scrip list (CLAUDE.md's original sector
    source) is blocked by BSE's bot protection; this NSE index file covers the 500 largest
    companies, which is most of what retail investors hold. Others stay 'Unclassified'."""
    rows = _csv_rows(text, {"Industry", "ISIN Code"}, "NSE Nifty 500 list")
    return {r["ISIN Code"]: r["Industry"] for r in rows if _valid_isin(r["ISIN Code"]) and r["Industry"]}


# --- Bhavcopy (NSE and BSE share the UDiFF format) -------------------------------------------

_BHAV_REQUIRED = {"TradDt", "FinInstrmTp", "ISIN", "TckrSymb", "SctySrs", "FinInstrmId", "FinInstrmNm", "ClsPric"}


@dataclass
class BhavRow:
    isin: str
    trade_date: date
    symbol: str
    series: str
    instrument_id: str
    name: str
    close: Decimal


def parse_bhavcopy(text: str, exchange: str) -> list[BhavRow]:
    """The exchange's daily closing-price file (UDiFF format, used by both NSE and BSE since 2024)."""
    rows = _csv_rows(text, _BHAV_REQUIRED, f"{exchange} bhavcopy")
    out = []
    for r in rows:
        close = _decimal(r["ClsPric"])
        if not _valid_isin(r["ISIN"]) or close is None:
            continue
        try:
            traded = date.fromisoformat(r["TradDt"])
        except ValueError:
            raise SourceFormatError(f"{exchange} bhavcopy: unreadable TradDt {r['TradDt']!r}") from None
        out.append(BhavRow(r["ISIN"], traded, r["TckrSymb"], r["SctySrs"], r["FinInstrmId"], r["FinInstrmNm"], close))
    return out


def is_equity_share_isin(isin: str) -> bool:
    """Indian ISINs encode the instrument: INE + 4-char issuer + 2-digit security type. Type 01 is
    equity shares; 07/08 etc. are debentures and bonds. Used to keep BSE's bond and NCD rows out
    of the STOCK list when adding BSE-only securities."""
    return isin.startswith("INE") and isin[7:9] == "01"


# --- AMFI -----------------------------------------------------------------------------------

_AMFI_COLUMNS = {
    "code": "Scheme Code",
    "isin_growth": "ISIN Div Payout/ ISIN Growth",
    "isin_reinvest": "ISIN Div Reinvestment",
    "name": "Scheme Name",
    "nav": "Net Asset Value",
    "date": "Date",
}
_AMFI_SECTION_RE = re.compile(r"^(Open Ended|Close Ended|Interval Fund) Schemes\s*\((.*)\)\s*$", re.IGNORECASE)


def _strip_suffix(value: str, suffix: str) -> str:
    return value[: -len(suffix)].strip() if value.lower().endswith(suffix.lower()) else value


def parse_amfi_navall(text: str) -> tuple[list[ParsedSecurity], list[ParsedPrice]]:
    """NAVAll.txt: every mutual fund scheme with its latest NAV.

    Layout: a ';'-separated header, then blocks. A line with no ';' is either a section heading
    ("Open Ended Schemes(Equity Scheme - Large Cap Fund)") or the AMC name for the rows under it.
    Each ISIN on a row is its own security (CLAUDE.md: growth and IDCW options differ)."""
    lines = [ln.strip() for ln in text.lstrip("﻿").splitlines()]
    if not lines:
        raise SourceFormatError("AMFI NAVAll: file is empty")
    header = [h.strip() for h in lines[0].split(";")]
    missing = [c for c in _AMFI_COLUMNS.values() if c not in header]
    if missing:
        raise SourceFormatError(f"AMFI NAVAll: expected columns missing: {missing} (got {header})")
    idx = {key: header.index(col) for key, col in _AMFI_COLUMNS.items()}
    plan_idx = header.index("Plan") if "Plan" in header else None
    option_idx = header.index("Option") if "Option" in header else None

    securities: dict[str, ParsedSecurity] = {}
    prices: list[ParsedPrice] = []
    structure = category = amc = None
    malformed = data_rows = 0

    for line in lines[1:]:
        if not line:
            continue
        if ";" not in line:
            section = _AMFI_SECTION_RE.match(line)
            if section:
                structure, category = section.group(1).title(), section.group(2).strip()
            else:
                amc = line
            continue

        cells = [c.strip() for c in line.split(";")]
        if len(cells) != len(header):
            malformed += 1
            continue
        data_rows += 1

        scheme = cells[idx["name"]]
        plan = _strip_suffix(cells[plan_idx], " Plan") if plan_idx is not None else ""
        option = _strip_suffix(cells[option_idx], " Option") if option_idx is not None else ""
        valid = [i for i in (cells[idx["isin_growth"]], cells[idx["isin_reinvest"]]) if _valid_isin(i)]
        both = len(valid) == 2

        # Build "Scheme - Direct Plan - Growth", matching the seed data's naming, without
        # repeating plan/option text that older rows already carry inside the scheme name.
        base = scheme
        if plan and plan.lower() not in scheme.lower():
            base += f" - {plan} Plan"
        if option and option.lower() not in scheme.lower():
            base += f" - {option}"

        nav = _decimal(cells[idx["nav"]])
        try:
            nav_date = datetime.strptime(cells[idx["date"]], "%d-%b-%Y").date()
        except ValueError:
            nav_date = None  # "N.A." rows: keep the security, skip the price

        for pos, isin in enumerate(valid):
            if isin in securities:
                continue  # first occurrence wins; AMFI occasionally repeats an ISIN
            mode = ("Payout", "Reinvestment")[pos] if both else None
            meta = {"scheme_code": cells[idx["code"]], "amc": amc, "category": category, "structure": structure}
            if plan:
                meta["plan"] = plan
            if option:
                meta["option"] = option
            if mode:
                meta["idcw_mode"] = mode
            securities[isin] = ParsedSecurity(
                isin=isin,
                name=f"{base} - {mode}" if mode else base,
                type="MF",
                symbol=None,
                exchange="AMFI",
                metadata={k: v for k, v in meta.items() if v},
            )
            if nav is not None and nav_date is not None:
                prices.append(ParsedPrice(isin, nav_date, nav, "AMFI"))

    # A handful of odd lines is normal; a lot means the layout changed under us.
    if data_rows and malformed > data_rows * 0.05:
        raise SourceFormatError(f"AMFI NAVAll: {malformed} of {malformed + data_rows} rows have the wrong column count")
    return list(securities.values()), prices
