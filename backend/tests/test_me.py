"""Route-wiring tests for /api/v1/me, using a fake connection instead of a real database.

These check the endpoints call the DB with the right, user_id-scoped query --
not real Postgres behaviour (the schema smoke test against the live Supabase
project already covered that; see the conversation this repo was built from).
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.main import app

FIXED_USER_ID = uuid4()


class FakeConnection:
    """Records the last query/args it was called with and returns a canned row."""

    def __init__(self, row: dict | None):
        self.row = row
        self.last_query: str | None = None
        self.last_args: tuple = ()

    async def fetchrow(self, query: str, *args):
        self.last_query = query
        self.last_args = args
        return self.row


@pytest.fixture
def profile_row() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": FIXED_USER_ID,
        "full_name": "Purvansh",
        "preferences": {},
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture
def client(profile_row):
    fake_conn = FakeConnection(profile_row)

    async def fake_get_db():
        yield fake_conn

    app.dependency_overrides[get_current_user_id] = lambda: FIXED_USER_ID
    app.dependency_overrides[get_db] = fake_get_db

    with TestClient(app) as test_client:
        test_client.fake_conn = fake_conn  # type: ignore[attr-defined]
        yield test_client

    app.dependency_overrides.clear()


def test_get_me_returns_profile(client, profile_row) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(FIXED_USER_ID)
    assert body["full_name"] == "Purvansh"
    # The query must be scoped by the verified user_id, not anything from the request.
    assert client.fake_conn.last_args == (FIXED_USER_ID,)


def test_patch_me_updates_only_sent_fields(client) -> None:
    response = client.patch("/api/v1/me", json={"full_name": "Purvansh Dave"})

    assert response.status_code == 200
    query = client.fake_conn.last_query
    assert "full_name = $1" in query
    # preferences legitimately appears in the RETURNING clause; it must not appear
    # in the SET clause, since it wasn't part of the PATCH payload.
    assert "preferences =" not in query
    # Last bound param must be the verified user_id, from the JWT -- never the request body.
    assert client.fake_conn.last_args[-1] == FIXED_USER_ID


def test_patch_me_with_no_fields_just_returns_current_profile(client) -> None:
    response = client.patch("/api/v1/me", json={})

    assert response.status_code == 200
    assert client.fake_conn.last_args == (FIXED_USER_ID,)
