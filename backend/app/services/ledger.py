"""The ledger write guard (CLAUDE.md rule 5).

Every create, edit, or soft-delete of a transaction can change whether some
BUY/SELL sequence for a (user, security) pair ever goes negative -- not just
the row being touched: deleting an old BUY can retroactively oversell a
later SELL. So every mutation runs through the same three-step guard:

  1. Take an advisory lock scoped to (user_id, security_id), so two requests
     racing on the same holding serialize instead of both passing the check
     against a balance the other is about to invalidate.
  2. Apply the mutation.
  3. Call ledger_balance_ok(user_id, security_id) (see the Phase 1 migration),
     which re-walks the whole running balance for that pair, not just the
     one row. If it's false, the whole DB transaction -- lock, mutation, and
     any partial writes -- rolls back automatically.

This was exercised directly against Postgres earlier (a real oversell
correctly flipped ledger_balance_ok to false, and a soft-deleted correcting
transaction flipped it back) before any of this endpoint code existed.
"""

from typing import Awaitable, Callable, TypeVar
from uuid import UUID

import asyncpg

T = TypeVar("T")


class LedgerViolation(Exception):
    """Raised when a mutation would leave the running balance negative at some point
    in time. Callers turn this into an HTTP 422 -- it's an expected user-facing
    outcome (e.g. "you tried to sell more than you hold"), not a server error."""


async def run_guarded(
    conn: asyncpg.Connection,
    user_id: UUID,
    security_id: UUID,
    mutate: Callable[[], Awaitable[T]],
) -> T:
    async with conn.transaction():
        # pg_advisory_xact_lock is held until this DB transaction ends (commit or
        # rollback), which is why it has to be taken inside this `async with`, not
        # before it -- a lock taken outside a transaction releases immediately.
        await conn.execute(
            "select pg_advisory_xact_lock(hashtextextended($1 || $2, 0))",
            str(user_id),
            str(security_id),
        )
        result = await mutate()

        balance_ok = await conn.fetchval(
            "select public.ledger_balance_ok($1, $2)", user_id, security_id
        )
        if not balance_ok:
            raise LedgerViolation(
                "This would leave the holding negative at some point in time."
            )
        return result
