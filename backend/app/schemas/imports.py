"""Import (Excel upload) response and request models. Decimals as strings (rule 4)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.schemas.transaction import parse_decimal_str


class ImportRowOut(BaseModel):
    id: UUID
    row_number: int
    raw: dict[str, str]
    isin: str | None
    security_name: str | None
    security_symbol: str | None
    txn_type: Literal["BUY", "SELL"] | None
    quantity: str | None
    price: str | None
    charges: str | None
    executed_at: datetime | None
    status: Literal["PENDING", "CONFIRMED", "REJECTED", "COMMITTED"]
    issues: list[str]


class ImportSummary(BaseModel):
    id: UUID
    original_filename: str
    status: Literal["UPLOADED", "PARSING", "PARSED", "COMMITTED", "FAILED", "DISCARDED"]
    parser: Literal["RULES", "LLM"] | None
    profile: str | None
    error: str | None
    created_at: datetime
    committed_at: datetime | None
    counts: dict[str, int]  # rows by status


class ImportDetail(ImportSummary):
    rows: list[ImportRowOut]


class ImportList(BaseModel):
    items: list[ImportSummary]
    next_cursor: str | None


class ImportRowUpdate(BaseModel):
    """What the review screen can change on a staged row. The security is chosen by ISIN."""

    isin: str | None = None
    txn_type: Literal["BUY", "SELL"] | None = None
    quantity: str | None = None
    price: str | None = None
    charges: str | None = None
    executed_at: datetime | None = None
    status: Literal["CONFIRMED", "REJECTED", "PENDING"] | None = None

    @field_validator("quantity", "price", "charges")
    @classmethod
    def _decimal(cls, v: str | None, info) -> str | None:
        if v is not None:
            parse_decimal_str(v, field_name=info.field_name)
        return v


class CommitResult(BaseModel):
    import_id: UUID
    transactions_created: int
    status: str
