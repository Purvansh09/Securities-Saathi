"""Valuation: bond estimates (rule 10), per-position numbers, totals and allocation.
Expected values are worked by hand in the comments."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from app.services.fifo import LedgerEntry, match_fifo
from app.services.valuation import (
    Position,
    PriceQuote,
    SecurityRef,
    allocate,
    estimate_bond_price,
    resolve_quote,
    summarize,
)

T0 = datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 29)

# The seeded G-Sec's real metadata: 7.18% GS 2033, maturing 14 Aug 2033
GSEC = {"issuer": "Government of India", "coupon_pct": 7.18, "maturity_date": "2033-08-14"}


def sec(isin="INE000000001", type_="STOCK", sector="IT", metadata=None):
    return SecurityRef(isin, isin, None, f"Name {isin}", type_, sector, metadata or {})


def fifo(*entries):
    return match_fifo(list(entries))


def buy(qty, price, charges="0", at=T0, id_="b"):
    return LedgerEntry(id_, "BUY", D(qty), D(price), D(charges), at)


def sell(qty, price, at, id_="s"):
    return LedgerEntry(id_, "SELL", D(qty), D(price), D("0"), at)


# --- bond estimates ---------------------------------------------------------------------

def test_gsec_accrues_from_last_coupon_30_360():
    # Coupons on 14 Feb / 14 Aug. Last coupon before 29 Sep 2026 is 14 Aug 2026.
    # 30/360 days: 1 month * 30 + (29 - 14) = 45
    # accrued = 100 * 7.18% * 45/360 = 0.8975  ->  100.8975
    assert estimate_bond_price(GSEC, TODAY) == D("100.8975")


def test_gsec_on_coupon_date_has_no_accrual():
    assert estimate_bond_price(GSEC, date(2026, 8, 14)) == D("100")


def test_gsec_accrual_across_a_year_boundary():
    # 13 Feb 2026 -> last coupon 14 Aug 2025; 360 - 6*30 + (13 - 14) = 179 days
    assert estimate_bond_price(GSEC, date(2026, 2, 13)) == D(100) + D("7.18") * 179 / 360


def test_matured_bond_is_worth_face_value():
    assert estimate_bond_price(GSEC, date(2033, 8, 14)) == D("100")
    assert estimate_bond_price(GSEC, date(2034, 1, 1)) == D("100")


def test_custom_face_value_and_annual_coupon():
    # face 1000, 8% annual, matures 31 Mar 2030 -> last coupon 31 Mar 2026
    # 30/360 from the 31st counts as the 30th: 6 months * 30 + (30 - 30) = 180 days
    # accrued = 1000 * 8% * 180/360 = 40
    meta = {"coupon_pct": 8, "maturity_date": "2030-03-31", "face_value": 1000, "coupon_frequency": 1}
    assert estimate_bond_price(meta, date(2026, 9, 30)) == D("1040")


def test_month_end_maturity_clamps_to_february():
    # Matures 31 Aug; stepping back six months lands on 28 Feb 2026 (not "31 Feb").
    # 15 Mar 2026: 1 month * 30 + (15 - 28) = 17 days
    meta = {"coupon_pct": 6, "maturity_date": "2030-08-31"}
    assert estimate_bond_price(meta, date(2026, 3, 15)) == D(100) + D(6) * 17 / 360


@pytest.mark.parametrize(
    "meta",
    [{}, {"coupon_pct": 7}, {"coupon_pct": "abc", "maturity_date": "2030-01-01"},
     {"coupon_pct": 7, "maturity_date": "2030-01-01", "coupon_frequency": 3}],
)
def test_bond_without_usable_metadata_is_not_estimated(meta):
    assert estimate_bond_price(meta, TODAY) is None


def test_real_price_beats_bond_estimate():
    bond = sec(type_="BOND", metadata=GSEC)
    quote = resolve_quote(bond, D("99.5"), date(2026, 9, 26), TODAY)
    assert quote == PriceQuote(D("99.5"), date(2026, 9, 26), estimated=False)


def test_bond_without_price_is_estimated_and_flagged():
    quote = resolve_quote(sec(type_="BOND", metadata=GSEC), None, None, TODAY)
    assert quote == PriceQuote(D("100.8975"), TODAY, estimated=True)


def test_stock_without_price_has_no_quote():
    assert resolve_quote(sec(), None, None, TODAY) is None


# --- positions and totals ---------------------------------------------------------------

def test_priced_position():
    # invested 10 * 100 + 20 = 1020; market 10 * 120 = 1200; unrealised 180 = 17.647...%
    p = Position(sec(), fifo(buy("10", "100", "20")), PriceQuote(D("120"), TODAY, False))

    assert p.invested == D("1020")
    assert p.market_value == D("1200")
    assert p.unrealized_pnl == D("180")
    assert p.unrealized_pnl_pct == D("180") / D("1020") * 100


def test_unpriced_position_is_carried_at_cost():
    p = Position(sec(), fifo(buy("5", "100")), None)

    assert p.market_value is None
    assert p.unrealized_pnl is None
    assert p.value == D("500")


def _sample_portfolio():
    priced = Position(  # STOCK / IT: invested 1020, worth 1200
        sec("INE000000001", "STOCK", "IT"), fifo(buy("10", "100", "20")), PriceQuote(D("120"), TODAY, False)
    )
    unpriced = Position(  # MF / no sector: invested 500, no price -> counts at 500
        sec("INF000000001", "MF", None), fifo(buy("5", "100")), None
    )
    exited = Position(  # sold everything for a 100 gain; not a holding any more
        sec("INE000000002", "STOCK", "Banks"),
        fifo(buy("1", "100"), sell("1", "200", at=T0 + timedelta(days=5))),
        PriceQuote(D("210"), TODAY, False),
    )
    return [priced, unpriced, exited]


def test_summary_totals():
    # invested 1020 + 500 = 1520; current 1200 + 500 = 1700; unrealised 180 (all from the
    # priced stock); realised 100 from the exited stock
    totals = summarize(_sample_portfolio())

    assert totals.invested == D("1520")
    assert totals.current_value == D("1700")
    assert totals.unrealized_pnl == D("180")
    assert totals.unrealized_pnl_pct == D("180") / D("1520") * 100
    assert totals.realized_pnl == D("100")
    assert totals.holdings_count == 2
    assert totals.unpriced_holdings == 1
    assert totals.estimated_holdings == 0


def test_summary_counts_estimated_bonds():
    bond = Position(sec("IN0020230085", "BOND", "Government Securities", GSEC), fifo(buy("10", "99")),
                    resolve_quote(sec(type_="BOND", metadata=GSEC), None, None, TODAY))
    assert summarize([bond]).estimated_holdings == 1


def test_empty_portfolio_summary():
    totals = summarize([])
    assert totals.invested == 0 and totals.current_value == 0
    assert totals.unrealized_pnl_pct is None


def test_allocation_by_asset_class():
    # STOCK 1200, MF 500 (at cost); exited position excluded. total 1700
    total, buckets = allocate(_sample_portfolio(), "asset_class")

    assert total == D("1700")
    assert [(b.key, b.value, b.holdings_count) for b in buckets] == [("STOCK", D("1200"), 1), ("MF", D("500"), 1)]
    assert buckets[0].weight_pct == D("1200") / D("1700") * 100
    assert sum(b.weight_pct for b in buckets) == pytest.approx(100)


def test_allocation_by_sector_buckets_missing_sector():
    _, buckets = allocate(_sample_portfolio(), "sector")
    assert [b.key for b in buckets] == ["IT", "Unclassified"]


def test_allocation_rejects_unknown_dimension():
    with pytest.raises(ValueError):
        allocate([], "country")
