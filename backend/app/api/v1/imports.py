"""Excel/CSV import with a review step (CLAUDE.md rule 7).

  POST   /imports                         upload -> stored in Supabase Storage -> parsed into staging rows
  GET    /imports                         the caller's imports, newest first
  GET    /imports/{id}                    one import with every staged row
  PATCH  /imports/{id}/rows/{row_id}      fix or include/exclude a staged row
  POST   /imports/{id}/commit             move confirmed rows into the ledger (idempotent)
  DELETE /imports/{id}                    discard an uncommitted import (soft: status DISCARDED)

Nothing reaches `transactions` except through commit, and commit runs the same ledger guard as a
manual entry (rule 5).
"""

import hashlib
import re
from datetime import datetime
from decimal import Decimal
from pathlib import PurePath
from uuid import UUID, uuid4

import anthropic
import asyncpg
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status

from app.api.v1.pagination import decode_cursor, encode_cursor
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.imports import CommitResult, ImportDetail, ImportList, ImportRowOut, ImportRowUpdate, ImportSummary
from app.services.fifo import IST
from app.services.imports import llm, service, storage
from app.services.imports.rows import is_blank_or_total, parse_row
from app.services.imports.table import ImportFileError, data_rows, detect_mapping, read_grid
from app.services.ledger import LedgerViolation

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_BYTES = 5 * 1024 * 1024  # matches the Storage bucket's limit
_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_ROWS_QUERY = """
    select r.id, r.row_number, r.raw, r.isin, r.txn_type, r.quantity, r.price, r.charges,
           r.executed_at, r.status, r.issues, s.name as security_name, s.symbol as security_symbol
    from public.import_rows r
    left join public.securities s on s.id = r.security_id
    where r.import_id = $1 and r.user_id = $2
"""


def _today():
    return datetime.now(IST).date()


def _str(d: Decimal | None) -> str | None:
    return None if d is None else str(d)


def _row_out(r: asyncpg.Record) -> ImportRowOut:
    return ImportRowOut(
        id=r["id"], row_number=r["row_number"], raw={k: str(v) for k, v in (r["raw"] or {}).items()},
        isin=r["isin"], security_name=r["security_name"], security_symbol=r["security_symbol"],
        txn_type=r["txn_type"], quantity=_str(r["quantity"]), price=_str(r["price"]),
        charges=_str(r["charges"]), executed_at=r["executed_at"], status=r["status"], issues=list(r["issues"]),
    )


async def _summary_rows(conn: asyncpg.Connection, user_id: UUID, where: str, *args) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"""
        select i.id, i.original_filename, i.status, i.parser, i.profile, i.error, i.created_at, i.committed_at,
               coalesce((select jsonb_object_agg(status, n) from (
                   select status::text, count(*) as n from public.import_rows r
                   where r.import_id = i.id group by status) c), '{{}}'::jsonb) as counts
        from public.imports i
        where i.user_id = $1 {where}
        """,
        user_id,
        *args,
    )


def _summary(r: asyncpg.Record) -> dict:
    return {k: r[k] for k in ("id", "original_filename", "status", "parser", "profile", "error", "created_at", "committed_at")} | {
        "counts": dict(r["counts"] or {})
    }


async def _detail(conn: asyncpg.Connection, user_id: UUID, import_id: UUID) -> ImportDetail:
    found = await _summary_rows(conn, user_id, "and i.id = $2", import_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    rows = await conn.fetch(_ROWS_QUERY + " order by r.row_number", import_id, user_id)
    return ImportDetail(**_summary(found[0]), rows=[_row_out(r) for r in rows])


@router.post("", response_model=ImportDetail, status_code=status.HTTP_201_CREATED)
async def upload_import(
    file: UploadFile = File(...),
    authorization: str = Header(...),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ImportDetail:
    filename = PurePath(file.filename or "upload").name
    ext = PurePath(filename).suffix.lower()
    if ext not in _CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload a .csv or .xlsx file. For an old .xls file, open it and save as .xlsx.",
        )
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The file is larger than 5 MB.")
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file is empty.")

    # Hash computed here, never taken from the client: it's what stops the same file being
    # imported twice (imports_user_file_active_idx).
    sha256 = hashlib.sha256(content).hexdigest()
    existing = await conn.fetchrow(
        """select id, created_at from public.imports
           where user_id = $1 and file_sha256 = $2 and status not in ('DISCARDED', 'FAILED')""",
        user_id,
        sha256,
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You've already uploaded this exact file (on {existing['created_at'].astimezone(IST):%d %b %Y}). "
            "Open it from the list, or discard that import first.",
        )

    import_id = uuid4()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120]
    path = f"{user_id}/{import_id}/{safe_name}"  # the folder name is what Storage RLS checks
    token = authorization.split(" ", 1)[1]
    try:
        await storage.upload(token, path, content, _CONTENT_TYPES[ext])
    except storage.StorageError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await conn.execute(
        """insert into public.imports (id, user_id, original_filename, storage_path, file_sha256, status)
           values ($1, $2, $3, $4, $5, 'PARSING')""",
        import_id, user_id, filename, path, sha256,
    )

    try:
        grid = read_grid(content, filename)
        mapping, parser = detect_mapping(grid), "RULES"
        if mapping is None and llm.is_available():
            try:
                mapping, parser = await llm.map_columns(grid), "LLM"
            except anthropic.APIError as exc:
                raise ImportFileError(f"The AI column mapper is unavailable right now ({type(exc).__name__}). Try again later.") from exc
        if mapping is None:
            raise ImportFileError(
                "Couldn't recognise the columns in this file. It needs columns for the security (ISIN, symbol "
                "or name), trade date, buy/sell, quantity, and price or trade value."
            )

        today = _today()
        staged = [
            parse_row(n, values, raw, today)
            for n, values, raw in data_rows(grid, mapping)
            if not is_blank_or_total(values)
        ]
        if not staged:
            raise ImportFileError("No trades found below the header row.")

        resolved = await service.resolve_securities(conn, user_id, staged)
        await service.flag_duplicates(conn, user_id, staged, resolved)
        async with conn.transaction():
            await service.stage_rows(conn, import_id, user_id, staged, resolved)
            await conn.execute(
                """update public.imports set status = 'PARSED', parser = $3, profile = $4, parsed_at = now()
                   where id = $1 and user_id = $2""",
                import_id, user_id, parser, mapping.profile,
            )
    except ImportFileError as exc:
        # Kept as FAILED (not deleted) so the upload stays on record; the same file can be
        # uploaded again later because the uniqueness index ignores FAILED imports.
        await conn.execute(
            "update public.imports set status = 'FAILED', error = $3 where id = $1 and user_id = $2",
            import_id, user_id, str(exc),
        )

    return await _detail(conn, user_id, import_id)


@router.get("", response_model=ImportList)
async def list_imports(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ImportList:
    offset = decode_cursor(cursor)
    rows = await _summary_rows(
        conn, user_id, "and i.status <> 'DISCARDED' order by i.created_at desc limit $2 offset $3", limit + 1, offset
    )
    return ImportList(
        items=[ImportSummary(**_summary(r)) for r in rows[:limit]],
        next_cursor=encode_cursor(offset + limit) if len(rows) > limit else None,
    )


@router.get("/{import_id}", response_model=ImportDetail)
async def get_import(
    import_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ImportDetail:
    return await _detail(conn, user_id, import_id)


@router.patch("/{import_id}/rows/{row_id}", response_model=ImportRowOut)
async def update_row(
    import_id: UUID,
    row_id: UUID,
    payload: ImportRowUpdate,
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> ImportRowOut:
    async with conn.transaction():
        imp = await conn.fetchrow(
            "select status from public.imports where id = $1 and user_id = $2 for update", import_id, user_id
        )
        if imp is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
        if imp["status"] != "PARSED":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This import is {imp['status'].lower()}; rows can't be changed")

        row = await conn.fetchrow(
            "select * from public.import_rows where id = $1 and import_id = $2 and user_id = $3",
            row_id, import_id, user_id,
        )
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")

        new = dict(row)
        changes = payload.model_dump(exclude_unset=True)
        if "isin" in changes:
            sec = await conn.fetchrow(
                "select id, isin from public.securities where isin = $1 and (created_by is null or created_by = $2)",
                changes["isin"], user_id,
            )
            if sec is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown ISIN")
            new["isin"], new["security_id"] = sec["isin"], sec["id"]
        for key in ("txn_type", "executed_at"):
            if key in changes:
                new[key] = changes[key]
        for key in ("quantity", "price", "charges"):
            if key in changes:
                new[key] = service.to_decimal(changes[key])

        issues = service.validate_fields(new, _today())
        if not issues and await service.is_duplicate(conn, user_id, new):
            issues.append(f"{service.WARNING_PREFIX}looks like a trade already in your ledger; confirm only if it's a separate trade")

        wanted = changes.get("status")
        if wanted == "CONFIRMED" and service.blocking(issues):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Fix this row first: " + "; ".join(service.blocking(issues)),
            )
        if wanted:
            new_status = wanted
        elif service.blocking(issues):
            new_status = "PENDING"
        elif row["status"] == "REJECTED" or issues:
            new_status = row["status"]  # an edit alone doesn't re-include a row or accept a warning
        else:
            new_status = "CONFIRMED"

        await conn.execute(
            """update public.import_rows
               set isin = $3, security_id = $4, txn_type = $5::public.txn_type, quantity = $6, price = $7,
                   charges = $8, executed_at = $9, status = $10::public.import_row_status, issues = $11
               where id = $1 and user_id = $2""",
            row_id, user_id, new["isin"], new["security_id"], new["txn_type"], new["quantity"], new["price"],
            new["charges"], new["executed_at"], new_status, issues,
        )
        updated = await conn.fetchrow(_ROWS_QUERY + " and r.id = $3", import_id, user_id, row_id)
    return _row_out(updated)


@router.post("/{import_id}/commit", response_model=CommitResult)
async def commit_import(
    import_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> CommitResult:
    imp = await conn.fetchrow(
        "select status, original_filename from public.imports where id = $1 and user_id = $2", import_id, user_id
    )
    if imp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    if imp["status"] == "COMMITTED":
        # Idempotent: committing twice is a no-op, not an error and never a duplicate.
        return CommitResult(import_id=import_id, transactions_created=0, status="COMMITTED")
    if imp["status"] != "PARSED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This import is {imp['status'].lower()}")

    confirmed = await conn.fetchval(
        "select count(*) from public.import_rows where import_id = $1 and user_id = $2 and status = 'CONFIRMED'",
        import_id, user_id,
    )
    if not confirmed:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No confirmed rows to import")

    try:
        created = await service.commit(conn, user_id, import_id, imp["original_filename"])
    except LedgerViolation as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return CommitResult(import_id=import_id, transactions_created=created, status="COMMITTED")


@router.delete("/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_import(
    import_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(get_db),
) -> None:
    result = await conn.execute(
        """update public.imports set status = 'DISCARDED'
           where id = $1 and user_id = $2 and status in ('PARSED', 'FAILED', 'UPLOADED', 'PARSING')""",
        import_id, user_id,
    )
    if result == "UPDATE 0":
        exists = await conn.fetchval("select status from public.imports where id = $1 and user_id = $2", import_id, user_id)
        if exists is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This import is already committed. Delete its transactions individually instead.",
        )
