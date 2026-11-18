"""Read an uploaded broker file into a grid of strings, find its header row, and map its
columns onto our canonical trade fields. Pure: bytes in, mapping out.

Rule-based first (CLAUDE.md rule 7). Broker exports differ mainly in what they call each column
("Trade Type" vs "Side" vs "Buy/Sell"), so the rules are one alias table rather than a parser per
broker. Profiles only label which broker a file looks like, for the review screen. A file the
aliases can't map goes to the LLM fallback (llm.py), which returns the same ColumnMapping shape.
"""

import csv
import io
import re
from dataclasses import dataclass, field

import pandas as pd

MAX_ROWS = 5000  # a decade of active trading; anything larger is probably the wrong file
HEADER_SCAN_ROWS = 40  # Zerodha's tradebook has ~15 lines of account details above the table


class ImportFileError(Exception):
    """The file can't be read as a trade table at all (wrong type, empty, corrupt)."""


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


# Canonical field -> header spellings seen in Indian broker exports, normalised by _norm().
# Order inside each list doesn't matter; the first matching column wins left to right.
ALIASES: dict[str, set[str]] = {
    "isin": {"isin", "isincode", "isinnumber", "isinno"},
    "symbol": {"symbol", "tradingsymbol", "ticker", "scrip", "scripcode", "scripsymbol", "nsesymbol"},
    "name": {"stockname", "companyname", "company", "scripname", "securityname", "schemename", "name",
             "instrumentname", "instrument", "security"},
    "datetime": {"orderexecutiontime", "executiondateandtime", "executiontime", "tradedatetime", "datetime",
                 "timestamp", "orderdatetime"},
    "trade_date": {"tradedate", "date", "orderdate", "transactiondate", "executiondate", "txndate"},
    "trade_time": {"tradetime", "time", "ordertime"},
    "side": {"tradetype", "type", "side", "buysell", "transactiontype", "action", "txntype", "bs"},
    "quantity": {"quantity", "qty", "units", "tradedquantity", "filledqty", "tradedqty", "shares"},
    "price": {"price", "tradeprice", "rate", "avgprice", "averageprice", "executionprice", "tradedprice", "nav",
              "marketrate"},
    "value": {"value", "amount", "tradevalue", "netamount", "totalamount", "ordervalue", "total"},
    "charges": {"charges", "totalcharges", "brokerage", "fees"},
    "status": {"status", "orderstatus"},
    "segment": {"segment", "instrumenttype"},
}

# Which broker a mapped header most looks like. Only a label for the review screen.
PROFILES: list[tuple[str, set[str]]] = [
    ("Zerodha tradebook", {"tradeid", "orderexecutiontime", "isin"}),
    ("Groww orders", {"executiondateandtime", "exchangeorderid"}),
    ("Upstox trades", {"tradenum", "scripcode"}),
    ("Angel One trades", {"tradeno", "buysell"}),
]


@dataclass
class ColumnMapping:
    """Canonical field -> column index in the header row."""

    header_row: int
    columns: dict[str, int] = field(default_factory=dict)
    profile: str = "Generic"

    def missing_required(self) -> list[str]:
        c = self.columns
        missing = []
        if not ({"isin", "symbol", "name"} & c.keys()):
            missing.append("ISIN, symbol or security name")
        if not ({"trade_date", "datetime"} & c.keys()):
            missing.append("trade date")
        if "side" not in c:
            missing.append("buy/sell")
        if "quantity" not in c:
            missing.append("quantity")
        if not ({"price", "value"} & c.keys()):
            missing.append("price or trade value")
        return missing


def read_grid(content: bytes, filename: str) -> list[list[str]]:
    """Every cell as a stripped string (blank for empty). Dates and numbers stay as text here;
    rows.py parses them deliberately, rather than trusting spreadsheet type guesses."""
    name = filename.lower()
    try:
        if name.endswith(".csv"):
            text = content.decode("utf-8-sig", errors="replace")
            grid = list(csv.reader(io.StringIO(text)))
        elif name.endswith(".xlsx"):
            frame = pd.read_excel(io.BytesIO(content), header=None, dtype=str, engine="openpyxl")
            grid = frame.fillna("").values.tolist()
        else:
            raise ImportFileError("Upload a .csv or .xlsx file. For an old .xls file, open it and save as .xlsx.")
    except ImportFileError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any reader failure means "not a readable spreadsheet"
        raise ImportFileError(f"Couldn't read this file as a spreadsheet ({type(exc).__name__}).") from exc

    grid = [[str(cell).strip() for cell in row] for row in grid]
    grid = [row for row in grid if any(row)]
    if not grid:
        raise ImportFileError("The file is empty.")
    if len(grid) > MAX_ROWS + HEADER_SCAN_ROWS:
        raise ImportFileError(f"The file has more than {MAX_ROWS} rows. Split it by year and upload each part.")
    return grid


def _map_header(row: list[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for idx, cell in enumerate(row):
        key = _norm(cell)
        if not key:
            continue
        for fld, aliases in ALIASES.items():
            if key in aliases and fld not in columns:
                columns[fld] = idx
                break
    return columns


def detect_mapping(grid: list[list[str]]) -> ColumnMapping | None:
    """The header row is the row, among the first few dozen, whose cells match the most aliases.
    Returns None when even the best candidate lacks a required field."""
    best: ColumnMapping | None = None
    for i, row in enumerate(grid[:HEADER_SCAN_ROWS]):
        columns = _map_header(row)
        if best is None or len(columns) > len(best.columns):
            best = ColumnMapping(header_row=i, columns=columns)
    if best is None or best.missing_required():
        return None

    normalised = {_norm(c) for c in grid[best.header_row]}
    best.profile = next((label for label, keys in PROFILES if keys <= normalised), "Generic")
    return best


def mapping_from_names(grid: list[list[str]], header_row: int, names: dict[str, str | None]) -> ColumnMapping | None:
    """Build a mapping from field -> column *name* (what the LLM returns), checking every name
    really is a column in that header row. Anything invented is dropped, never trusted."""
    if not 0 <= header_row < len(grid):
        return None
    header = grid[header_row]
    columns = {}
    for fld, col_name in names.items():
        if fld in ALIASES and col_name and col_name in header:
            columns[fld] = header.index(col_name)
    mapping = ColumnMapping(header_row=header_row, columns=columns, profile="Mapped by AI")
    return None if mapping.missing_required() else mapping


def data_rows(grid: list[list[str]], mapping: ColumnMapping) -> list[tuple[int, dict[str, str], dict[str, str]]]:
    """(1-based row number in the file, {field: value}, {original header: value}) for every row
    below the header. The raw dict is what's kept in import_rows.raw for the review screen."""
    header = grid[mapping.header_row]
    out = []
    for offset, row in enumerate(grid[mapping.header_row + 1:], start=mapping.header_row + 2):
        cells = row + [""] * (len(header) - len(row))
        values = {fld: cells[idx] for fld, idx in mapping.columns.items() if idx < len(cells)}
        raw = {h or f"Column {j + 1}": cells[j] for j, h in enumerate(header) if j < len(cells)}
        out.append((offset, values, raw))
    return out
