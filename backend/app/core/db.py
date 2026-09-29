"""asyncpg connection pool.

Non-negotiable rule (CLAUDE.md #13): this connection bypasses Postgres RLS
(it authenticates as the `postgres` role via the direct connection string,
not through PostgREST as `authenticated`). RLS stays enabled on every table
as defense in depth, but it does not protect us here — every query issued
through this pool MUST filter by the user_id taken from the verified JWT
(see core/auth.py), never from a request body or query param.

The pool is created lazily on first use rather than at app startup, so
`/health` and any route with an overridden `get_db` dependency (as tests
do) never need a real database to be reachable.
"""

import json

import asyncpg

from app.core.config import get_settings

_pool: asyncpg.Pool | None = None


async def init_connection(conn: asyncpg.Connection) -> None:
    """Runs once per new pooled connection. asyncpg hands json/jsonb back as raw strings
    (and refuses a dict as a jsonb parameter) unless a codec is registered -- without this,
    securities.metadata and profiles.preferences would reach Pydantic as str and fail."""
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            settings.database_url, min_size=1, max_size=10, init=init_connection
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_db() -> asyncpg.Connection:
    """FastAPI dependency: yields a connection borrowed from the pool for the request."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
