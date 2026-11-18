"""Import parsing: header detection, column mapping and row parsing, with no DB or network.
Sample files mimic each broker's export layout (header names, preambles, value-vs-price)."""

import io
from datetime import date, datetime
from decimal import Decimal as D

import openpyxl
import pytest

from app.services.imports.rows import IST, is_blank_or_total, parse_datetime, parse_number, parse_row
from app.services.imports.table import ImportFileError, data_rows, detect_mapping, mapping_from_names, read_grid

TODAY = date(2026, 10, 6)

ZERODHA_CSV = """Client ID,AB1234
Tradebook for Equity from 2026-04-01 to 2026-09-30
,
Symbol,ISIN,Trade Date,Exchange,Segment,Series,Trade Type,Auction,Quantity,Price,Trade ID,Order ID,Order Execution Time
RELIANCE,INE002A01018,2026-05-10,NSE,EQ,EQ,buy,false,10,1450.5,1001,2001,2026-05-10T10:15:30
INFY,INE009A01021,2026-06-02,BSE,EQ,A,sell,false,5,1600,1002,2002,2026-06-02T14:01:00
NIFTY26JUNFUT,,2026-06-03,NFO,FO,,buy,false,50,22000,1003,2003,2026-06-03T09:20:00
"""

GROWW_CSV = """Stock name,Symbol,ISIN,Type,Quantity,Value,Exchange,Exchange Order Id,Execution date and time,Order status
Tata Consultancy Services,TCS,INE467B01029,BUY,4,"15,200.00",NSE,1300000001,10-05-2026 10:15 AM,Executed
Tata Consultancy Services,TCS,INE467B01029,SELL,1,3900,NSE,1300000002,11-05-2026 02:30 PM,Cancelled
"""

UPSTOX_CSV = """Date,Company,Amount,Exchange,Segment,Scrip Code,Instrument Type,Trade Num,Trade Time,Side,Quantity,Price
12-06-2026,HDFC BANK LTD,"8,000.00",NSE,EQ,HDFCBANK,EQUITY,55501,11:45:10,Buy,5,1600
13-06-2026,HDFC BANK LTD,1650,NSE,EQ,HDFCBANK,EQUITY,55502,,Sell,1,1650
Total,,9650,,,,,,,,,
"""


def _parse(text: str, filename: str = "file.csv"):
    grid = read_grid(text.encode(), filename)
    mapping = detect_mapping(grid)
    assert mapping is not None
    rows = [parse_row(n, v, raw, TODAY) for n, v, raw in data_rows(grid, mapping) if not is_blank_or_total(v)]
    return mapping, rows


def test_zerodha_preamble_skipped_and_rows_parsed():
    mapping, rows = _parse(ZERODHA_CSV)
    assert mapping.header_row == 2 and mapping.profile == "Zerodha tradebook"
    rel, infy, fut = rows
    assert (rel.isin, rel.txn_type, rel.quantity, rel.price) == ("INE002A01018", "BUY", D("10"), D("1450.5"))
    assert rel.executed_at == datetime(2026, 5, 10, 10, 15, 30, tzinfo=IST)  # execution time beats trade date
    assert rel.issues == [] and rel.rejected_reason is None
    assert infy.txn_type == "SELL"
    assert fut.rejected_reason.startswith("Derivatives aren't supported")


def test_groww_value_becomes_price_and_cancelled_is_excluded():
    mapping, (buy, cancelled) = _parse(GROWW_CSV)
    assert mapping.profile == "Groww orders"
    assert buy.price == D("3800")  # 15,200 / 4
    assert buy.executed_at == datetime(2026, 5, 10, 10, 15, tzinfo=IST)  # day-first
    assert cancelled.rejected_reason == "Order not executed (status: Cancelled)"


def test_upstox_separate_time_column_default_close_and_total_row():
    _, rows = _parse(UPSTOX_CSV)
    assert len(rows) == 2  # the "Total" footer isn't a trade
    buy, sell = rows
    assert buy.symbol == "HDFCBANK" and buy.executed_at == datetime(2026, 6, 12, 11, 45, 10, tzinfo=IST)
    # No time given: market close on the trade day, so it stays on the right IST date.
    assert sell.executed_at == datetime(2026, 6, 13, 15, 30, tzinfo=IST)


def test_xlsx_is_read_like_csv():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Account statement"])
    ws.append(["Symbol", "ISIN", "Trade Date", "Trade Type", "Quantity", "Price"])
    ws.append(["RELIANCE", "INE002A01018", datetime(2026, 5, 10), "BUY", 10, 1450.5])
    buf = io.BytesIO()
    wb.save(buf)
    grid = read_grid(buf.getvalue(), "trades.xlsx")
    mapping = detect_mapping(grid)
    (n, values, raw), = data_rows(grid, mapping)
    row = parse_row(n, values, raw, TODAY)
    assert row.issues == [] and row.price == D("1450.5")
    assert row.executed_at.date() == date(2026, 5, 10)  # Excel date cell, read without a time


def test_unrecognised_file_returns_no_mapping():
    grid = read_grid(b"Foo,Bar,Baz\n1,2,3\n", "x.csv")
    assert detect_mapping(grid) is None


def test_wrong_file_type_is_refused():
    with pytest.raises(ImportFileError, match=".xlsx"):
        read_grid(b"whatever", "trades.xls")


def test_llm_mapping_drops_invented_columns():
    grid = read_grid(b"Scrip,When,B/S,Units,Rate\nRELIANCE,10/05/2026,B,10,1450\n", "x.csv")
    names = {"symbol": "Scrip", "trade_date": "When", "side": "B/S", "quantity": "Units", "price": "Rate"}
    mapping = mapping_from_names(grid, 0, names)
    assert mapping is not None and mapping.columns["quantity"] == 3
    # A column name the model made up is ignored, so a required field is missing -> no mapping.
    assert mapping_from_names(grid, 0, {**names, "quantity": "Quantity Traded"}) is None


@pytest.mark.parametrize(
    "raw, expected",
    [("1,23,456.50", D("123456.50")), ("₹ 1450", D("1450")), ("(250.00)", D("-250.00")), ("-", None), ("abc", None)],
)
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-05-10", datetime(2026, 5, 10)),
        ("10-05-2026", datetime(2026, 5, 10)),  # day-first, as Indian exports write it
        ("10/05/2026", datetime(2026, 5, 10)),
        ("10-May-2026", datetime(2026, 5, 10)),
        ("2026-05-10 00:00:00", datetime(2026, 5, 10)),  # pandas' rendering of an Excel date
        ("46152", datetime(2026, 5, 10)),  # a raw Excel serial date
    ],
)
def test_parse_date_formats(value, expected):
    assert parse_datetime(value)[0] == expected


def test_row_problems_are_listed_for_review():
    row = parse_row(7, {"symbol": "RELIANCE", "side": "hold", "quantity": "0", "trade_date": "31-02-2026"}, {}, TODAY)
    assert row.issues == [
        "Can't tell buy or sell from “hold”",
        "Missing or zero quantity",
        "Missing price",
        "Can't read the date “31-02-2026”",
    ]


@pytest.mark.parametrize(
    "word, side",
    [("Buy", "BUY"), ("B", "BUY"), ("Purchase", "BUY"), ("SIP", "BUY"),
     ("Sell", "SELL"), ("S", "SELL"), ("Sale", "SELL"), ("Redemption", "SELL")],
)
def test_side_words(word, side):
    row = parse_row(1, {"isin": "INE002A01018", "side": word, "quantity": "1", "price": "1", "trade_date": "2026-05-10"}, {}, TODAY)
    assert row.txn_type == side


def test_future_date_and_negative_sell_quantity():
    row = parse_row(1, {"isin": "INE002A01018", "side": "S", "quantity": "-5", "price": "10", "trade_date": "2027-01-01"}, {}, TODAY)
    assert row.quantity == D("5") and row.txn_type == "SELL"
    assert row.issues == ["Trade date is in the future"]
