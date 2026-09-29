"""GET /securities and friends.

Read-only in Phase 1 -- writing here (the sync job, custom-security creation)
comes later. Every query below applies the same visibility rule: a security
is visible if it's public (created_by is null, i.e. it came from the sync/
seed) or the caller created it themselves. The backend's DB connection
bypasses RLS (CLAUDE.md rule 13), so that rule has to be repeated here in
every query rather than left to Postgres.
"""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.v1.pagination import ISIN_PATTERN
from app.api.v1.pagination import decode_cursor as _decode_cursor
from app.api.v1.pagination import encode_cursor as _encode_cursor
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.security import (
    LatestPrice,
    PriceHistory,
    PricePoint,
    SecurityDetail,
    SecurityList,
    SecuritySummary,
    SecurityType,
)

router = APIRouter(prefix="/securities", tags=["securities"])

@router.get("", response_model=SecurityList)
async def list_securities(
    q: str | None = Query(default=None, min_length=1, max_length=100),
    security_type: SecurityType | None = Query(default=None, alias="type"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> SecurityList:
    offset = _decode_cursor(cursor)

    # symbol/isin prefix and name substring matches all go through the pg_trgm GIN indexes
    # built in the Phase 1 migration (securities_name_trgm_idx, securities_symbol_trgm_idx),
    # which is what makes a leading-wildcard ILIKE fast at this table's scale.
    # Fetch one extra row (limit + 1) to know whether a next page exists without a separate count query.
    rows = await conn.fetch(
        """
        select isin, name, type, symbol, exchange, sector, is_custom
        from public.securities
        where is_active = true
          and (created_by is null or created_by = $1)
          and ($2::public.security_type is null or type = $2)
          and (
            $3::text is null
            or symbol ilike $3 || '%'
            or isin   ilike $3 || '%'
            or name   ilike '%' || $3 || '%'
          )
        order by
          case
            when $3::text is not null and symbol ilike $3 || '%' then 0
            when $3::text is not null and isin   ilike $3 || '%' then 1
            else 2
          end,
          name
        limit $4 offset $5
        """,
        user_id,
        security_type.value if security_type else None,
        q,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(offset + limit) if has_more else None

    return SecurityList(
        items=[SecuritySummary(**dict(row)) for row in page],
        next_cursor=next_cursor,
    )


@router.get("/{isin}", response_model=SecurityDetail)
async def get_security(
    isin: str = Path(..., pattern=ISIN_PATTERN),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> SecurityDetail:
    row = await conn.fetchrow(
        """
        select id, isin, name, type, symbol, exchange, sector, is_active, is_custom,
               metadata, last_synced_at, created_at, updated_at
        from public.securities
        where isin = $1
          and (created_by is null or created_by = $2)
        """,
        isin,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Security not found")
    return SecurityDetail(**dict(row))


@router.get("/{isin}/price", response_model=LatestPrice)
async def get_latest_price(
    isin: str = Path(..., pattern=ISIN_PATTERN),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> LatestPrice:
    row = await conn.fetchrow(
        """
        select s.isin, lp.price_date, lp.price, lp.source
        from public.securities s
        join public.latest_prices lp on lp.security_id = s.id
        where s.isin = $1
          and (s.created_by is null or s.created_by = $2)
        """,
        isin,
        user_id,
    )
    if row is None:
        # Legitimately empty right now: Phase 1 seeded securities but not prices
        # (see supabase/seed.sql) -- the price refresh job fills this in later.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No price available for this security")
    data = dict(row)
    data["price"] = str(data["price"])
    return LatestPrice(**data)


@router.get("/{isin}/history", response_model=PriceHistory)
async def get_price_history(
    isin: str = Path(..., pattern=ISIN_PATTERN),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> PriceHistory:
    # Confirm visibility first so a hidden custom security 404s instead of returning an empty history
    # (an empty list would be indistinguishable from "no prices yet" for a security that does exist).
    exists = await conn.fetchval(
        "select true from public.securities where isin = $1 and (created_by is null or created_by = $2)",
        isin,
        user_id,
    )
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Security not found")

    rows = await conn.fetch(
        """
        select sp.price_date, sp.price, sp.source
        from public.security_prices sp
        join public.securities s on s.id = sp.security_id
        where s.isin = $1
          and ($2::date is null or sp.price_date >= $2::date)
          and ($3::date is null or sp.price_date <= $3::date)
        order by sp.price_date
        """,
        isin,
        from_,
        to,
    )
    points = []
    for row in rows:
        data = dict(row)
        data["price"] = str(data["price"])
        points.append(PricePoint(**data))
    return PriceHistory(isin=isin, points=points)
