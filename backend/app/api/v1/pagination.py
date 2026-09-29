"""Shared opaque-cursor pagination (API conventions in CLAUDE.md). The cursor is a
base64-encoded offset; list endpoints fetch limit + 1 rows to know whether another
page exists without a separate count query."""

import base64

from fastapi import HTTPException, status

ISIN_PATTERN = r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"  # must match the DB check constraint (Phase 1 migration)


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor") from exc
    if offset < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor")
    return offset
