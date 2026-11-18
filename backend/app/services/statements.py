"""Downloadable statements: holdings and realised P/L, as Excel (.xlsx) and PDF.

The numbers come from the same functions as the API (FIFO, valuation, XIRR, tax engine), so a
statement can never disagree with the screen. Excel cells hold real numbers (formatted with
Indian digit grouping) so the user can work with them; the PDF is for printing and sharing.
This module only lays out data it is given; it computes nothing new.

PDF text uses the built-in Helvetica font, which has no rupee sign, so amounts are written "Rs".
"""

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

APP = "Securities Saathi"
DISCLAIMER = (
    "Generated from the transactions recorded in Securities Saathi. Valuations use the latest available "
    "closing price or NAV. Tax figures are an estimate from published rules, not tax advice; check them "
    "with a tax professional or the Income Tax Department before filing."
)

# Excel: Indian digit grouping (12,34,56,789.00). Excel allows at most two conditions per format,
# so lakhs and crores take the two conditional sections and everything else (incl. negatives)
# falls through to plain thousands grouping.
_INR_FMT = '[>=10000000]##\\,##\\,##\\,##0.00;[>=100000]##\\,##\\,##0.00;##,##0.00'
_QTY_FMT = "#,##0.####"
_PCT_FMT = '0.00"%";[Red]-0.00"%"'


@dataclass
class Column:
    title: str
    kind: str = "text"  # text | inr | qty | pct (signed change) | share (unsigned weight) | date | int
    width: float = 14  # Excel character width; PDF widths are scaled from these


@dataclass
class Table:
    title: str
    columns: list[Column]
    rows: list[list]
    totals: list | None = None


@dataclass
class Statement:
    title: str
    subtitle: str
    owner: str
    summary: list[tuple[str, str]]  # label, formatted value (shown above the tables)
    tables: list[Table]
    notes: list[str] = field(default_factory=list)
    filename: str = "statement"


# --- formatting (PDF and summary lines) ------------------------------------------------------

def inr(value: Decimal | None, *, sign: bool = False) -> str:
    """Rs 12,34,567.89 -- Indian grouping, rounded half-even to paise at the presentation layer (rule 4)."""
    if value is None:
        return "-"
    q = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    neg = q < 0
    whole, frac = f"{abs(q):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join(groups + [tail]) if groups else tail
    prefix = "-" if neg else ("+" if sign and q > 0 else "")
    return f"{prefix}Rs {text}.{frac}"


def pct(value: Decimal | None, *, sign: bool = True) -> str:
    if value is None:
        return "-"
    q = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    if q == 0:
        q = abs(q)  # no "-0.00%"
    return f"{'+' if sign and q > 0 else ''}{q}%"


def qty(value: Decimal) -> str:
    return f"{Decimal(value).normalize():,f}"


def _cell_text(value, kind: str, *, blank: str = "-") -> str:
    if value is None or value == "":
        return blank
    if isinstance(value, str):  # e.g. "Slab" in a money column
        return value
    if kind == "inr":
        return inr(value).replace("Rs ", "")
    if kind == "qty":
        return qty(value)
    if kind == "pct":
        return pct(value)
    if kind == "share":  # a weight or share of a total: never signed
        return pct(value, sign=False)
    if kind == "date":
        return value.strftime("%d %b %Y") if isinstance(value, (date, datetime)) else str(value)
    return str(value)


# --- Excel -----------------------------------------------------------------------------------

def to_xlsx(st: Statement) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\\/*?:\[\]]", "-", st.title)[:31]  # characters Excel forbids in sheet names
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="E8EEFC")

    ws.append([f"{APP} - {st.title}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([st.subtitle])
    ws.append([f"Prepared for {st.owner}" if st.owner else ""])
    ws.append([])
    for label, value in st.summary:
        ws.append([label, value])
        ws.cell(ws.max_row, 1).font = bold
    ws.append([])

    widths: dict[int, float] = {}
    for table in st.tables:
        ws.append([table.title])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
        ws.append([c.title for c in table.columns])
        header_row = ws.max_row
        for i, _ in enumerate(table.columns, start=1):
            cell = ws.cell(header_row, i)
            cell.font = bold
            cell.fill = head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        for row in table.rows + ([table.totals] if table.totals else []):
            ws.append([v if v is not None else "" for v in row])
            r = ws.max_row
            for i, col in enumerate(table.columns, start=1):
                cell = ws.cell(r, i)
                if col.kind == "inr":
                    cell.number_format = _INR_FMT
                elif col.kind == "qty":
                    cell.number_format = _QTY_FMT
                elif col.kind in ("pct", "share"):
                    cell.number_format = _PCT_FMT
                elif col.kind == "date":
                    cell.number_format = "dd mmm yyyy"
                if table.totals and row is table.totals:
                    cell.font = bold
        for i, col in enumerate(table.columns, start=1):
            widths[i] = max(widths.get(i, 0), col.width)
        ws.append([])

    for i, w in widths.items():
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.column_dimensions["A"].width = max(widths.get(1, 0), 28)

    for note in st.notes + [DISCLAIMER]:
        ws.append([note])
        ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=False)
        ws.cell(ws.max_row, 1).font = Font(italic=True, size=9, color="5D6470")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- PDF -------------------------------------------------------------------------------------

def _latin1(text: str) -> str:
    """Helvetica is Latin-1 only; replace the few characters that aren't (curly quotes, rupee sign, dashes)."""
    swaps = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "₹": "Rs ", "…": "..."}
    for a, b in swaps.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


class _Pdf(FPDF):
    def __init__(self, footer_text: str):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.footer_text = footer_text
        self.set_auto_page_break(auto=True, margin=14)
        self.set_margins(10, 10, 10)

    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", size=7)
        self.set_text_color(110)
        self.cell(0, 5, _latin1(f"{self.footer_text}   |   Page {self.page_no()}/{{nb}}"), align="C")


def to_pdf(st: Statement) -> bytes:
    pdf = _Pdf(f"{APP} - {st.title} - {st.subtitle}")
    pdf.alias_nb_pages()
    pdf.add_page()
    usable = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, _latin1(f"{APP} - {st.title}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(90)
    pdf.cell(0, 5, _latin1(st.subtitle + (f"   |   Prepared for {st.owner}" if st.owner else "")), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0)
    pdf.ln(2)

    # Summary as a two-column grid of label/value pairs.
    pdf.set_font("Helvetica", size=9)
    col_w = usable / 4
    for i, (label, value) in enumerate(st.summary):
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(90)
        x, y = pdf.get_x(), pdf.get_y()
        pdf.cell(col_w, 4.5, _latin1(label))
        pdf.set_xy(x, y + 4.5)
        pdf.set_text_color(0)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(col_w, 5.5, _latin1(value))
        if i % 4 == 3 or i == len(st.summary) - 1:
            pdf.set_xy(pdf.l_margin, y + 12)
        else:
            pdf.set_xy(x + col_w, y)
    pdf.ln(2)

    for table in st.tables:
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, _latin1(table.title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        total_w = sum(c.width for c in table.columns)
        widths = [usable * c.width / total_w for c in table.columns]
        aligns = ["L" if c.kind in ("text", "date") else "R" for c in table.columns]

        def header():
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_fill_color(232, 238, 252)
            for c, w, a in zip(table.columns, widths, aligns):
                pdf.cell(w, 6, _latin1(c.title), border="B", align=a, fill=True)
            pdf.ln()

        header()
        rows = table.rows + ([table.totals] if table.totals else [])
        for idx, row in enumerate(rows):
            if pdf.get_y() > pdf.h - 18:
                pdf.add_page()
                header()
            is_total = table.totals is not None and idx == len(rows) - 1
            pdf.set_font("Helvetica", "B" if is_total else "", 7)
            for value, c, w, a in zip(row, table.columns, widths, aligns):
                text = _latin1(_cell_text(value, c.kind, blank="" if is_total else "-"))
                # Trim to fit rather than wrap, so every row stays one line high.
                while text and pdf.get_string_width(text) > w - 1.5:
                    text = text[:-2] + "~" if len(text) > 2 else ""
                pdf.cell(w, 5, text, border="T" if is_total else 0, align=a)
            pdf.ln()
        pdf.ln(3)

    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(90)
    for note in st.notes + [DISCLAIMER]:
        pdf.multi_cell(0, 3.8, _latin1(note), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())
