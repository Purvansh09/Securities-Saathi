"""Internal job triggers (CLAUDE.md API conventions): protected by the X-Internal-Secret header,
not by a user JWT, because they're called by a scheduler rather than a person."""

from datetime import date

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth import require_internal_secret
from app.core.db import get_db
from app.services.securities_sync.jobs import run_price_refresh, run_sync
from app.services.securities_sync.sources import SourceFormatError

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_secret)])


def _upstream_error(exc: Exception) -> HTTPException:
    # 502: the failure is in a publisher's file or server, not in this request.
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{type(exc).__name__}: {exc}")


@router.post("/sync-securities")
async def sync_securities(conn: asyncpg.Connection = Depends(get_db)) -> dict:
    try:
        return await run_sync(conn)
    except (SourceFormatError, httpx.HTTPError) as exc:
        raise _upstream_error(exc) from exc


@router.post("/refresh-prices")
async def refresh_prices(
    as_of: date | None = Query(default=None, description="Price date; defaults to today in IST"),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict:
    try:
        return await run_price_refresh(conn, as_of)
    except (SourceFormatError, httpx.HTTPError) as exc:
        raise _upstream_error(exc) from exc
