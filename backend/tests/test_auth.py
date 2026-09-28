from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException

from app.core.auth import get_current_user_id
from app.core.config import get_settings

SECRET = "test-jwt-secret"  # matches tests/conftest.py's SUPABASE_JWT_SECRET


def _make_token(sub: str | None, *, expired: bool = False, secret: str = SECRET) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "aud": "authenticated",
        "exp": now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
    }
    if sub is not None:
        payload["sub"] = sub
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.asyncio
async def test_valid_token_returns_user_id() -> None:
    get_settings.cache_clear()
    user_id = uuid4()
    token = _make_token(str(user_id))

    result = await get_current_user_id(authorization=f"Bearer {token}")

    assert result == user_id
    assert isinstance(result, UUID)


@pytest.mark.asyncio
async def test_missing_header_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(authorization=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_rejected() -> None:
    get_settings.cache_clear()
    token = _make_token(str(uuid4()), expired=True)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_secret_rejected() -> None:
    get_settings.cache_clear()
    token = _make_token(str(uuid4()), secret="not-the-real-secret")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_token_without_sub_rejected() -> None:
    get_settings.cache_clear()
    token = _make_token(sub=None)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401
