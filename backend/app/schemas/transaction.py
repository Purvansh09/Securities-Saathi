import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, field_validator

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")  # must match the DB check constraint


class TxnType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


def parse_decimal_str(value: str, *, field_name: str) -> Decimal:
    """Every money/quantity field crosses the API boundary as a string (CLAUDE.md rule 4) --
    this is the one place that turns such a string into a real Decimal, rejecting anything
    a client might slip in as a float-shaped surprise (NaN, Infinity, scientific notation)."""
    try:
        d = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a valid decimal string") from exc
    if not d.is_finite():
        raise ValueError(f"{field_name} must be a finite number")
    return d


class TransactionCreate(BaseModel):
    isin: str
    txn_type: TxnType
    quantity: str
    price: str
    charges: str = "0"
    executed_at: datetime
    notes: str | None = None

    @field_validator("isin")
    @classmethod
    def _isin_format(cls, v: str) -> str:
        if not _ISIN_RE.fullmatch(v):
            raise ValueError("isin is not a valid ISIN format")
        return v

    @field_validator("quantity")
    @classmethod
    def _quantity(cls, v: str) -> str:
        if parse_decimal_str(v, field_name="quantity") <= 0:
            raise ValueError("quantity must be greater than 0")
        return v

    @field_validator("price")
    @classmethod
    def _price(cls, v: str) -> str:
        if parse_decimal_str(v, field_name="price") < 0:
            raise ValueError("price must be >= 0")
        return v

    @field_validator("charges")
    @classmethod
    def _charges(cls, v: str) -> str:
        if parse_decimal_str(v, field_name="charges") < 0:
            raise ValueError("charges must be >= 0")
        return v

    def decimals(self) -> tuple[Decimal, Decimal, Decimal]:
        return Decimal(self.quantity), Decimal(self.price), Decimal(self.charges)


class TransactionUpdate(BaseModel):
    """Corrections only -- quantity, price, charges, timing, notes. Not txn_type or isin:
    changing what was bought/sold is a new transaction, not an edit to this one."""

    quantity: str | None = None
    price: str | None = None
    charges: str | None = None
    executed_at: datetime | None = None
    notes: str | None = None

    @field_validator("quantity")
    @classmethod
    def _quantity(cls, v: str | None) -> str | None:
        if v is not None and parse_decimal_str(v, field_name="quantity") <= 0:
            raise ValueError("quantity must be greater than 0")
        return v

    @field_validator("price")
    @classmethod
    def _price(cls, v: str | None) -> str | None:
        if v is not None and parse_decimal_str(v, field_name="price") < 0:
            raise ValueError("price must be >= 0")
        return v

    @field_validator("charges")
    @classmethod
    def _charges(cls, v: str | None) -> str | None:
        if v is not None and parse_decimal_str(v, field_name="charges") < 0:
            raise ValueError("charges must be >= 0")
        return v


class TransactionOut(BaseModel):
    id: UUID
    isin: str
    txn_type: TxnType
    quantity: str
    price: str
    charges: str
    executed_at: datetime
    notes: str | None
    source: str
    created_at: datetime
    updated_at: datetime


class TransactionList(BaseModel):
    items: list[TransactionOut]
    next_cursor: str | None
