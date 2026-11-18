"""Database side of the import flow: stage parsed rows, re-check a row after the user edits it,
and commit confirmed rows into the ledger.

Statuses (import_rows.status):
  CONFIRMED -- valid, will be committed
  PENDING   -- needs the user: a blocking problem, or a warning they must accept
  REJECTED  -- excluded (not a cash-market trade, or the user excluded it)
  COMMITTED -- now a transaction

An issue starting with WARNING_PREFIX (e.g. a likely duplicate) doesn't block commit once the
user confirms the row; any other issue does. Every query is scoped by the user_id from the
verified JWT (rule 13).
"""

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from app.services.imports.rows import IST, StagedRow
from app.services.ledger import LedgerViolation

WARNING_PREFIX = "Warning: "


def blocking(issues: list[str]) -> list[str]:
    return [i for i in issues if not i.startswith(WARNING_PREFIX)]


# --- security resolution --------------------------------------------------------------------


async def _candidates(conn: asyncpg.Connection, user_id: UUID, rows: list[StagedRow]) -> list[asyncpg.Record]:
    isins = sorted({r.isin for r in rows if r.isin})
    symbols = sorted({r.symbol for r in rows if r.symbol})
    names = sorted({r.name.lower() for r in rows if r.name})
    if not (isins or symbols or names):
        return []
    return await conn.fetch(
        """
        select id, isin, name, symbol, exchange, is_active,
               upper(metadata->>'nse_symbol') as nse_symbol,
               upper(metadata->>'bse_symbol') as bse_symbol,
               metadata->>'bse_code' as bse_code
        from public.securities
        where (created_by is null or created_by = $1)
          and (isin = any($2::text[])
               or upper(symbol) = any($3::text[])
               or upper(metadata->>'nse_symbol') = any($3::text[])
               or upper(metadata->>'bse_symbol') = any($3::text[])
               or metadata->>'bse_code' = any($3::text[])
               or lower(name) = any($4::text[]))
        """,
        user_id,
        isins,
        symbols,
        names,
    )


def _pick(matches: list[asyncpg.Record]) -> tuple[asyncpg.Record | None, bool]:
    """(chosen, ambiguous). Prefer active listings, then NSE as the primary exchange."""
    unique = list({m["id"]: m for m in matches}.values())
    if len(unique) <= 1:
        return (unique[0] if unique else None), False
    active = [m for m in unique if m["is_active"]] or unique
    nse = [m for m in active if m["exchange"] == "NSE"]
    pool = nse if len(nse) == 1 else active
    return (pool[0], False) if len(pool) == 1 else (None, True)


async def resolve_securities(conn: asyncpg.Connection, user_id: UUID, rows: list[StagedRow]) -> dict[int, UUID]:
    """Fill in row.isin and return {row_number: security_id}; add an issue where no single
    security matches. ISIN wins over symbol, symbol over name."""
    cands = await _candidates(conn, user_id, rows)
    by_isin = {c["isin"]: c for c in cands}
    by_symbol: dict[str, list] = defaultdict(list)
    by_name: dict[str, list] = defaultdict(list)
    for c in cands:
        for key in {c["symbol"] and c["symbol"].upper(), c["nse_symbol"], c["bse_symbol"], c["bse_code"]} - {None}:
            by_symbol[key].append(c)
        by_name[c["name"].lower()].append(c)

    resolved: dict[int, UUID] = {}
    for r in rows:
        if r.rejected_reason:
            continue
        chosen, ambiguous = (by_isin.get(r.isin), False) if r.isin else (None, False)
        if chosen is None and r.symbol:
            chosen, ambiguous = _pick(by_symbol.get(r.symbol, []))
        if chosen is None and not ambiguous and r.name:
            chosen, ambiguous = _pick(by_name.get(r.name.lower(), []))

        if chosen is not None:
            r.isin = chosen["isin"]
            resolved[r.row_number] = chosen["id"]
        elif ambiguous:
            r.issues.append(f"“{r.symbol or r.name}” matches more than one security: choose the right one")
        elif r.isin or r.symbol or r.name:
            label = r.isin or r.symbol or r.name
            r.issues.append(f"“{label}” isn't in the securities list: choose the security")
    return resolved


# --- duplicates -----------------------------------------------------------------------------


async def flag_duplicates(conn: asyncpg.Connection, user_id: UUID, rows: list[StagedRow], resolved: dict[int, UUID]) -> None:
    """Same security, side, quantity and price on the same IST day as a live ledger entry."""
    ids = sorted(set(resolved.values()))
    if not ids:
        return
    existing = await conn.fetch(
        """
        select security_id, txn_type, quantity, price, (executed_at at time zone 'Asia/Kolkata')::date as d
        from public.transactions
        where user_id = $1 and deleted_at is null and security_id = any($2::uuid[])
        """,
        user_id,
        ids,
    )
    seen = {(e["security_id"], e["txn_type"], e["quantity"], e["price"], e["d"]) for e in existing}
    for r in rows:
        sid = resolved.get(r.row_number)
        if sid and r.executed_at and r.quantity is not None and r.price is not None:
            key = (sid, r.txn_type, r.quantity, r.price, r.executed_at.astimezone(IST).date())
            if key in seen:
                r.issues.append(f"{WARNING_PREFIX}looks like a trade already in your ledger; confirm only if it's a separate trade")


async def is_duplicate(conn: asyncpg.Connection, user_id: UUID, row: dict) -> bool:
    """The same check as flag_duplicates, for one row after the user edits it."""
    if row["security_id"] is None or row["executed_at"] is None:
        return False
    return await conn.fetchval(
        """
        select exists (
          select 1 from public.transactions
          where user_id = $1 and deleted_at is null and security_id = $2 and txn_type = $3::public.txn_type
            and quantity = $4 and price = $5
            and (executed_at at time zone 'Asia/Kolkata')::date = ($6::timestamptz at time zone 'Asia/Kolkata')::date
        )
        """,
        user_id, row["security_id"], row["txn_type"], row["quantity"], row["price"], row["executed_at"],
    )


def initial_status(r: StagedRow) -> str:
    if r.rejected_reason:
        return "REJECTED"
    return "PENDING" if r.issues else "CONFIRMED"


# --- staging --------------------------------------------------------------------------------


async def stage_rows(conn: asyncpg.Connection, import_id: UUID, user_id: UUID, rows: list[StagedRow], resolved: dict[int, UUID]) -> None:
    await conn.executemany(
        """
        insert into public.import_rows
          (import_id, user_id, row_number, raw, isin, security_id, txn_type, quantity, price, charges,
           executed_at, status, issues)
        values ($1, $2, $3, $4, $5, $6, $7::public.txn_type, $8, $9, $10, $11, $12::public.import_row_status, $13)
        """,
        [
            (
                import_id, user_id, r.row_number, r.raw, r.isin, resolved.get(r.row_number), r.txn_type,
                r.quantity, r.price, r.charges, r.executed_at, initial_status(r),
                [r.rejected_reason] if r.rejected_reason else r.issues,
            )
            for r in rows
        ],
    )


# --- re-check after an edit -----------------------------------------------------------------


def validate_fields(row: dict, today: date) -> list[str]:
    """Blocking problems for a staged row's *current* values (after the user's edits)."""
    issues = []
    if row["security_id"] is None:
        issues.append("Choose the security")
    if row["txn_type"] not in ("BUY", "SELL"):
        issues.append("Choose buy or sell")
    if row["quantity"] is None or row["quantity"] <= 0:
        issues.append("Quantity must be more than 0")
    if row["price"] is None or row["price"] < 0:
        issues.append("Enter the price")
    if row["charges"] is None or row["charges"] < 0:
        issues.append("Charges can't be negative")
    executed_at: datetime | None = row["executed_at"]
    if executed_at is None:
        issues.append("Enter the trade date")
    elif executed_at.astimezone(IST).date() > today:
        issues.append("Trade date is in the future")
    return issues


# --- commit ---------------------------------------------------------------------------------


async def commit(conn: asyncpg.Connection, user_id: UUID, import_id: UUID, filename: str) -> int:
    """Move CONFIRMED rows into the ledger in one DB transaction. Idempotent: a row can only
    become a transaction once (transactions.import_row_id is unique), so re-running a commit
    that already happened inserts nothing. Returns the number of transactions created."""
    async with conn.transaction():
        rows = await conn.fetch(
            """
            select id, row_number, security_id, issues from public.import_rows
            where import_id = $1 and user_id = $2 and status = 'CONFIRMED'
            """,
            import_id,
            user_id,
        )
        rows = [r for r in rows if not blocking(list(r["issues"]))]
        security_ids = sorted({r["security_id"] for r in rows}, key=str)

        # Same lock as services/ledger.run_guarded, one per affected holding, taken in a fixed
        # order so two concurrent commits can't deadlock on each other.
        for sid in security_ids:
            await conn.execute(
                "select pg_advisory_xact_lock(hashtextextended($1 || $2, 0))", str(user_id), str(sid)
            )

        inserted = await conn.fetchval(
            """
            with ins as (
              insert into public.transactions
                (user_id, security_id, txn_type, quantity, price, charges, executed_at, notes, source, import_row_id)
              select r.user_id, r.security_id, r.txn_type, r.quantity, r.price, r.charges, r.executed_at,
                     'Imported from ' || $3 || ', row ' || r.row_number, 'IMPORT', r.id
              from public.import_rows r
              where r.id = any($1::uuid[]) and r.user_id = $2
              on conflict (import_row_id) do nothing
              returning 1
            )
            select count(*) from ins
            """,
            [r["id"] for r in rows],
            user_id,
            filename,
        )

        # Rule 5 for every holding the import touched: the running balance must never go negative,
        # including against trades already in the ledger. Any failure rolls the whole import back.
        failing = []
        for sid in security_ids:
            if not await conn.fetchval("select public.ledger_balance_ok($1, $2)", user_id, sid):
                failing.append(sid)
        if failing:
            labels = await conn.fetch(
                "select coalesce(symbol, name) as label from public.securities where id = any($1::uuid[]) order by 1",
                failing,
            )
            names = ", ".join(r["label"] for r in labels)
            raise LedgerViolation(
                f"These would sell more than you hold at some point: {names}. Import the earlier buys "
                "first (or add them by hand), or exclude those sell rows."
            )

        await conn.execute(
            "update public.import_rows set status = 'COMMITTED' where id = any($1::uuid[]) and user_id = $2",
            [r["id"] for r in rows],
            user_id,
        )
        await conn.execute(
            "update public.imports set status = 'COMMITTED', committed_at = now() where id = $1 and user_id = $2",
            import_id,
            user_id,
        )
    return inserted


def to_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)
