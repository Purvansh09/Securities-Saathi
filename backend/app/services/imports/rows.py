"""Turn one mapped spreadsheet row into a staged trade plus a list of problems. Pure.

Nothing here writes to the ledger (rule 7): a staged row only becomes a transaction when the
user has reviewed it and committed the import. Problems are written in plain words because
they're shown to the user as-is on the review screen.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))
# Exports often give only the trade date. Market close is a neutral, honest default: it keeps the
# trade on the right IST day, and same-day ordering is handled by the ledger (BUY before SELL).
DEFAULT_TRADE_TIME = time(15, 30)
_SCALE = Decimal("0.000001")  # numeric(20,6), the ledger's scale

_BUY = {"buy", "b", "bought", "purchase", "purchased", "sip", "switchin", "bonus", "allotment"}
_SELL = {"sell", "s", "sold", "sale", "sales", "redeem", "redemption", "redeemed", "switchout"}
_EXECUTED = {"executed", "complete", "completed", "traded", "success", "successful", "filled", "done"}
_DERIVATIVES = re.compile(r"\b(fo|f&o|fut|futures|opt|options|optidx|optstk|futidx|futstk|cds|currency|mcx|com)\b")

_DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y %I:%M %p", "%d-%m-%Y %I:%M:%S %p",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y %I:%M %p",
    "%d %b %Y %I:%M %p", "%d %b %Y, %I:%M %p", "%d-%b-%Y %H:%M:%S", "%d %b %Y %H:%M",
]
_DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y", "%d/%m/%y", "%b %d, %Y", "%d.%m.%Y"]
_TIME_FORMATS = ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"]


@dataclass
class StagedRow:
    row_number: int
    raw: dict[str, str]
    isin: str | None = None
    symbol: str | None = None
    name: str | None = None
    txn_type: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    charges: Decimal = Decimal(0)
    executed_at: datetime | None = None
    issues: list[str] = field(default_factory=list)
    rejected_reason: str | None = None  # set when the row isn't a trade we import at all


def parse_number(value: str) -> Decimal | None:
    """'1,23,456.50', '₹ 1450', '(250.00)' -> Decimal. Indian digit grouping is just commas."""
    s = value.strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("INR", "").strip()
    if not s or s in {"-", "--", "NA", "N.A.", "nan"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return -d if negative else d


def _excel_serial(value: str) -> datetime | None:
    """Excel stores dates as days since 1899-12-30; some exports leak the raw number."""
    try:
        serial = float(value)
    except ValueError:
        return None
    if not 20000 < serial < 80000:  # roughly 1954 to 2119; anything else isn't a date
        return None
    return datetime(1899, 12, 30) + timedelta(days=serial)


def parse_datetime(date_value: str, time_value: str = "") -> tuple[datetime | None, bool]:
    """(naive IST datetime, whether a time of day was found). Day-first throughout, because
    that's how Indian brokers write dates; ISO dates are unambiguous either way."""
    d = date_value.strip()
    # pandas renders Excel dates as "2026-05-10 00:00:00"; a bare midnight means "no time given".
    d = re.sub(r"\s+00:00:00$", "", d)
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(d, fmt), True
        except ValueError:
            pass
    parsed: datetime | None = None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(d, fmt)
            break
        except ValueError:
            pass
    if parsed is None:
        parsed = _excel_serial(d)
    if parsed is None:
        return None, False
    t = time_value.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.combine(parsed.date(), datetime.strptime(t, fmt).time()), True
        except ValueError:
            pass
    return parsed, False


def parse_row(row_number: int, values: dict[str, str], raw: dict[str, str], today: date) -> StagedRow:
    r = StagedRow(row_number=row_number, raw=raw)
    get = lambda k: (values.get(k) or "").strip()  # noqa: E731

    # Rows that aren't completed cash-market trades are excluded outright, with the reason shown.
    status = get("status")
    if status and status.lower() not in _EXECUTED:
        r.rejected_reason = f"Order not executed (status: {status})"
    segment = get("segment")
    if segment and _DERIVATIVES.search(segment.lower()):
        r.rejected_reason = f"Derivatives aren't supported (segment: {segment})"

    r.isin = get("isin").upper() or None
    r.symbol = get("symbol").upper() or None
    r.name = get("name") or None
    if not (r.isin or r.symbol or r.name):
        r.issues.append("No ISIN, symbol or name: choose the security")

    side = re.sub(r"[^a-z]", "", get("side").lower())
    if side in _BUY:
        r.txn_type = "BUY"
    elif side in _SELL:
        r.txn_type = "SELL"
    else:
        r.issues.append(f"Can't tell buy or sell from “{get('side')}”" if get("side") else "Missing buy/sell")

    qty = parse_number(get("quantity"))
    if qty is None or qty == 0:
        r.issues.append("Missing or zero quantity")
    else:
        r.quantity = abs(qty).quantize(_SCALE, rounding=ROUND_HALF_EVEN)  # some exports sign sells negative

    price = parse_number(get("price"))
    if price is None and r.quantity:
        value = parse_number(get("value"))
        if value is not None:
            price = abs(value) / r.quantity  # Groww gives total value, not price per unit
    if price is None or price < 0:
        r.issues.append("Missing price")
    else:
        r.price = abs(price).quantize(_SCALE, rounding=ROUND_HALF_EVEN)

    charges = parse_number(get("charges"))
    if charges is not None:
        r.charges = abs(charges).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

    when, _has_time = parse_datetime(get("datetime") or get("trade_date"), get("trade_time"))
    if when is None:
        shown = get("datetime") or get("trade_date")
        r.issues.append(f"Can't read the date “{shown}”" if shown else "Missing trade date")
    else:
        if not _has_time:
            when = datetime.combine(when.date(), DEFAULT_TRADE_TIME)
        r.executed_at = when.replace(tzinfo=IST)  # stored in UTC by Postgres (rule 11)
        if when.date() > today:
            r.issues.append("Trade date is in the future")

    return r


def is_blank_or_total(values: dict[str, str]) -> bool:
    """Footer rows such as 'Total' or an empty line under the table aren't trades."""
    filled = [v for v in values.values() if v.strip()]
    if not filled:
        return True
    return any(v.strip().lower() in {"total", "grand total", "net total"} for v in values.values())
