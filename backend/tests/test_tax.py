"""Tax engine: classification, holding periods, rate changes, set-off and the s.112A exemption.
Worked examples follow the Income Tax Department's own illustrations where it gives them."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.services.tax.engine import (
    BOND, DEBT_FUND, EQUITY, OTHER, AssetClass, add_months, classify_lot, classify_security,
    financial_year, summarize_year,
)
from app.services.tax.rules import rules_for

SHARE = AssetClass(EQUITY, True, "share")
GOLD_ETF = AssetClass(OTHER, True, "gold etf")
DEBT_MF = AssetClass(DEBT_FUND, False, "debt fund")
INTL_FOF = AssetClass(OTHER, False, "fof")


# --- classification -------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sec_type, name, meta, exchange, kind, certain",
    [
        ("STOCK", "Reliance", {}, "NSE", EQUITY, True),
        ("ETF", "NIFTYBEES", {"underlying_class": "EQUITY"}, "NSE", EQUITY, True),
        ("ETF", "GOLDBEES", {"underlying_class": "COMMODITY"}, "NSE", OTHER, True),
        ("ETF", "LIQUIDBEES", {"underlying_class": "DEBT"}, "NSE", DEBT_FUND, True),
        ("MF", "HDFC Large Cap", {"category": "Equity Scheme - Large Cap Fund"}, "AMFI", EQUITY, True),
        ("MF", "X Arbitrage", {"category": "Hybrid Scheme - Arbitrage Fund"}, "AMFI", EQUITY, True),
        ("MF", "X Liquid", {"category": "Debt Scheme - Liquid Fund"}, "AMFI", DEBT_FUND, True),
        ("MF", "X Nifty 50 Index", {"category": "Other Scheme - Index Funds"}, "AMFI", EQUITY, False),
        ("MF", "X Gilt SDL Index", {"category": "Other Scheme - Index Funds"}, "AMFI", DEBT_FUND, False),
        ("MF", "X US FoF", {"category": "Other Scheme - FoF Overseas"}, "AMFI", OTHER, False),
        ("BOND", "7.18% GOI 2033", {}, "NSE", BOND, True),
    ],
)
def test_classify_security(sec_type, name, meta, exchange, kind, certain):
    a = classify_security(sec_type, name, meta, exchange)
    assert (a.kind, a.certain) == (kind, certain)


# --- holding period and rules by sale date ---------------------------------------------------

def test_more_than_twelve_months_means_the_day_after():
    assert classify_lot(SHARE, date(2025, 4, 10), date(2026, 4, 10)).term == "SHORT"  # exactly 12 months
    assert classify_lot(SHARE, date(2025, 4, 10), date(2026, 4, 11)).term == "LONG"


def test_add_months_clamps_month_end():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 3, 31), 12) == date(2026, 3, 31)


def test_official_illustration_sbi_shares():
    # Bought April 2023, sold December 2024 -> long term; bought April 2024, sold January 2025 -> short.
    assert classify_lot(SHARE, date(2023, 4, 15), date(2024, 12, 15)).term == "LONG"
    assert classify_lot(SHARE, date(2024, 4, 15), date(2025, 1, 15)).term == "SHORT"


def test_equity_rates_switch_on_23_july_2024():
    before = classify_lot(SHARE, date(2024, 1, 2), date(2024, 7, 22))
    after = classify_lot(SHARE, date(2024, 1, 2), date(2024, 7, 23))
    assert (before.rule, before.rate) == ("Equity STCG (s.111A)", D("15"))
    assert (after.rule, after.rate) == ("Equity STCG (s.111A)", D("20"))
    lt_before = classify_lot(SHARE, date(2022, 1, 2), date(2024, 7, 22))
    lt_after = classify_lot(SHARE, date(2022, 1, 2), date(2024, 7, 23))
    assert (lt_before.rate, lt_after.rate) == (D("10"), D("12.5"))


def test_gold_etf_is_listed_so_twelve_months_and_flat_rate_when_long():
    st = classify_lot(GOLD_ETF, date(2025, 5, 1), date(2026, 4, 1))
    lt = classify_lot(GOLD_ETF, date(2025, 5, 1), date(2026, 5, 2))
    assert (st.term, st.rate) == ("SHORT", None)  # slab
    assert (lt.term, lt.rate, lt.rule) == ("LONG", D("12.5"), "LTCG (s.112)")


def test_unlisted_non_equity_fund_needs_24_months_now_36_before():
    assert classify_lot(INTL_FOF, date(2024, 9, 1), date(2026, 8, 31)).term == "SHORT"  # 23.97 months
    assert classify_lot(INTL_FOF, date(2024, 9, 1), date(2026, 9, 2)).term == "LONG"
    old = classify_lot(INTL_FOF, date(2021, 6, 1), date(2024, 5, 1))  # 35 months, old 36-month rule
    assert old.term == "SHORT"


def test_debt_fund_after_april_2023_is_always_short_term():
    lot = classify_lot(DEBT_MF, date(2023, 4, 1), date(2026, 9, 1))
    assert (lot.term, lot.rate) == ("SHORT", None) and "50AA" in lot.rule
    older = classify_lot(DEBT_MF, date(2023, 3, 31), date(2026, 9, 1))  # bought before the cut-off
    assert (older.term, older.rate) == ("LONG", D("12.5"))


def test_pre_2018_equity_is_flagged_for_grandfathering():
    lot = classify_lot(SHARE, date(2017, 6, 1), date(2026, 6, 1))
    assert any("31 Jan 2018" in n for n in lot.notes)


def test_financial_year_runs_april_to_march():
    assert financial_year(date(2026, 3, 31)) == "2025-26"
    assert financial_year(date(2026, 4, 1)) == "2026-27"
    assert rules_for(date(2027, 1, 1)).equity_ltcg_exemption == D("125000")


# --- one year: exemption, set-off, tax -------------------------------------------------------

ST_EQ = classify_lot(SHARE, date(2026, 1, 5), date(2026, 6, 1))  # 20%
LT_EQ = classify_lot(SHARE, date(2024, 9, 1), date(2026, 6, 1))  # 12.5% above 1.25 lakh
ST_SLAB = classify_lot(GOLD_ETF, date(2026, 1, 5), date(2026, 6, 1))
LT_OTHER = classify_lot(GOLD_ETF, date(2024, 9, 1), date(2026, 6, 1))


def bucket(summary, term, category, rate):
    return next(b for b in summary.buckets if (b.term, b.category, b.rate) == (term, category, rate))


def test_exemption_covers_first_125000_of_equity_ltcg():
    s = summarize_year("2026-27", [(LT_EQ, D("200000")), (ST_EQ, D("10000"))])
    lt = bucket(s, "LONG", "EQUITY", D("12.5"))
    assert (lt.exemption, lt.taxable, lt.tax) == (D("125000"), D("75000"), D("9375.0"))
    assert s.special_rate_tax == D("9375.0") + D("2000")  # + 20% of 10,000 STCG
    assert s.cess == s.special_rate_tax * 4 / 100


def test_short_term_loss_offsets_slab_gain_first_then_flat_rates():
    s = summarize_year("2026-27", [(ST_EQ, D("-30000")), (ST_SLAB, D("20000")), (LT_EQ, D("150000"))])
    assert bucket(s, "SHORT", "OTHER", None).set_off == D("20000")  # slab gain absorbed first
    lt = bucket(s, "LONG", "EQUITY", D("12.5"))
    assert lt.set_off == D("10000")  # the remaining 10,000 of loss
    assert lt.taxable == D("150000") - D("10000") - D("125000")
    assert s.short_term_loss_carried == 0


def test_long_term_loss_cannot_touch_short_term_gains_and_is_carried():
    s = summarize_year("2026-27", [(LT_EQ, D("-50000")), (ST_EQ, D("40000"))])
    assert bucket(s, "SHORT", "EQUITY", D("20")).taxable == D("40000")
    assert s.long_term_loss_carried == D("50000")
    assert any("carried forward" in n for n in s.notes)


def test_gains_and_losses_net_within_a_bucket_first():
    s = summarize_year("2026-27", [(LT_EQ, D("-20000")), (LT_EQ, D("100000"))])
    eq = bucket(s, "LONG", "EQUITY", D("12.5"))
    assert (eq.gains, eq.losses, eq.net, eq.set_off) == (D("100000"), D("20000"), D("80000"), 0)
    assert eq.taxable == 0  # 80,000 is inside the 1.25 lakh exemption


def test_equity_long_term_loss_absorbs_other_ltcg():
    # Without the set-off the gold ETF gain would be taxed at 12.5%; the equity LT loss cancels it.
    s = summarize_year("2026-27", [(LT_EQ, D("-20000")), (LT_OTHER, D("20000"))])
    other = bucket(s, "LONG", "OTHER", D("12.5"))
    assert (other.set_off, other.taxable) == (D("20000"), 0)
    assert s.long_term_loss_carried == 0 and s.special_rate_tax == 0


def test_slab_gains_reported_not_taxed():
    s = summarize_year("2026-27", [(ST_SLAB, D("5000"))])
    assert s.slab_income == D("5000") and s.special_rate_tax == 0
    assert bucket(s, "SHORT", "OTHER", None).tax is None
