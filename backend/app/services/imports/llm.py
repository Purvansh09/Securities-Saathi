"""LLM fallback for files the rule-based mapper can't read (CLAUDE.md rule 7).

What Claude does here is deliberately narrow: look at the first rows of the sheet and say which
row is the header and which column holds each field. It never sees the whole file, never
produces numbers, and never writes anything. The answer is then checked against the real header
(table.mapping_from_names drops any column name that isn't actually there), the rows are parsed
by the same deterministic code as rule-mapped files, and the user still reviews every row before
anything reaches the ledger.

Privacy: only the first SAMPLE_ROWS rows are sent. The upload screen says so.
"""

import ssl

import anthropic
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.imports.table import ColumnMapping, mapping_from_names

MODEL = "claude-opus-5-5"
SAMPLE_ROWS = 15  # enough to get past an account-details preamble and see a few trades


class LlmColumnMapping(BaseModel):
    """Structured output: the header row index, and the exact header text for each field
    (null when the file has no such column)."""

    header_row: int = Field(description="0-based index, within the rows shown, of the column-header row")
    isin: str | None
    symbol: str | None = Field(description="Ticker / trading symbol / scrip code column")
    name: str | None = Field(description="Company or scheme name column")
    datetime: str | None = Field(description="Column holding date and time together")
    trade_date: str | None = Field(description="Column holding only the trade date")
    trade_time: str | None = Field(description="Column holding only the time of day")
    side: str | None = Field(description="Column saying buy or sell")
    quantity: str | None
    price: str | None = Field(description="Price per unit / NAV")
    value: str | None = Field(description="Total trade value, when there is no per-unit price")
    charges: str | None = Field(description="Brokerage/taxes/fees for the trade")
    status: str | None = Field(description="Order status column, e.g. Executed/Cancelled")
    segment: str | None = Field(description="Segment or instrument type, e.g. EQ / FO")


_INSTRUCTIONS = (
    "Below are the first rows of a trade-history export from an Indian broker or mutual fund "
    "platform, one row per line, cells separated by ' | '. Find the row that holds the column "
    "headers, then for each field give the exact header text (copied character for character) "
    "of the column that holds it, or null if the file has no such column. Don't guess: if no "
    "column clearly holds a field, use null."
)


def is_available() -> bool:
    return bool(get_settings().anthropic_api_key)


async def map_columns(grid: list[list[str]]) -> ColumnMapping | None:
    """A verified ColumnMapping, or None if Claude couldn't produce a usable one."""
    sample = "\n".join(f"{i}: " + " | ".join(row) for i, row in enumerate(grid[:SAMPLE_ROWS]))
    # The OS certificate store, not certifi's bundle: on a machine whose antivirus inspects
    # HTTPS, that's where its root certificate lives (same reasoning as securities_sync/fetch.py).
    client = anthropic.AsyncAnthropic(
        api_key=get_settings().anthropic_api_key,
        http_client=anthropic.DefaultAsyncHttpxClient(verify=ssl.create_default_context()),
        timeout=60.0,
    )
    async with client:
        response = await client.messages.parse(
            model=MODEL,
            max_tokens=4000,
            output_config={"effort": "low"},  # a lookup, not a reasoning problem
            output_format=LlmColumnMapping,
            messages=[{"role": "user", "content": f"{_INSTRUCTIONS}\n\n{sample}"}],
        )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        return None  # declined or cut off: fall back to "couldn't map this file"

    answer = response.parsed_output
    names = answer.model_dump(exclude={"header_row"})
    return mapping_from_names(grid, answer.header_row, names)
