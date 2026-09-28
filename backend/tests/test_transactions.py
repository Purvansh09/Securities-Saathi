"""Route-wiring tests for /api/v1/transactions, against a fake connection.

These check control flow -- which queries run in which order, what gets
scoped by the verified user_id, and how a LedgerViolation becomes an HTTP
422 -- not real Postgres transaction/rollback semantics. That side was
already exercised directly against the live database earlier (a real
oversell flipped ledger_balance_ok to false; a soft-deleted correcting
transaction flipped it back), before any of this endpoint code existed.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.main import app

FIXED_USER_ID = uuid4()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False  # never swallow -- mirrors asyncpg: rollback happens, exception still propagates


class FakeConnection:
    """fetchval/fetchrow/fetch each pop from their own queue in call order, so a test
    supplies canned responses in the order the endpoint is expected to ask for them."""

    def __init__(self, *, fetchval=None, fetchrow=None, fetch=None):
        self.fetchval_queue = list(fetchval or [])
        self.fetchrow_queue = list(fetchrow or [])
        self.fetch_queue = list(fetch or [])
        self.calls: list[tuple] = []

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self.fetchval_queue.pop(0)

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self.fetchrow_queue.pop(0)

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return self.fetch_queue.pop(0)

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        return "UPDATE 1"

    def transaction(self):
        return _FakeTransaction()

    def calls_of(self, method):
        return [c for c in self.calls if c[0] == method]


def _make_client(conn: FakeConnection) -> TestClient:
    async def fake_get_db():
        yield conn

    app.dependency_overrides[get_current_user_id] = lambda: FIXED_USER_ID
    app.dependency_overrides[get_db] = fake_get_db
    client = TestClient(app)
    client.fake_conn = conn  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


SECURITY_ID = uuid4()
ISIN = "INE002A01018"


def _txn_row(**overrides):
    now = datetime.now(timezone.utc)
    row = {
        "id": uuid4(),
        "txn_type": "BUY",
        "quantity": Decimal("10"),
        "price": Decimal("2500.00"),
        "charges": Decimal("20.00"),
        "executed_at": now,
        "notes": None,
        "source": "MANUAL",
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


VALID_CREATE_PAYLOAD = {
    "isin": ISIN,
    "txn_type": "BUY",
    "quantity": "10",
    "price": "2500.00",
    "charges": "20.00",
    "executed_at": "2026-01-01T10:00:00Z",
}


# --- validation happens before any DB call ------------------------------------

@pytest.mark.parametrize(
    "overrides",
    [
        {"quantity": "0"},
        {"quantity": "-5"},
        {"price": "-1"},
        {"charges": "-1"},
        {"isin": "not-an-isin"},
    ],
)
def test_create_transaction_rejects_bad_input_without_touching_db(overrides):
    conn = FakeConnection()  # empty queues -- if the endpoint tries a query, popping raises IndexError
    client = _make_client(conn)

    response = client.post("/api/v1/transactions", json={**VALID_CREATE_PAYLOAD, **overrides})

    assert response.status_code == 422
    assert conn.calls == []


# --- POST /transactions ------------------------------------------------------

def test_create_transaction_success():
    conn = FakeConnection(
        fetchval=[SECURITY_ID, True],  # resolve isin -> security_id, then ledger_balance_ok
        fetchrow=[_txn_row()],  # the insert...returning
    )
    client = _make_client(conn)

    response = client.post("/api/v1/transactions", json=VALID_CREATE_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["isin"] == ISIN
    assert body["quantity"] == "10"  # decimal-as-string, not a JSON number
    assert len(conn.calls_of("execute")) == 1  # the advisory lock


def test_create_transaction_security_not_found():
    conn = FakeConnection(fetchval=[None])
    client = _make_client(conn)

    response = client.post("/api/v1/transactions", json=VALID_CREATE_PAYLOAD)

    assert response.status_code == 404
    assert conn.calls_of("execute") == []  # never got as far as taking the lock


def test_create_transaction_ledger_violation_returns_422():
    conn = FakeConnection(
        fetchval=[SECURITY_ID, False],  # balance check fails
        fetchrow=[_txn_row()],
    )
    client = _make_client(conn)

    response = client.post("/api/v1/transactions", json=VALID_CREATE_PAYLOAD)

    assert response.status_code == 422
    assert "negative" in response.json()["detail"]


# --- GET /transactions --------------------------------------------------------

def test_list_transactions_scopes_by_verified_user_id():
    conn = FakeConnection(fetch=[[dict(_txn_row(), isin=ISIN)]])
    client = _make_client(conn)

    response = client.get("/api/v1/transactions")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    fetch_call = conn.calls_of("fetch")[0]
    assert fetch_call[2][0] == FIXED_USER_ID  # first bound param is always the verified user_id


def test_list_transactions_pagination_next_cursor():
    rows = [dict(_txn_row(), isin=ISIN) for _ in range(3)]
    conn = FakeConnection(fetch=[rows])  # limit=2 -> fetch limit+1=3 -> a next page exists
    client = _make_client(conn)

    response = client.get("/api/v1/transactions", params={"limit": 2})

    body = response.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None


# --- GET /transactions/{id} ---------------------------------------------------

def test_get_transaction_not_found():
    conn = FakeConnection(fetchrow=[None])
    client = _make_client(conn)

    response = client.get(f"/api/v1/transactions/{uuid4()}")

    assert response.status_code == 404


def test_get_transaction_found():
    conn = FakeConnection(fetchrow=[dict(_txn_row(), isin=ISIN)])
    client = _make_client(conn)

    response = client.get(f"/api/v1/transactions/{uuid4()}")

    assert response.status_code == 200
    assert response.json()["isin"] == ISIN


# --- PATCH /transactions/{id} -------------------------------------------------

def test_update_transaction_not_found():
    conn = FakeConnection(fetchrow=[None])
    client = _make_client(conn)

    response = client.patch(f"/api/v1/transactions/{uuid4()}", json={"notes": "typo fix"})

    assert response.status_code == 404


def test_update_transaction_builds_set_clause_from_sent_fields_only():
    txn_id = uuid4()
    conn = FakeConnection(
        fetchrow=[
            {"id": txn_id, "security_id": SECURITY_ID},  # _get_owned_transaction
            _txn_row(),  # update ... returning
        ],
        fetchval=[True, ISIN],  # ledger_balance_ok, then the isin lookup
    )
    client = _make_client(conn)

    response = client.patch(f"/api/v1/transactions/{txn_id}", json={"quantity": "5"})

    assert response.status_code == 200
    update_call = conn.calls_of("fetchrow")[1]
    assert "quantity = $1" in update_call[1]
    assert "price" not in update_call[1].split("returning")[0]  # not sent, not in the SET clause


def test_update_transaction_ledger_violation_returns_422():
    txn_id = uuid4()
    conn = FakeConnection(
        fetchrow=[{"id": txn_id, "security_id": SECURITY_ID}, _txn_row()],
        fetchval=[False],  # balance check fails; the isin lookup after is never reached
    )
    client = _make_client(conn)

    response = client.patch(f"/api/v1/transactions/{txn_id}", json={"quantity": "999"})

    assert response.status_code == 422


def test_update_transaction_rejects_bad_decimal_without_touching_lock():
    txn_id = uuid4()
    conn = FakeConnection(fetchrow=[{"id": txn_id, "security_id": SECURITY_ID}])
    client = _make_client(conn)

    response = client.patch(f"/api/v1/transactions/{txn_id}", json={"quantity": "not-a-number"})

    assert response.status_code == 422
    assert conn.calls_of("execute") == []  # never took the advisory lock


# --- DELETE /transactions/{id} ------------------------------------------------

def test_delete_transaction_success():
    txn_id = uuid4()
    conn = FakeConnection(
        fetchrow=[{"id": txn_id, "security_id": SECURITY_ID}],
        fetchval=[True],
    )
    client = _make_client(conn)

    response = client.delete(f"/api/v1/transactions/{txn_id}")

    assert response.status_code == 204
    execute_calls = conn.calls_of("execute")
    assert len(execute_calls) == 2  # advisory lock, then the soft-delete update
    assert "deleted_at = now()" in execute_calls[1][1]


def test_delete_transaction_not_found():
    conn = FakeConnection(fetchrow=[None])
    client = _make_client(conn)

    response = client.delete(f"/api/v1/transactions/{uuid4()}")

    assert response.status_code == 404


def test_delete_transaction_ledger_violation_returns_422():
    # Deleting a BUY that a later SELL now depends on -- the case rule 5 exists for.
    txn_id = uuid4()
    conn = FakeConnection(
        fetchrow=[{"id": txn_id, "security_id": SECURITY_ID}],
        fetchval=[False],
    )
    client = _make_client(conn)

    response = client.delete(f"/api/v1/transactions/{txn_id}")

    assert response.status_code == 422
