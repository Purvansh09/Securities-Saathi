"""XIRR: the solver against Excel's documented example and closed-form cases, the cash-flow
convention, and GET /analysis/xirr on the same hand-worked portfolio as test_portfolio.py."""

import math
from datetime import date, datetime, timezone
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

import app.api.v1.analysis as analysis_module
from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.main import app
from app.services.xirr import CashFlow, Trade, trade_flows, xirr
from tests.test_portfolio import FIXED_USER_ID, PORTFOLIO_ROWS, TODAY, FakeConnection


def flows(*pairs):
    return [CashFlow(date.fromisoformat(d), D(a)) for d, a in pairs]


# --- solver ---------------------------------------------------------------------------------


def test_matches_excels_documented_example():
    # The example from Microsoft's XIRR help page; Excel returns 0.373362535 (37.34%).
    example = flows(
        ("2008-01-01", "-10000"),
        ("2008-03-01", "2750"),
        ("2008-10-30", "4250"),
        ("2009-02-15", "3250"),
        ("2009-04-01", "2750"),
    )
    rate = xirr(example)
    # Excel only iterates to 0.000001% (1e-8), so agree to that...
    assert abs(rate - D("0.373362535")) < D("1e-8")

    # ...and our root is the more exact one: NPV at our rate is closer to zero than at Excel's.
    def npv(r: D) -> D:
        return sum(cf.amount / (1 + r) ** (D((cf.on - example[0].on).days) / 365) for cf in example)

    assert abs(npv(rate)) < abs(npv(D("0.373362535")))


def test_one_year_ten_percent_is_ten_percent():
    assert xirr(flows(("2025-01-01", "-1000"), ("2026-01-01", "1100"))) == D("0.1000000000")


def test_leap_year_uses_actual_over_365():
    # 366 days apart, so 10% over the period is slightly under 10% a year.
    rate = xirr(flows(("2024-01-01", "-1000"), ("2025-01-01", "1100")))
    expected = 1.1 ** (365 / 366) - 1
    assert abs(float(rate) - expected) < 1e-9


def test_losses_are_negative():
    assert xirr(flows(("2025-01-01", "-1000"), ("2026-01-01", "500"))) == D("-0.5000000000")


def test_same_day_flows_are_netted():
    # Two buys on one day behave like one buy of the total.
    split = xirr(flows(("2025-01-01", "-400"), ("2025-01-01", "-600"), ("2026-01-01", "1100")))
    assert split == D("0.1000000000")


@pytest.mark.parametrize(
    "cash_flows",
    [
        flows(("2025-01-01", "-1000"), ("2025-06-01", "-500")),  # nothing received
        flows(("2025-01-01", "1000"), ("2025-06-01", "500")),  # nothing invested
        flows(("2025-01-01", "-1000"), ("2025-01-01", "1100")),  # no time passed
        flows(("2025-01-01", "-100"), ("2025-01-02", "200")),  # doubling in a day: off the scale
        [],
    ],
)
def test_undefined_cases_return_none(cash_flows):
    assert xirr(cash_flows) is None


def test_trade_flows_sign_charges_and_ist_date():
    buy = Trade("BUY", D("10"), D("100"), D("5"), datetime(2026, 3, 31, 20, 0, tzinfo=timezone.utc))
    sell = Trade("SELL", D("10"), D("110"), D("5"), datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc))
    assert trade_flows([buy, sell]) == [
        CashFlow(date(2026, 4, 1), D("-1005")),  # 20:00 UTC is 01:30 IST on 1 April (rule 11)
        CashFlow(date(2026, 6, 1), D("1095")),
    ]


# --- endpoint -------------------------------------------------------------------------------


def _float_xirr(cfs: list[tuple[date, float]]) -> float:
    """Independent check: plain-float bisection, written separately from the code under test."""
    t0 = cfs[0][0]

    def npv(r):
        return sum(a / (1 + r) ** ((d - t0).days / 365) for d, a in cfs)

    lo, hi = -0.99, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if (npv(mid) > 0) == (npv(lo) > 0) else (lo, mid)
    return (lo + hi) / 2


@pytest.fixture
def report(monkeypatch):
    monkeypatch.setattr(analysis_module, "today_ist", lambda: TODAY)
    conn = FakeConnection(PORTFOLIO_ROWS)

    async def _db():
        yield conn

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user_id] = lambda: FIXED_USER_ID
    try:
        resp = TestClient(app).get("/api/v1/analysis/xirr")
        assert resp.status_code == 200, resp.text
        yield resp.json(), conn
    finally:
        app.dependency_overrides.clear()


def test_portfolio_xirr_totals_and_flags(report):
    body, conn = report
    p = body["portfolio"]
    # Buys: TCS 38020 + MF 5000 + GSEC 990 + INFY 1500. Sells: TCS 15990 + INFY 1600.
    assert D(p["invested"]) == D("45510")
    assert D(p["received"]) == D("17590")
    assert D(p["current_value"]) == D("30608.975")  # same figure as /portfolio/summary
    assert p["first_date"] == "2026-01-05" and p["days"] == 267
    assert p["short_period"] is True
    assert p["includes_unpriced"] is True  # the MF has no price and is counted at cost

    expected = _float_xirr(
        [
            (date(2026, 1, 5), -38020), (date(2026, 1, 7), -5000), (date(2026, 1, 8), -990),
            (date(2026, 1, 9), -1500), (date(2026, 2, 4), 15990), (date(2026, 2, 14), 1600),
            (TODAY, 30608.975),
        ]
    )
    assert abs(float(p["xirr_pct"]) / 100 - expected) < 1e-8

    # The ledger query is scoped to the JWT's user (rule 13).
    assert conn.calls[0][1] == (FIXED_USER_ID,)


def test_holding_xirr_includes_closed_positions(report):
    body, _ = report
    by_isin = {h["isin"]: h for h in body["holdings"]}
    infy = by_isin["INE009A01021"]
    assert infy["is_open"] is False and D(infy["current_value"]) == 0
    # 1500 -> 1600 over 36 days, annualised.
    assert abs(float(infy["xirr_pct"]) / 100 - ((1600 / 1500) ** (365 / 36) - 1)) < 1e-8
    # Open holdings come first, largest first.
    assert [h["isin"] for h in body["holdings"]][:1] == ["INE467B01029"]
    assert math.isclose(float(by_isin["INF179K01XQ0"]["xirr_pct"]), 0.0, abs_tol=1e-9)  # at cost: 0%
