"""Database writes for the sync. Each function runs in one DB transaction: the batch is
bulk-copied into a temp table, then merged into the real table with a single set-based
statement. Either the whole batch lands or none of it does.

The securities master and prices are shared reference data, not user data, so these writes
aren't scoped by user_id (rule 13 applies to user tables). Custom securities are the one
user-owned part, and the only thing the sync does to them is promote them when the same ISIN
appears in an official list (CLAUDE.md "Securities sync")."""

import json
from datetime import date

import asyncpg

from app.services.securities_sync.sources import ParsedPrice, ParsedSecurity, SourceFormatError

# Types whose presence in the NSE/AMFI lists is authoritative. Bonds aren't synced (they come
# from the seed and user-added custom rows), so they're never deactivated by absence.
SYNCED_TYPES = ["STOCK", "ETF", "MF"]

# If one run would deactivate more than this share of the active synced rows, something is wrong
# with the input rather than with the market. Abort instead.
MAX_DEACTIVATE_SHARE = 0.05


async def upsert_securities(conn: asyncpg.Connection, records: list[ParsedSecurity]) -> dict[str, int]:
    async with conn.transaction():
        await conn.execute(
            """
            create temp table _sync_sec (
              isin text primary key, name text not null, type text not null, symbol text,
              exchange text not null, sector text, metadata text not null
            ) on commit drop
            """
        )
        # metadata goes in as text and is cast in SQL: binary COPY can't use the pool's
        # text-based jsonb codec (core/db.py).
        await conn.copy_records_to_table(
            "_sync_sec",
            records=[(r.isin, r.name, r.type, r.symbol, r.exchange, r.sector, json.dumps(r.metadata)) for r in records],
        )

        promoted = await conn.fetchval(
            "select count(*) from public.securities s join _sync_sec t using (isin) where s.is_custom"
        )

        counts = await conn.fetchrow(
            """
            with up as (
              insert into public.securities as s
                (isin, name, type, symbol, exchange, sector, metadata,
                 is_active, is_custom, created_by, last_synced_at)
              select isin, name, type::public.security_type, symbol, exchange, sector, metadata::jsonb,
                     true, false, null, now()
              from _sync_sec
              on conflict (isin) do update set
                -- ETF list names are often just the index ("Nifty 50"), so an existing ETF
                -- keeps its better name. A promoted custom row takes the official one.
                name           = case when s.type = 'ETF' and not s.is_custom then s.name else excluded.name end,
                type           = excluded.type,
                symbol         = excluded.symbol,
                exchange       = excluded.exchange,
                sector         = coalesce(excluded.sector, s.sector),
                metadata       = s.metadata || excluded.metadata,   -- merge, never drop existing keys
                is_active      = true,
                is_custom      = false,                             -- promotion (CLAUDE.md)
                created_by     = null,
                last_synced_at = now(),
                updated_at     = now()
              returning (xmax = 0) as inserted
            )
            select count(*) filter (where inserted) as inserted,
                   count(*) filter (where not inserted) as updated
            from up
            """
        )

        # Vanished from the official lists -> inactive, never deleted (CLAUDE.md). Only rows the
        # lists are authoritative for: NSE/AMFI rows of synced types. BSE-only rows come from a
        # daily trade file, where absence just means "didn't trade today".
        active_synced = await conn.fetchval(
            """
            select count(*) from public.securities
            where is_active and not is_custom and exchange in ('NSE', 'AMFI') and type::text = any($1::text[])
            """,
            SYNCED_TYPES,
        )
        deactivated = await conn.fetchval(
            """
            with d as (
              update public.securities s
              set is_active = false, updated_at = now()
              where s.is_active and not s.is_custom
                and s.exchange in ('NSE', 'AMFI')
                and s.type::text = any($1::text[])
                and not exists (select 1 from _sync_sec t where t.isin = s.isin)
              returning 1
            )
            select count(*) from d
            """,
            SYNCED_TYPES,
        )
        if active_synced and deactivated > active_synced * MAX_DEACTIVATE_SHARE:
            # Raising inside the transaction rolls back the upsert as well.
            raise SourceFormatError(
                f"Sync would deactivate {deactivated} of {active_synced} securities; aborting without changes"
            )

    return {"inserted": counts["inserted"], "updated": counts["updated"], "promoted": promoted, "deactivated": deactivated}


async def upsert_prices(conn: asyncpg.Connection, prices: list[ParsedPrice]) -> dict[str, int]:
    async with conn.transaction():
        await conn.execute(
            """
            create temp table _sync_px (isin text, price_date date, price numeric(20, 6), source text)
            on commit drop
            """
        )
        await conn.copy_records_to_table(
            "_sync_px", records=[(p.isin, p.price_date, p.price, p.source) for p in prices]
        )
        row = await conn.fetchrow(
            """
            with up as (
              insert into public.security_prices (security_id, price_date, price, source)
              select s.id, p.price_date, p.price, p.source
              from _sync_px p
              join public.securities s on s.isin = p.isin
              -- NAV only for mutual funds: an ETF is valued at its traded price, not its NAV.
              where p.source <> 'AMFI' or s.type = 'MF'
              on conflict (security_id, price_date) do update
                set price = excluded.price, source = excluded.source
                where security_prices.source <> 'MANUAL'            -- a hand-entered price wins
              returning (xmax = 0) as inserted
            )
            select count(*) filter (where inserted) as inserted,
                   count(*) filter (where not inserted) as updated
            from up
            """
        )
    return {"inserted": row["inserted"], "updated": row["updated"], "unmatched": len(prices) - row["inserted"] - row["updated"]}


async def prune_prices(conn: asyncpg.Connection, keep_from: date) -> int:
    """Keep full price history only for securities someone has traded; for the other ~20,000 keep
    a short window. At ~20,000 prices a day, full history for everything would reach Supabase's
    free-tier 500 MB within about a year. Valuation only needs the latest price.

    A security's most recent price is never pruned, however old: some schemes publish NAVs
    rarely, or stopped at maturity, and their last NAV is still the only valuation there is."""
    return await conn.fetchval(
        """
        with d as (
          delete from public.security_prices sp
          where sp.price_date < $1
            and sp.source <> 'MANUAL'
            and not exists (select 1 from public.transactions t where t.security_id = sp.security_id)
            and sp.price_date < (select max(x.price_date) from public.security_prices x
                                 where x.security_id = sp.security_id)
          returning 1
        )
        select count(*) from d
        """,
        keep_from,
    )
