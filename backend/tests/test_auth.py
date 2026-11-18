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


# --- Asymmetric (ES256) tokens: the project's current signing mode --------------------------
# We use a real PyJWKClient and only replace its network fetch, so kid lookup and key parsing
# run exactly as they would against Supabase's JWKS endpoint.

import hashlib
import hmac
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import auth

KID = "test-kid-1"
_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())


def _jwks_for(private_key: ec.EllipticCurvePrivateKey, kid: str = KID) -> dict:
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
    return {"keys": [jwk]}


def _make_es256_token(sub: str, *, key=_PRIVATE_KEY, kid: str = KID, expired: bool = False) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "exp": now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
    }
    return jwt.encode(payload, key, algorithm="ES256", headers={"kid": kid})


@pytest.fixture
def fake_jwks(monkeypatch):
    """Point auth at a JWKS client whose 'network fetch' returns our test public key."""
    client = jwt.PyJWKClient("https://example.supabase.co/auth/v1/.well-known/jwks.json")
    monkeypatch.setattr(client, "fetch_data", lambda: _jwks_for(_PRIVATE_KEY))
    monkeypatch.setattr(auth, "_jwks_client", client)
    return client


async def _status_for(token: str) -> int:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(authorization=f"Bearer {token}")
    return exc_info.value.status_code


@pytest.mark.asyncio
async def test_es256_token_returns_user_id(fake_jwks) -> None:
    user_id = uuid4()
    assert await get_current_user_id(authorization=f"Bearer {_make_es256_token(str(user_id))}") == user_id


@pytest.mark.asyncio
async def test_es256_expired_rejected(fake_jwks) -> None:
    assert await _status_for(_make_es256_token(str(uuid4()), expired=True)) == 401


@pytest.mark.asyncio
async def test_es256_signed_by_other_key_rejected(fake_jwks) -> None:
    # Same kid, but signed by a key that isn't Supabase's: signature check must fail.
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    assert await _status_for(_make_es256_token(str(uuid4()), key=attacker_key)) == 401


@pytest.mark.asyncio
async def test_es256_unknown_kid_rejected(fake_jwks) -> None:
    assert await _status_for(_make_es256_token(str(uuid4()), kid="no-such-kid")) == 401


@pytest.mark.asyncio
async def test_jwks_unreachable_is_503_not_401(monkeypatch) -> None:
    # If we can't fetch Supabase's keys, that's our outage; don't tell the user to log in again.
    client = jwt.PyJWKClient("https://example.supabase.co/auth/v1/.well-known/jwks.json")

    def boom():
        raise jwt.PyJWKClientConnectionError("network down")

    monkeypatch.setattr(client, "fetch_data", boom)
    monkeypatch.setattr(auth, "_jwks_client", client)
    assert await _status_for(_make_es256_token(str(uuid4()))) == 503


@pytest.mark.asyncio
async def test_alg_none_rejected(fake_jwks) -> None:
    token = jwt.encode({"sub": str(uuid4()), "aud": "authenticated"}, key=None, algorithm="none")
    assert await _status_for(token) == 401


@pytest.mark.asyncio
async def test_algorithm_confusion_rejected(fake_jwks) -> None:
    # Classic attack: take the *public* key (which anyone can download from the JWKS) and use it
    # as an HMAC secret to sign an HS256 token. HS256 is only ever checked against the shared
    # secret, so this must fail.
    get_settings.cache_clear()
    public_pem = _PRIVATE_KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    def b64(obj: dict) -> bytes:
        return jwt.utils.base64url_encode(json.dumps(obj, separators=(",", ":")).encode())

    signing_input = b64({"alg": "HS256", "typ": "JWT"}) + b"." + b64({"sub": str(uuid4()), "aud": "authenticated"})
    sig = jwt.utils.base64url_encode(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    token = (signing_input + b"." + sig).decode()
    assert await _status_for(token) == 401


@pytest.mark.asyncio
async def test_garbage_token_rejected() -> None:
    assert await _status_for("not.a.jwt") == 401
