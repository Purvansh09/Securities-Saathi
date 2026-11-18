"""Capital-gains tax rules as versioned, dated data (CLAUDE.md rule 8).

Rates and holding periods change with Finance Acts, so they live here as rule sets with an
effective date range, never as constants scattered through the code. The engine (engine.py) picks
the rule set by the date of *transfer* (sale), because that is what the law keys on.

Verified 2026-10-07 against the Income Tax Department's own pages, which state they reflect the
Income-tax Act, 1961 as amended by the Finance Act, 2026:
  https://www.incometaxindia.gov.in/w/tax-on-long-term-capital-gains%E2%80%8B
  https://www.incometaxindia.gov.in/w/tax-on-short-term-capital-gains%E2%80%8B
  https://www.incometaxindia.gov.in/w/section-112a-60
  https://www.incometaxindia.gov.in/w/section-50aa
From tax year 2026-27 the Income-tax Act, 2025 applies; it renumbers these provisions but keeps the
same rates and thresholds for 2026-27. Section labels below use the 1961 Act numbers people know.

To update for a future Finance Act: add a new RuleSet with its effective_from date and close the
previous one's effective_to. Don't edit an old rule set; past years must keep their old rules.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

RULES_VERIFIED_ON = date(2026, 10, 7)
SOURCES = [
    "https://www.incometaxindia.gov.in/w/tax-on-long-term-capital-gains%E2%80%8B",
    "https://www.incometaxindia.gov.in/w/tax-on-short-term-capital-gains%E2%80%8B",
    "https://www.incometaxindia.gov.in/w/section-112a-60",
    "https://www.incometaxindia.gov.in/w/section-50aa",
]


@dataclass(frozen=True)
class RuleSet:
    name: str
    effective_from: date  # date of transfer, inclusive
    effective_to: date | None  # inclusive; None = still in force
    equity_stcg_rate: Decimal  # s.111A: STT-paid listed equity, equity-oriented fund units
    equity_ltcg_rate: Decimal  # s.112A
    equity_ltcg_exemption: Decimal  # s.112A: per financial year, on net equity LTCG
    other_ltcg_rate: Decimal  # s.112: everything else held long term
    other_ltcg_indexation: bool  # whether s.112 allowed indexation (we don't compute it; see engine)
    listed_bond_ltcg_rate: Decimal  # listed bonds/debentures (s.112 proviso: 10% without indexation pre-2024)
    months_listed: int  # holding period for listed securities and equity-oriented fund units
    months_other: int  # holding period for other assets, e.g. non-equity fund units


RULE_SETS: list[RuleSet] = [
    RuleSet(
        name="Before 23 Jul 2024",
        effective_from=date(2018, 4, 1),  # s.112A (LTCG on equity) began with FY 2018-19
        effective_to=date(2024, 7, 22),
        equity_stcg_rate=Decimal("15"),
        equity_ltcg_rate=Decimal("10"),
        equity_ltcg_exemption=Decimal("100000"),
        other_ltcg_rate=Decimal("20"),
        other_ltcg_indexation=True,
        listed_bond_ltcg_rate=Decimal("10"),
        months_listed=12,
        months_other=36,
    ),
    RuleSet(
        name="From 23 Jul 2024 (Finance (No. 2) Act 2024)",
        effective_from=date(2024, 7, 23),
        effective_to=None,
        equity_stcg_rate=Decimal("20"),
        equity_ltcg_rate=Decimal("12.5"),
        equity_ltcg_exemption=Decimal("125000"),
        other_ltcg_rate=Decimal("12.5"),
        other_ltcg_indexation=False,
        listed_bond_ltcg_rate=Decimal("12.5"),
        months_listed=12,
        months_other=24,
    ),
]

# s.50AA: units of a "specified mutual fund" (mostly debt) acquired on/after this date are always
# short-term, taxed at slab rate, however long they're held.
SPECIFIED_MF_FROM = date(2023, 4, 1)

# s.112A grandfathering: for listed equity bought before this date, cost is stepped up to the
# 31 Jan 2018 price. We don't hold that price, so such lots are flagged rather than adjusted.
GRANDFATHER_BEFORE = date(2018, 2, 1)

CESS_RATE = Decimal("4")  # health & education cess on the tax; surcharge depends on total income


def rules_for(transfer_date: date) -> RuleSet:
    for rs in RULE_SETS:
        if rs.effective_from <= transfer_date and (rs.effective_to is None or transfer_date <= rs.effective_to):
            return rs
    # Sales before FY 2018-19 predate s.112A; the earliest rule set is the closest we model.
    return RULE_SETS[0]
