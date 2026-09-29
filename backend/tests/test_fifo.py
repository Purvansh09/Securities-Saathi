"""FIFO lot matching (CLAUDE.md rule 6). Every expected number below is worked out by hand
in the comment next to it, so the tests double as a readable spec."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from app.services.fifo import FifoError, LedgerEntry, match_fifo

T0 = datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc)


def buy(id_, qty, price, charges="0", at=T0, created=None):
    return LedgerEntry(id_, "BUY", D(qty), D(price), D(charges), at, created)


def sell(id_, qty, price, charges="0", at=T0, created=None):
    return LedgerEntry(id_, "SELL", D(qty), D(price), D(charges), at, created)


def days(n):
    return T0 + timedelta(days=n)


def test_buys_only_charges_go_into_cost_basis():
    # 10 x 100 + 20 charges = 1020 cost, 102 per unit
    result = match_fifo([buy("b1", "10", "100", "20")])

    assert result.quantity == D("10")
    assert result.cost_basis == D("1020")
    assert result.avg_cost == D("102")
    assert result.matches == []
    assert result.realized_pnl == 0


def test_partial_lot_sale():
    # lot: 10 @ 100 + 20 charges = 1020
    # sell 4 @ 150 - 6 charges = 594 proceeds; cost 1020 * 4/10 = 408; gain 186
    # left: 6 units costing 612 (still 102/unit)
    result = match_fifo([buy("b1", "10", "100", "20"), sell("s1", "4", "150", "6", at=days(30))])

    [m] = result.matches
    assert (m.buy_id, m.sell_id, m.quantity) == ("b1", "s1", D("4"))
    assert m.cost == D("408")
    assert m.proceeds == D("594")
    assert m.gain == D("186")
    assert m.holding_days == 30

    assert result.quantity == D("6")
    assert result.cost_basis == D("612")
    assert result.avg_cost == D("102")
    assert result.realized_pnl == D("186")


def test_sale_spanning_multiple_lots_consumes_oldest_first():
    # lot1: 10 @ 100 = 1000 (day 0);  lot2: 10 @ 120 = 1200 (day 31)
    # sell 15 @ 130 - 15 charges on day 59 = 1935 net proceeds
    #   slice 1: all 10 of lot1 -> cost 1000, proceeds 1935 * 10/15 = 1290, gain 290
    #   slice 2: 5 of lot2      -> cost 600,  proceeds 1935 - 1290 = 645,   gain 45
    # left: 5 of lot2 costing 600; realised 335
    result = match_fifo(
        [
            buy("b1", "10", "100", at=days(0)),
            buy("b2", "10", "120", at=days(31)),
            sell("s1", "15", "130", "15", at=days(59)),
        ]
    )

    m1, m2 = result.matches
    assert (m1.buy_id, m1.quantity, m1.cost, m1.proceeds, m1.gain) == ("b1", D("10"), D("1000"), D("1290"), D("290"))
    assert (m2.buy_id, m2.quantity, m2.cost, m2.proceeds, m2.gain) == ("b2", D("5"), D("600"), D("645"), D("45"))
    assert m1.holding_days == 59 and m2.holding_days == 28  # each slice keeps its own lot's age

    [lot] = result.open_lots
    assert (lot.buy_id, lot.quantity, lot.cost) == ("b2", D("5"), D("600"))
    assert result.realized_pnl == D("335")


def test_same_timestamp_buy_is_applied_before_sell():
    # Intraday: bought and sold at the same recorded time. Given SELL-first on input,
    # it must still match, because BUY sorts before SELL on a tie (as ledger_balance_ok does).
    result = match_fifo([sell("s1", "5", "110"), buy("b1", "5", "100")])

    [m] = result.matches
    assert m.gain == D("50")
    assert m.holding_days == 0
    assert result.open_lots == []


def test_same_timestamp_buys_tiebreak_on_created_at():
    # Two buys at the same executed_at: the one recorded first is the older lot,
    # regardless of input order.
    later = buy("b-late", "1", "200", created=T0 + timedelta(seconds=5))
    earlier = buy("b-early", "1", "100", created=T0)
    result = match_fifo([later, earlier, sell("s1", "1", "150", at=days(1))])

    [m] = result.matches
    assert m.buy_id == "b-early"
    assert m.gain == D("50")


def test_repeated_partial_sales_leave_no_rounding_residue():
    # lot: 3 @ 10 + 1 charge = 31, i.e. 10.333... per unit -- not representable exactly.
    # Three sells of 1 @ 10: the first two take 31/3 each, the last takes whatever is left,
    # so costs add back to exactly 31 and realised P/L is exactly 30 - 31 = -1.
    result = match_fifo(
        [
            buy("b1", "3", "10", "1"),
            sell("s1", "1", "10", at=days(1)),
            sell("s2", "1", "10", at=days(2)),
            sell("s3", "1", "10", at=days(3)),
        ]
    )

    assert sum(m.cost for m in result.matches) == D("31")
    assert result.realized_pnl == D("-1")
    assert result.open_lots == []


def test_sell_charges_split_across_lots_add_back_exactly():
    # 7 units over lots of 3 and 4, sell charges 10 -> 10 * 3/7 and the remainder.
    result = match_fifo(
        [
            buy("b1", "3", "10"),
            buy("b2", "4", "10", at=days(1)),
            sell("s1", "7", "10", "10", at=days(2)),
        ]
    )

    assert sum(m.proceeds for m in result.matches) == D("60")  # 70 - 10
    assert result.realized_pnl == D("-10")


def test_full_exit_then_rebuy_starts_a_fresh_lot():
    result = match_fifo(
        [
            buy("b1", "5", "100"),
            sell("s1", "5", "120", at=days(10)),
            buy("b2", "2", "90", at=days(20)),
        ]
    )

    [lot] = result.open_lots
    assert (lot.buy_id, lot.quantity, lot.cost) == ("b2", D("2"), D("180"))
    assert result.realized_pnl == D("100")


def test_fractional_units_for_mutual_funds():
    # MF units go to 3+ decimals. 12.345 units @ 81.2345 NAV, sell 2.5 units @ 90.
    # cost of 2.5 units = 12.345 * 81.2345 * 2.5 / 12.345 = 203.08625; proceeds 225
    result = match_fifo(
        [buy("b1", "12.345", "81.2345"), sell("s1", "2.5", "90", at=days(400))]
    )

    [m] = result.matches
    assert m.cost == D("203.08625")
    assert m.gain == D("21.91375")
    assert result.quantity == D("9.845")


def test_holding_period_uses_ist_dates():
    # Bought 2026-03-31 20:00 UTC = 1 Apr 01:30 IST; sold 1 Apr 10:30 IST.
    # In UTC dates that's 1 day apart, in IST it's the same day -- and it's in FY 2026-27, not 2025-26.
    bought = datetime(2026, 3, 31, 20, 0, tzinfo=timezone.utc)
    sold = datetime(2026, 4, 1, 5, 0, tzinfo=timezone.utc)
    result = match_fifo([buy("b1", "1", "100", at=bought), sell("s1", "1", "100", at=sold)])

    [m] = result.matches
    assert str(m.acquired_on) == "2026-04-01"
    assert m.holding_days == 0


def test_oversell_raises():
    with pytest.raises(FifoError, match="exceeds"):
        match_fifo([buy("b1", "5", "100"), sell("s1", "6", "100", at=days(1))])


def test_sell_before_any_buy_raises():
    with pytest.raises(FifoError):
        match_fifo([sell("s1", "1", "100"), buy("b1", "1", "100", at=days(1))])


@pytest.mark.parametrize(
    "entry",
    [
        LedgerEntry("x", "HOLD", D("1"), D("1"), D("0"), T0),
        LedgerEntry("x", "BUY", D("0"), D("1"), D("0"), T0),
        LedgerEntry("x", "BUY", D("1"), D("-1"), D("0"), T0),
    ],
)
def test_invalid_entries_raise(entry):
    with pytest.raises(FifoError):
        match_fifo([entry])


def test_empty_ledger():
    result = match_fifo([])
    assert result.quantity == 0
    assert result.avg_cost is None
    assert result.realized_pnl == 0
