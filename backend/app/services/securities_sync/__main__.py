"""CLI for the daily jobs, meant for a scheduler (Task Scheduler, cron, GitHub Actions).
Exits non-zero on failure so the scheduler reports it."""

import argparse
import asyncio
import json
import sys
from datetime import date

import asyncpg

from app.core.config import get_settings
from app.core.db import init_connection
from app.services.securities_sync.jobs import run_price_refresh, run_sync


async def _main(job: str, as_of: date | None) -> int:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        await init_connection(conn)
        if job in ("sync", "all"):
            print(json.dumps(await run_sync(conn), indent=2))
        if job in ("prices", "all"):
            print(json.dumps(await run_price_refresh(conn, as_of), indent=2))
    finally:
        await conn.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.services.securities_sync")
    parser.add_argument("job", choices=["sync", "prices", "all"], nargs="?", default="all")
    parser.add_argument("--date", type=date.fromisoformat, help="price date (default: today in IST)")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_main(args.job, args.date)))
    except Exception as exc:  # noqa: BLE001 -- report any failure clearly to the scheduler
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
