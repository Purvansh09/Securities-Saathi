"""GET /tax/capital-gains and the downloadable statements, on the hand-worked portfolio from
test_portfolio.py (TCS: 4 of 10 sold, gain 782; INFY: bought and sold, gain 100; both sells in
Feb 2026, so financial year 2025-26, both short term)."""

import io
from decimal import Decimal as D

import openpyxl
import pytest
from fastapi.testclient import TestClient

import app.api.v1.reports as reports_module
import app.api.v1.tax as tax_module
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.main import app
from tests.test_portfolio import FIXED_USER_ID, PORTFOLIO_ROWS, TODAY


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, query, *args):
        return self.rows

    async def fetchval(self, query, *args):
        return "Test Investor"  # profiles.full_name


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(reports_module, "today_ist", lambda: TODAY)
    monkeypatch.setattr(tax_module, "today_ist", lambda: TODAY)

    async def _db():
        yield FakeConnection(PORTFOLIO_ROWS)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user_id] = lambda: FIXED_USER_ID
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_tax_report_defaults_to_latest_year_with_sales(client):
    body = client.get("/api/v1/tax/capital-gains").json()
    assert body["fy"] == "2025-26" and body["years_available"] == ["2025-26"]
    assert {(l["symbol"], l["term"], l["rule"], l["rate_pct"]) for l in body["lots"]} == {
        ("TCS", "SHORT", "Equity STCG (s.111A)", "20"),
        ("INFY", "SHORT", "Equity STCG (s.111A)", "20"),
    }
    s = body["summary"]
    # 782 + 100 = 882 of equity STCG at 20% = 176.40, plus 4% cess = 7.056.
    assert D(s["special_rate_tax"]) == D("176.4")
    assert D(s["total_estimated_tax"]) == D("183.456")


def test_tax_report_for_a_year_without_sales_is_empty(client):
    body = client.get("/api/v1/tax/capital-gains?fy=2026-27").json()
    assert body["lots"] == [] and body["buckets"] == [] and D(body["summary"]["total_estimated_tax"]) == 0


def _sheet_text(content: bytes) -> list[list]:
    ws = openpyxl.load_workbook(io.BytesIO(content)).active
    return [[c for c in row] for row in ws.iter_rows(values_only=True)]


def test_holdings_statement_xlsx(client):
    resp = client.get("/api/v1/reports/holdings?format=xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="holdings-statement-2026-09-29.xlsx"'
    rows = _sheet_text(resp.content)
    assert rows[0][0] == "Securities Saathi - Holdings statement"
    assert rows[2][0] == "Prepared for Test Investor"
    total = next(r for r in rows if r[0] == "Total" and r[5] is not None)
    # Same figures as /portfolio/summary: invested 28802, value 30608.975.
    assert D(str(total[5])) == D("28802") and D(str(total[8])) == D("30608.975")
    assert {r[0] for r in rows} >= {"TCS", "HDFC Large Cap Fund", "7.18% GS 2033"}  # INFY is closed: not held


def test_realised_statement_xlsx_lists_lots_with_tax_rule(client):
    rows = _sheet_text(client.get("/api/v1/reports/realised?fy=2025-26&format=xlsx").content)
    lots = [r for r in rows if r[0] in ("TCS", "INFY")]
    assert len(lots) == 2 and all(r[10] == "Equity STCG (s.111A)" for r in lots)
    assert any(r[0] == "Equity STCG @ 20%" for r in rows)  # the tax computation table


@pytest.mark.parametrize("path", ["/api/v1/reports/holdings?format=pdf", "/api/v1/reports/realised?format=pdf"])
def test_pdf_statements_render(client, path):
    resp = client.get(path)
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF") and len(resp.content) > 1500
