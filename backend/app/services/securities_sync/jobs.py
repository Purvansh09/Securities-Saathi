"""The two daily jobs: sync the securities master, then refresh prices. Each returns a
summary dict (counts and warnings) that the CLI prints and the internal endpoints return."""

import time
from datetime import date, datetime, timedelta, timezone

import asyncpg
import httpx

from app.services.securities_sync import fetch, merge, sources, store

IST = timezone(timedelta(hours=5, minutes=30))
PRICE_WINDOW_DAYS = 7  # history kept for securities nobody has traded (see store.prune_prices)


def today_ist() -> date:
    return datetime.now(IST).date()


async def run_sync(conn: asyncpg.Connection, client: httpx.AsyncClient | None = None) -> dict:
    started = time.monotonic()
    warnings: list[str] = []
    own_client = client is None
    client = client or fetch.make_client()
    try:
        # Required: these lists define what exists. Any failure here stops the sync.
        nse_equity = sources.parse_nse_equity_list(await fetch.fetch_text(client, fetch.NSE_EQUITY_LIST))
        nse_etfs = sources.parse_nse_etf_list(await fetch.fetch_text(client, fetch.NSE_ETF_LIST))
        amfi, _ = sources.parse_amfi_navall(await fetch.fetch_text(client, fetch.AMFI_NAVALL))
        merge.check_minimums({"NSE equity list": len(nse_equity), "NSE ETF list": len(nse_etfs), "AMFI NAVAll": len(amfi)})

        # Optional enrichments: without them the sync is still correct, just less complete.
        sectors: dict[str, str] = {}
        try:
            sectors = sources.parse_nifty500_sectors(await fetch.fetch_text(client, fetch.NSE_NIFTY500))
        except (httpx.HTTPError, sources.SourceFormatError) as exc:
            warnings.append(f"Sectors skipped: {exc}")

        bse_rows: list[sources.BhavRow] = []
        try:
            found = await fetch.fetch_bhavcopy(client, "BSE", today_ist())
            if found:
                bse_rows = sources.parse_bhavcopy(found[1], "BSE")
            else:
                warnings.append("BSE bhavcopy not found for the last 7 days; BSE-only stocks skipped")
        except (httpx.HTTPError, sources.SourceFormatError) as exc:
            warnings.append(f"BSE bhavcopy skipped: {exc}")
    finally:
        if own_client:
            await client.aclose()

    records = merge.merge_securities(nse_equity, nse_etfs, amfi, bse_rows, sectors)
    result = await store.upsert_securities(conn, records)

    return {
        "job": "sync-securities",
        "sources": {
            "nse_equity": len(nse_equity),
            "nse_etf": len(nse_etfs),
            "amfi_mf": len(amfi),
            "bse_bhavcopy_rows": len(bse_rows),
            "nifty500_sectors": len(sectors),
        },
        "securities": {"total": len(records), **result},
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 1),
    }


async def run_price_refresh(
    conn: asyncpg.Connection, as_of: date | None = None, client: httpx.AsyncClient | None = None
) -> dict:
    started = time.monotonic()
    as_of = as_of or today_ist()
    warnings: list[str] = []
    own_client = client is None
    client = client or fetch.make_client()
    try:
        nse = await fetch.fetch_bhavcopy(client, "NSE", as_of)
        if nse is None:
            warnings.append(f"No NSE bhavcopy in the 7 days up to {as_of}")
        nse_rows = sources.parse_bhavcopy(nse[1], "NSE") if nse else []

        # Same trading day as NSE where possible, so the two exchanges' closes are comparable.
        bse = await fetch.fetch_bhavcopy(client, "BSE", nse[0] if nse else as_of)
        if bse is None:
            warnings.append(f"No BSE bhavcopy in the 7 days up to {as_of}")
        bse_rows = sources.parse_bhavcopy(bse[1], "BSE") if bse else []

        _, amfi_prices = sources.parse_amfi_navall(await fetch.fetch_text(client, fetch.AMFI_NAVALL))
    finally:
        if own_client:
            await client.aclose()

    prices = merge.pick_prices(nse_rows, bse_rows, amfi_prices)
    result = await store.upsert_prices(conn, prices)
    pruned = await store.prune_prices(conn, as_of - timedelta(days=PRICE_WINDOW_DAYS))

    return {
        "job": "refresh-prices",
        "as_of": as_of.isoformat(),
        "sources": {
            "nse_trade_date": nse[0].isoformat() if nse else None,
            "nse_rows": len(nse_rows),
            "bse_trade_date": bse[0].isoformat() if bse else None,
            "bse_rows": len(bse_rows),
            "amfi_navs": len(amfi_prices),
        },
        # "unmatched" = prices for ISINs not in the securities master (e.g. bonds, which aren't synced).
        "prices": {"candidates": len(prices), **result, "pruned": pruned},
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 1),
    }
