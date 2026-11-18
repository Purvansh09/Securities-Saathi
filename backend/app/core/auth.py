"""JWT verification.

Non-negotiable rule (CLAUDE.md #12): we never hand-roll signup/login —
Supabase Auth does that from the frontend. This module only verifies the
JWT Supabase issued and trusts its `sub` claim as the user_id. It never
issues or refreshes tokens itself.

Supabase has two signing modes, and a project can be mid-migration between them:
- Asymmetric signing keys (ES256/RS256) — the current default, and what this
  project uses. Supabase signs with a private key it never shares; we verify
  with the matching public key, published at
  {SUPABASE_URL}/auth/v1/.well-known/jwks.json and picked by the token's `kid`.
- Legacy shared secret (HS256) — still accepted, because tokens issued before the
  switch (and the legacy anon/service keys) are signed this way until they expire.

The token's header says which one it is, but we never let the header choose the
key freely: HS256 is only ever checked against the shared secret, and ES256/RS256
only against a public key from Supabase's JWKS. That closes the classic
"algorithm confusion" attack (signing an HS256 token with the *public* key as
if it were the secret).
"""

from uuid import UUID

import jwt
from fastapi import Header, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

_ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")

_jwks_client: jwt.PyJWKClient | None = None


def _get_jwks_client() -> jwt.PyJWKClient:
    """One client per process. It caches the key set for `lifespan` seconds, and if a
    token arrives with a `kid` it hasn't seen (Supabase rotated keys) it refetches once."""
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        # Supabase's API gateway wants an apikey header even on this public endpoint.
        # The publishable key is designed to be public, so it's safe to send.
        headers = {"apikey": settings.supabase_publishable_key} if settings.supabase_publishable_key else None
        _jwks_client = jwt.PyJWKClient(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json",
            headers=headers,
            cache_jwk_set=True,
            lifespan=600,
            timeout=5,
        )
    return _jwks_client


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _decode(token: str) -> dict:
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Invalid token") from exc

    try:
        if alg == "HS256":
            key = get_settings().supabase_jwt_secret
        elif alg in _ASYMMETRIC_ALGORITHMS:
            key = _get_jwks_client().get_signing_key_from_jwt(token).key
        else:
            # Covers "none" and anything else we don't expect Supabase to issue.
            raise _unauthorized("Unsupported token algorithm")

        return jwt.decode(token, key, algorithms=[alg], audience="authenticated")
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Token expired") from exc
    except jwt.PyJWKClientConnectionError as exc:
        # We couldn't fetch Supabase's public keys. That's our outage, not a bad token,
        # so say 503 rather than telling the client to log in again.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not verify token right now"
        ) from exc
    except (jwt.PyJWKClientError, jwt.InvalidTokenError) as exc:
        # PyJWKClientError: no key in the JWKS matches the token's kid.
        raise _unauthorized("Invalid token") from exc


async def get_current_user_id(authorization: str | None = Header(default=None)) -> UUID:
    """FastAPI dependency. Reads `Authorization: Bearer <jwt>` and returns the caller's user_id.

    Every route that touches user data depends on this rather than trusting
    any user_id a client might put in the request body or query string.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Missing bearer token")

    token = authorization.split(" ", 1)[1]
    # Run in a worker thread: on a JWKS cache miss, PyJWKClient does a blocking HTTP
    # fetch, which would otherwise stall every other request on the event loop.
    payload = await run_in_threadpool(_decode, token)

    sub = payload.get("sub")
    if not sub:
        raise _unauthorized("Token missing sub claim")

    try:
        return UUID(sub)
    except ValueError as exc:
        raise _unauthorized("sub claim is not a UUID") from exc


async def require_internal_secret(x_internal_secret: str | None = Header(default=None)) -> None:
    """FastAPI dependency for the internal sync/refresh endpoints (CLAUDE.md API conventions)."""
    settings = get_settings()
    if not settings.internal_sync_secret or x_internal_secret != settings.internal_sync_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal secret")
