"""Route tests for /api/v1/portfolio against a fake connection.

One realistic portfolio, with every expected figure worked out by hand:

  TCS  (STOCK, IT)       BUY 10 @ 3800 + 20 charges (day 0)   -> lot cost 38020
                         SELL 4 @ 4000 - 10 charges (day 30)  -> cost 38020*4/10 = 15208,
                                                                 proceeds 15990, gain 782
                         left: 6 units costing 22812 (3802/unit); price 4100 -> worth 24600,
                         unrealised 1788
  HDFC MF (MF, no sector) BUY 100 @ 50                         -> invested 5000, NO price:
                                                                 carried at cost
  GSEC (BOND)            BUY 10 @ 99                           -> invested 990, no price:
                                                                 estimated at 100.8975 on
                                                                 29 Sep 2026 -> worth 1008.975
  INFY (STOCK, IT)       BUY 1 @ 1500, SELL 1 @ 1600           -> fully exited, realised 100

  invested   22812 + 5000 + 990       = 28802
  current    24600 + 5000 + 1008.975  = 30608.975
  unrealised 1788 + 0 + 18.975        = 1806.975
  realised   782 + 100                = 882
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.v1.portfolio as portfolio_module
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.main import app

FIXED_USER_ID = uuid4()
T0 = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 29)
GSEC_META = {"issuer": "Government of India", "coupon_pct": 7.18, "maturity_date": "2033-08-14"}

TCS = dict(security_id=uuid4(), isin="INE467B01029", symbol="TCS", name="Tata Consultancy Services",
           type="STOCK", sector="IT", metadata={}, latest_price=D("4100"), latest_price_date=date(2026, 9, 26))
MF = dict(security_id=uuid4(), isin="INF179K01XQ0", symbol=None, name="HDFC Large Cap Fund",
          type="MF", sector=None, metadata={"amc": "HDFC Mutual Fund"}, latest_price=None, latest_price_date=None)
GSEC = dict(security_id=uuid4(), isin="IN0020230085", symbol=None, name="7.18% GS 2033",
            type="BOND", sector="Government Securities", metadata=GSEC_META, latest_price=None,
            latest_price_date=None)
INFY = dict(security_id=uuid4(), isin="INE009A01021", symbol="INFY", name="Infosys",
            type="STOCK", sector="IT", metadata={}, latest_price=D("1700"), latest_price_date=date(2026, 9, 26))


def row(security, txn_type, qty, price, charges="0", day=0, txn_id=None):
    at = T0 + timedelta(days=day)
    return {"id": txn_id or uuid4(), "txn_type": txn_type, "quantity": D(qty), "price": D(price),
            "charges": D(charges), "executed_at": at, "created_at": at, **security}


TCS_BUY_ID, TCS_SELL_ID = uuid4(), uuid4()
PORTFOLIO_ROWS = [
    row(TCS, "BUY", "10", "3800", "20", day=0, txn_id=TCS_BUY_ID),
    row(TCS, "SELL", "4", "4000", "10", day=30, txn_id=TCS_SELL_ID),
    row(MF, "BUY", "100", "50", day=2),
    row(GSEC, "BUY", "10", "99", day=3),
    row(INFY, "BUY", "1", "1500", day=4),
    row(INFY, "SELL", "1", "1600", day=40),
]


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(portfolio_module, "today_ist", lambda: TODAY)

    def _make(rows):
        conn = FakeConnection(rows)

        async def fake_get_db():
            yield conn

        app.dependency_overrides[get_current_user_id] = lambda: FIXED_USER_ID
        app.dependency_overrides[get_db] = fake_get_db
        client = TestClient(app)
        client.fake_conn = conn
        return client

    yield _make
    app.dependency_overrides.clear()


def pct(part, whole):
    return str((D(part) / D(whole) * 100).quantize(D("0.000001")))


# --- summary ------------------------------------------------------------------------------

def test_summary(make_client):
    client = make_client(PORTFOLIO_ROWS)

    body = client.get("/api/v1/portfolio/summary").json()

    assert body == {
        "as_of": "2026-09-29",
        "invested": "28802.000000",
        "current_value": "30608.975000",
        "unrealized_pnl": "1806.975000",
        "unrealized_pnl_pct": pct("1806.975", "28802"),
        "realized_pnl": "882.000000",
        "holdings_count": 3,  # INFY is fully sold
        "unpriced_holdings": 1,  # the MF
        "estimated_holdings": 1,  # the bond
    }


def test_summary_scopes_the_ledger_query_by_verified_user(make_client):
    client = make_client(PORTFOLIO_ROWS)
    client.get("/api/v1/portfolio/summary")

    query, args = client.fake_conn.calls[0]
    assert args == (FIXED_USER_ID,)
    assert "t.user_id = $1" in query
    assert "t.deleted_at is null" in query


def test_empty_portfolio(make_client):
    client = make_client([])

    summary = client.get("/api/v1/portfolio/summary").json()
    assert summary["invested"] == "0.000000"
    assert summary["unrealized_pnl_pct"] is None
    assert summary["holdings_count"] == 0
    assert client.get("/api/v1/portfolio/holdings").json() == {"items": [], "next_cursor": None}
    assert client.get("/api/v1/portfolio/allocation").json()["buckets"] == []


# --- holdings -----------------------------------------------------------------------------

def test_holdings_sorted_by_value_and_exclude_exited(make_client):
    body = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/holdings").json()

    assert [h["isin"] for h in body["items"]] == [TCS["isin"], MF["isin"], GSEC["isin"]]
    assert body["next_cursor"] is None


def test_priced_holding_fields(make_client):
    tcs = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/holdings").json()["items"][0]

    assert tcs["quantity"] == "6.000000"
    assert tcs["avg_cost"] == "3802.000000"
    assert tcs["invested"] == "22812.000000"
    assert tcs["price"] == "4100.000000"
    assert tcs["price_date"] == "2026-09-26"
    assert tcs["price_estimated"] is False
    assert tcs["market_value"] == "24600.000000"
    assert tcs["unrealized_pnl"] == "1788.000000"
    assert tcs["unrealized_pnl_pct"] == pct("1788", "22812")
    assert tcs["realized_pnl"] == "782.000000"
    assert tcs["weight_pct"] == pct("24600", "30608.975")


def test_unpriced_holding_is_carried_at_cost(make_client):
    mf = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/holdings").json()["items"][1]

    assert mf["price"] is None and mf["market_value"] is None and mf["unrealized_pnl"] is None
    assert mf["weight_pct"] == pct("5000", "30608.975")  # still weighted, at cost


def test_bond_holding_is_estimated(make_client):
    bond = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/holdings").json()["items"][2]

    assert bond["price"] == "100.897500"
    assert bond["price_estimated"] is True
    assert bond["market_value"] == "1008.975000"


def test_holdings_type_filter_keeps_whole_portfolio_weights(make_client):
    body = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/holdings", params={"type": "MF"}).json()

    [mf] = body["items"]
    assert mf["isin"] == MF["isin"]
    assert mf["weight_pct"] == pct("5000", "30608.975")  # not 100%


def test_holdings_pagination(make_client):
    client = make_client(PORTFOLIO_ROWS)

    first = client.get("/api/v1/portfolio/holdings", params={"limit": 2}).json()
    second = client.get("/api/v1/portfolio/holdings", params={"limit": 2, "cursor": first["next_cursor"]}).json()

    assert [h["isin"] for h in first["items"]] == [TCS["isin"], MF["isin"]]
    assert [h["isin"] for h in second["items"]] == [GSEC["isin"]]
    assert second["next_cursor"] is None


# --- holding detail -----------------------------------------------------------------------

def test_holding_detail_lots(make_client):
    body = make_client(PORTFOLIO_ROWS).get(f"/api/v1/portfolio/holdings/{TCS['isin']}").json()

    [lot] = body["open_lots"]
    assert lot["buy_transaction_id"] == str(TCS_BUY_ID)
    assert (lot["quantity"], lot["unit_cost"], lot["cost"]) == ("6.000000", "3802.000000", "22812.000000")

    [sale] = body["realized"]
    assert (sale["buy_transaction_id"], sale["sell_transaction_id"]) == (str(TCS_BUY_ID), str(TCS_SELL_ID))
    assert (sale["quantity"], sale["cost"], sale["proceeds"], sale["gain"]) == (
        "4.000000", "15208.000000", "15990.000000", "782.000000"
    )
    assert sale["holding_days"] == 30


def test_holding_detail_for_fully_exited_security(make_client):
    body = make_client(PORTFOLIO_ROWS).get(f"/api/v1/portfolio/holdings/{INFY['isin']}").json()

    assert body["quantity"] == "0.000000"
    assert body["avg_cost"] is None
    assert body["market_value"] is None
    assert body["weight_pct"] == "0.000000"
    assert body["realized_pnl"] == "100.000000"
    assert body["open_lots"] == []


def test_holding_detail_untraded_security_is_404(make_client):
    response = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/holdings/INE002A01018")
    assert response.status_code == 404


def test_holding_detail_rejects_malformed_isin(make_client):
    assert make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/holdings/nope").status_code == 422


# --- allocation ---------------------------------------------------------------------------

def test_allocation_by_asset_class(make_client):
    body = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/allocation").json()

    assert body["by"] == "asset_class"
    assert body["total_value"] == "30608.975000"
    assert [(b["key"], b["value"], b["holdings_count"]) for b in body["buckets"]] == [
        ("STOCK", "24600.000000", 1),
        ("MF", "5000.000000", 1),
        ("BOND", "1008.975000", 1),
    ]
    assert body["buckets"][0]["weight_pct"] == pct("24600", "30608.975")


def test_allocation_by_sector(make_client):
    body = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/allocation", params={"by": "sector"}).json()

    assert [b["key"] for b in body["buckets"]] == ["IT", "Unclassified", "Government Securities"]


def test_allocation_rejects_unknown_dimension(make_client):
    response = make_client(PORTFOLIO_ROWS).get("/api/v1/portfolio/allocation", params={"by": "country"})
    assert response.status_code == 422
