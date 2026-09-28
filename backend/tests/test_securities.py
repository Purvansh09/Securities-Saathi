from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1.securities import _decode_cursor, _encode_cursor
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.main import app

FIXED_USER_ID = uuid4()


class FakeConnection:
    """Same idea as tests/test_me.py's FakeConnection, extended with fetch/fetchval
    since the securities endpoints use those too."""

    def __init__(self, *, fetch_result=None, fetchrow_result=None, fetchval_result=None):
        self.fetch_result = fetch_result or []
        self.fetchrow_result = fetchrow_result
        self.fetchval_result = fetchval_result
        self.last_query: str | None = None
        self.last_args: tuple = ()

    async def fetch(self, query: str, *args):
        self.last_query = query
        self.last_args = args
        return self.fetch_result

    async def fetchrow(self, query: str, *args):
        self.last_query = query
        self.last_args = args
        return self.fetchrow_result

    async def fetchval(self, query: str, *args):
        self.last_query = query
        self.last_args = args
        return self.fetchval_result


def _make_client(fake_conn: FakeConnection) -> TestClient:
    async def fake_get_db():
        yield fake_conn

    app.dependency_overrides[get_current_user_id] = lambda: FIXED_USER_ID
    app.dependency_overrides[get_db] = fake_get_db
    client = TestClient(app)
    client.fake_conn = fake_conn  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# --- cursor helper -----------------------------------------------------------

def test_cursor_round_trip():
    for offset in (0, 20, 1000):
        assert _decode_cursor(_encode_cursor(offset)) == offset


def test_decode_cursor_none_is_zero():
    assert _decode_cursor(None) == 0


def test_decode_cursor_rejects_garbage():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _decode_cursor("not-a-valid-cursor!!!")
    assert exc_info.value.status_code == 400


# --- GET /securities -----------------------------------------------------------

def _security_row(isin="INE002A01018", name="Reliance Industries Ltd.", symbol="RELIANCE"):
    return {
        "isin": isin,
        "name": name,
        "type": "STOCK",
        "symbol": symbol,
        "exchange": "NSE",
        "sector": "Energy",
        "is_custom": False,
    }


def test_list_securities_no_next_cursor_when_fewer_than_limit():
    conn = FakeConnection(fetch_result=[_security_row()])
    client = _make_client(conn)

    response = client.get("/api/v1/securities", params={"limit": 20})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] is None
    # visibility filter's user_id must be the one from the verified JWT
    assert conn.last_args[0] == FIXED_USER_ID


def test_list_securities_returns_next_cursor_when_more_rows_exist():
    # limit=2 requested; fetch returns limit+1=3 rows -> a next page exists
    conn = FakeConnection(fetch_result=[_security_row(isin=f"INE00200000{i}") for i in range(3)])
    client = _make_client(conn)

    response = client.get("/api/v1/securities", params={"limit": 2})

    body = response.json()
    assert len(body["items"]) == 2  # the extra row is dropped, never shown to the client
    assert body["next_cursor"] is not None
    assert _decode_cursor(body["next_cursor"]) == 2  # offset 0 + limit 2


def test_list_securities_passes_type_filter_value():
    conn = FakeConnection(fetch_result=[])
    client = _make_client(conn)

    client.get("/api/v1/securities", params={"type": "ETF"})

    # args order: user_id, type, q, limit+1, offset
    assert conn.last_args[1] == "ETF"


def test_list_securities_rejects_invalid_type():
    conn = FakeConnection(fetch_result=[])
    client = _make_client(conn)

    response = client.get("/api/v1/securities", params={"type": "NOT_A_TYPE"})

    assert response.status_code == 422


# --- GET /securities/{isin} -----------------------------------------------------------

def test_get_security_not_found():
    conn = FakeConnection(fetchrow_result=None)
    client = _make_client(conn)

    response = client.get("/api/v1/securities/INE002A01018")

    assert response.status_code == 404


def test_get_security_rejects_malformed_isin():
    conn = FakeConnection()
    client = _make_client(conn)

    response = client.get("/api/v1/securities/not-an-isin")

    assert response.status_code == 422


def test_get_security_found():
    now = datetime.now(timezone.utc)
    row = {
        "id": uuid4(),
        "isin": "INE002A01018",
        "name": "Reliance Industries Ltd.",
        "type": "STOCK",
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "sector": "Energy",
        "is_active": True,
        "is_custom": False,
        "metadata": {},
        "last_synced_at": None,
        "created_at": now,
        "updated_at": now,
    }
    conn = FakeConnection(fetchrow_result=row)
    client = _make_client(conn)

    response = client.get("/api/v1/securities/INE002A01018")

    assert response.status_code == 200
    assert response.json()["isin"] == "INE002A01018"


# --- GET /securities/{isin}/price -----------------------------------------------------------

def test_get_latest_price_not_found_returns_404_not_500():
    # This is the expected state for every seeded security right now (see supabase/seed.sql --
    # prices aren't seeded), so it must be a clean 404, not an error.
    conn = FakeConnection(fetchrow_result=None)
    client = _make_client(conn)

    response = client.get("/api/v1/securities/INE002A01018/price")

    assert response.status_code == 404


def test_get_latest_price_serializes_price_as_string():
    from decimal import Decimal

    row = {"isin": "INE002A01018", "price_date": date(2026, 9, 1), "price": Decimal("2650.500000"), "source": "MANUAL"}
    conn = FakeConnection(fetchrow_result=row)
    client = _make_client(conn)

    response = client.get("/api/v1/securities/INE002A01018/price")

    assert response.status_code == 200
    price = response.json()["price"]
    assert isinstance(price, str)
    assert price == "2650.500000"


# --- GET /securities/{isin}/history -----------------------------------------------------------

def test_get_price_history_404_when_security_does_not_exist():
    conn = FakeConnection(fetchval_result=None)
    client = _make_client(conn)

    response = client.get("/api/v1/securities/INE002A01018/history")

    assert response.status_code == 404


def test_get_price_history_empty_list_when_security_exists_but_unpriced():
    conn = FakeConnection(fetchval_result=True, fetch_result=[])
    client = _make_client(conn)

    response = client.get("/api/v1/securities/INE002A01018/history")

    assert response.status_code == 200
    assert response.json()["points"] == []
