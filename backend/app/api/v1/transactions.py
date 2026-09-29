"""POST/GET/PATCH/DELETE /transactions -- the ledger, exposed.

Every write goes through services.ledger.run_guarded (CLAUDE.md rule 5):
lock on (user_id, security_id), apply the change, check ledger_balance_ok,
roll back the whole thing to a clean 422 if it would go negative. Every
query -- reads included -- filters deleted_at is null (rule 2) and scopes
by the user_id from the verified JWT, never anything from the request
(rule 13); DELETE is a soft delete, never a real one.
"""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.v1.pagination import ISIN_PATTERN
from app.api.v1.pagination import decode_cursor as _decode_cursor
from app.api.v1.pagination import encode_cursor as _encode_cursor
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.transaction import (
    TransactionCreate,
    TransactionList,
    TransactionOut,
    TransactionUpdate,
    TxnType,
    parse_decimal_str,
)
from app.services.ledger import LedgerViolation, run_guarded

router = APIRouter(prefix="/transactions", tags=["transactions"])

_TXN_OUT_COLUMNS = "id, txn_type, quantity, price, charges, executed_at, notes, source, created_at, updated_at"

# Same columns, explicitly qualified with the "t" alias -- used in every query that joins
# transactions to securities, since both tables have an "id" column and a bare "id" in the
# select list would be ambiguous.
_TXN_SELECT_JOINED = (
    "t.id, t.txn_type, t.quantity, t.price, t.charges, t.executed_at, "
    "t.notes, t.source, t.created_at, t.updated_at"
)


def _row_to_out(row: dict) -> TransactionOut:
    data = dict(row)
    data["quantity"] = str(data["quantity"])
    data["price"] = str(data["price"])
    data["charges"] = str(data["charges"])
    return TransactionOut(**data)


async def _resolve_visible_security_id(conn: asyncpg.Connection, isin: str, user_id: UUID) -> UUID:
    security_id = await conn.fetchval(
        "select id from public.securities where isin = $1 and (created_by is null or created_by = $2)",
        isin,
        user_id,
    )
    if security_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Security not found")
    return security_id


async def _get_owned_transaction(conn: asyncpg.Connection, txn_id: UUID, user_id: UUID) -> dict:
    """Fetches an existing, non-deleted transaction the caller owns, or 404s.
    Used before PATCH/DELETE to find the security_id to lock on -- the lock has to be
    taken before the mutating statement, so this lookup happens outside run_guarded."""
    row = await conn.fetchrow(
        "select id, security_id from public.transactions where id = $1 and user_id = $2 and deleted_at is null",
        txn_id,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return dict(row)


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    payload: TransactionCreate,
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> TransactionOut:
    security_id = await _resolve_visible_security_id(conn, payload.isin, user_id)
    quantity, price, charges = payload.decimals()

    async def mutate() -> dict:
        row = await conn.fetchrow(
            f"""
            insert into public.transactions
                (user_id, security_id, txn_type, quantity, price, charges, executed_at, notes)
            values ($1, $2, $3, $4, $5, $6, $7, $8)
            returning {_TXN_OUT_COLUMNS}
            """,
            user_id,
            security_id,
            payload.txn_type.value,
            quantity,
            price,
            charges,
            payload.executed_at,
            payload.notes,
        )
        return dict(row)

    try:
        row = await run_guarded(conn, user_id, security_id, mutate)
    except LedgerViolation as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    row["isin"] = payload.isin
    return _row_to_out(row)


@router.get("", response_model=TransactionList)
async def list_transactions(
    isin: str | None = Query(default=None, pattern=ISIN_PATTERN),
    txn_type: TxnType | None = Query(default=None, alias="type"),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> TransactionList:
    offset = _decode_cursor(cursor)

    rows = await conn.fetch(
        f"""
        select {_TXN_SELECT_JOINED}, s.isin
        from public.transactions t
        join public.securities s on s.id = t.security_id
        where t.user_id = $1
          and t.deleted_at is null
          and ($2::text is null or s.isin = $2)
          and ($3::public.txn_type is null or t.txn_type = $3)
          and ($4::timestamptz is null or t.executed_at >= $4::timestamptz)
          and ($5::timestamptz is null or t.executed_at <= $5::timestamptz)
        order by t.executed_at desc, t.created_at desc
        limit $6 offset $7
        """,
        user_id,
        isin,
        txn_type.value if txn_type else None,
        from_,
        to,
        limit + 1,
        offset,
    )

    has_more = len(rows) > limit
    page = rows[:limit]
    return TransactionList(
        items=[_row_to_out(dict(r)) for r in page],
        next_cursor=_encode_cursor(offset + limit) if has_more else None,
    )


@router.get("/{transaction_id}", response_model=TransactionOut)
async def get_transaction(
    transaction_id: UUID = Path(...),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> TransactionOut:
    row = await conn.fetchrow(
        f"""
        select {_TXN_SELECT_JOINED}, s.isin
        from public.transactions t
        join public.securities s on s.id = t.security_id
        where t.id = $1 and t.user_id = $2 and t.deleted_at is null
        """,
        transaction_id,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return _row_to_out(dict(row))


@router.patch("/{transaction_id}", response_model=TransactionOut)
async def update_transaction(
    payload: TransactionUpdate,
    transaction_id: UUID = Path(...),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> TransactionOut:
    existing = await _get_owned_transaction(conn, transaction_id, user_id)
    security_id = existing["security_id"]

    fields = payload.model_dump(exclude_unset=True)
    decimal_fields = {"quantity", "price", "charges"}
    values_by_field: dict[str, object] = {}
    for key, value in fields.items():
        if key in decimal_fields:
            values_by_field[key] = parse_decimal_str(value, field_name=key)
        else:
            values_by_field[key] = value

    async def mutate() -> dict:
        if not values_by_field:
            row = await conn.fetchrow(
                f"select {_TXN_OUT_COLUMNS} from public.transactions where id = $1", transaction_id
            )
            return dict(row)

        set_clauses = []
        args: list = []
        for i, (key, value) in enumerate(values_by_field.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            args.append(value)
        args.append(transaction_id)
        args.append(user_id)

        row = await conn.fetchrow(
            f"""
            update public.transactions
            set {", ".join(set_clauses)}
            where id = ${len(args) - 1} and user_id = ${len(args)} and deleted_at is null
            returning {_TXN_OUT_COLUMNS}
            """,
            *args,
        )
        if row is None:
            # Row existed a moment ago (_get_owned_transaction) but vanished/changed
            # owner/got deleted concurrently -- surface as a conflict, not a silent no-op.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Transaction changed concurrently")
        return dict(row)

    try:
        row = await run_guarded(conn, user_id, security_id, mutate)
    except LedgerViolation as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    isin = await conn.fetchval("select isin from public.securities where id = $1", security_id)
    row["isin"] = isin
    return _row_to_out(row)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    transaction_id: UUID = Path(...),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    existing = await _get_owned_transaction(conn, transaction_id, user_id)
    security_id = existing["security_id"]

    async def mutate() -> None:
        # Soft delete (CLAUDE.md rule 2): the row and its history stay, just excluded
        # from the live ledger from now on. The trigger on transactions snapshots the
        # pre-delete row into transaction_revisions automatically.
        await conn.execute(
            "update public.transactions set deleted_at = now() where id = $1 and user_id = $2 and deleted_at is null",
            transaction_id,
            user_id,
        )

    try:
        await run_guarded(conn, user_id, security_id, mutate)
    except LedgerViolation as exc:
        # Deleting an old BUY that a later SELL now depends on is exactly the case
        # this guards against -- the delete itself gets rolled back.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
