"""JWT verification.

Non-negotiable rule (CLAUDE.md #12): we never hand-roll signup/login —
Supabase Auth does that from the frontend. This module only verifies the
JWT Supabase issued and trusts its `sub` claim as the user_id. It never
issues or refreshes tokens itself.

Supabase's default (legacy) JWT signing is HS256 with the project's JWT
secret, audience "authenticated". If the project is switched to the newer
asymmetric JWT signing keys, this verification needs to move to JWKS
(fetching public keys from {SUPABASE_URL}/auth/v1/.well-known/jwks.json)
instead of a shared secret — flag that to the guide if it comes up, it's
a real "current infra" gap, not a bug.
"""

from uuid import UUID

import jwt
from fastapi import Header, HTTPException, status

from app.core.config import get_settings


def _decode(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc


async def get_current_user_id(authorization: str | None = Header(default=None)) -> UUID:
    """FastAPI dependency. Reads `Authorization: Bearer <jwt>` and returns the caller's user_id.

    Every route that touches user data depends on this rather than trusting
    any user_id a client might put in the request body or query string.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1]
    payload = _decode(token)

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub claim")

    try:
        return UUID(sub)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sub claim is not a UUID") from exc


async def require_internal_secret(x_internal_secret: str | None = Header(default=None)) -> None:
    """FastAPI dependency for the internal sync/refresh endpoints (CLAUDE.md API conventions)."""
    settings = get_settings()
    if not settings.internal_sync_secret or x_internal_secret != settings.internal_sync_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal secret")
