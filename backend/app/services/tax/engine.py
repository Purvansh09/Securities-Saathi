"""Deterministic capital-gains classification (CLAUDE.md rule 8: a rule engine, not ML, not an LLM).

Three steps, all pure:
  1. classify_security -- which tax treatment a security gets (equity / other / debt fund / bond)
  2. classify_lot      -- for one FIFO-matched sale lot: short or long term, which rule, which rate
  3. summarize_year    -- for one financial year: net each bucket, set off losses, apply the s.112A
                          exemption, estimate the tax

Inputs are the FIFO matches the portfolio already computes (services/fifo.py), so the gains here
are the same numbers the realised P/L screen shows. Rates come from rules.py by sale date.

What this deliberately does NOT do (shown to the user as notes, never silently): slab-rate tax
(depends on the person's other income), surcharge, indexation for pre-July-2024 non-equity LTCG,
and the 31-Jan-2018 grandfathered cost (we don't hold that price).
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services.tax.rules import CESS_RATE, GRANDFATHER_BEFORE, SPECIFIED_MF_FROM, RuleSet, rules_for

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)

EQUITY, OTHER, DEBT_FUND, BOND = "EQUITY", "OTHER", "DEBT_FUND", "BOND"


@dataclass(frozen=True)
class AssetClass:
    kind: str  # EQUITY | OTHER | DEBT_FUND | BOND
    listed: bool
    basis: str  # plain-English reason, shown to the user
    certain: bool = True  # False when the category doesn't settle it; the UI asks the user to check


# AMFI category text -> treatment. Checked in order; first match wins.
_EQUITY_MF = re.compile(r"equity|elss|arbitrage|aggressive hybrid|equity savings|index funds - equity", re.I)
_DEBT_MF = re.compile(r"debt|income|liquid|gilt|overnight|money market|fixed term|conservative hybrid|index funds - debt", re.I)
_DEBTISH_NAME = re.compile(r"gilt|g-?sec|sdl|bond|debt|liquid|psu|crisil|target maturity|money market|overnight", re.I)
_LIKELY_EQUITY_HYBRID = re.compile(r"balanced advantage|dynamic asset allocation", re.I)


def classify_security(sec_type: str, name: str, metadata: dict, exchange: str | None) -> AssetClass:
    meta = metadata or {}
    if sec_type == "STOCK":
        return AssetClass(EQUITY, True, "Listed equity share (STT paid on exchange trades)")

    if sec_type == "BOND":
        listed = exchange in ("NSE", "BSE")
        return AssetClass(BOND, listed, "Listed bond" if listed else "Unlisted bond")

    if sec_type == "ETF":
        underlying = (meta.get("underlying_class") or "").upper()
        if underlying == "EQUITY":
            return AssetClass(EQUITY, True, "Equity ETF (equity-oriented fund units)")
        if underlying == "DEBT":
            return AssetClass(DEBT_FUND, True, "Debt ETF (specified mutual fund)")
        if underlying in ("COMMODITY", "GLOBAL INDICES"):
            label = "Gold/silver ETF" if underlying == "COMMODITY" else "International ETF (not equity-oriented for Indian tax)"
            return AssetClass(OTHER, True, label)
        return AssetClass(OTHER, True, f"ETF with underlying “{underlying or 'unknown'}”", certain=False)

    # Mutual funds: AMFI's scheme category decides.
    category = meta.get("category") or ""
    if _EQUITY_MF.search(category):
        return AssetClass(EQUITY, False, f"Equity-oriented fund ({category})")
    if _DEBT_MF.search(category):
        return AssetClass(DEBT_FUND, False, f"Debt fund ({category})")
    if "index funds" in category.lower():
        if _DEBTISH_NAME.search(name):
            return AssetClass(DEBT_FUND, False, f"Debt index fund ({category})", certain=False)
        return AssetClass(EQUITY, False, f"Index fund, assumed equity ({category})", certain=False)
    if _LIKELY_EQUITY_HYBRID.search(category):
        return AssetClass(EQUITY, False, f"Hybrid fund usually taxed as equity ({category})", certain=False)
    return AssetClass(OTHER, False, f"Non-equity fund ({category or 'category unknown'})", certain=False)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):  # 31 Mar + 12 months -> 31 Mar; 31 Jan + 1 month -> 28/29 Feb
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


def financial_year(d: date) -> str:
    """1 April to 31 March (rule 11): 2026-05-10 -> "2026-27"."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def fy_bounds(fy: str) -> tuple[date, date]:
    start = int(fy[:4])
    return date(start, 4, 1), date(start + 1, 3, 31)


@dataclass(frozen=True)
class LotTax:
    term: str  # SHORT | LONG
    category: str  # EQUITY (special rates) | OTHER (s.112 / slab)
    rate: Decimal | None  # None = taxed at the person's slab rate
    rule: str  # e.g. "Equity STCG (s.111A)"
    rule_set: str
    notes: tuple[str, ...] = ()


def classify_lot(asset: AssetClass, acquired: date, sold: date) -> LotTax:
    """acquired/sold are IST calendar dates. "Held for more than N months" means the sale date is
    after the date N months on from purchase: bought 10 Apr 2025, sold 10 Apr 2026 is exactly 12
    months, so short term; sold 11 Apr 2026 is long term."""
    rs: RuleSet = rules_for(sold)
    notes: list[str] = []
    if not asset.certain:
        notes.append(f"Check the classification: {asset.basis}")

    # s.50AA: debt fund units bought on/after 1 Apr 2023 are short term regardless of holding.
    if asset.kind == DEBT_FUND and acquired >= SPECIFIED_MF_FROM:
        return LotTax("SHORT", "OTHER", None, "Debt fund, always short term (s.50AA)", rs.name, tuple(notes))
    # s.50AA also covers unlisted bonds transferred from 23 Jul 2024.
    if asset.kind == BOND and not asset.listed and sold >= date(2024, 7, 23):
        return LotTax("SHORT", "OTHER", None, "Unlisted bond, always short term (s.50AA)", rs.name, tuple(notes))

    months = rs.months_listed if (asset.kind == EQUITY or asset.listed) else rs.months_other
    long_term = sold > add_months(acquired, months)

    if asset.kind == EQUITY:
        if not long_term:
            return LotTax("SHORT", "EQUITY", rs.equity_stcg_rate, "Equity STCG (s.111A)", rs.name, tuple(notes))
        if acquired < GRANDFATHER_BEFORE:
            notes.append("Bought before 1 Feb 2018: cost can be stepped up to the 31 Jan 2018 price, which isn't applied here, so the gain may be overstated")
        return LotTax("LONG", "EQUITY", rs.equity_ltcg_rate, "Equity LTCG (s.112A)", rs.name, tuple(notes))

    if not long_term:
        return LotTax("SHORT", "OTHER", None, f"STCG at slab rate (held up to {months} months)", rs.name, tuple(notes))
    if asset.kind == BOND and asset.listed:
        return LotTax("LONG", "OTHER", rs.listed_bond_ltcg_rate, "Listed bond LTCG (s.112)", rs.name, tuple(notes))
    if rs.other_ltcg_indexation:
        notes.append("Sold before 23 Jul 2024: indexation could lower this gain; not applied here")
    return LotTax("LONG", "OTHER", rs.other_ltcg_rate, "LTCG (s.112)", rs.name, tuple(notes))


# --- one financial year ---------------------------------------------------------------------


@dataclass
class Bucket:
    key: tuple  # (term, category, rate)
    label: str
    term: str
    category: str
    rate: Decimal | None
    gains: Decimal = _ZERO  # sum of positive lot gains
    losses: Decimal = _ZERO  # sum of negative lot gains, as a positive number
    set_off: Decimal = _ZERO  # other buckets' losses absorbed here
    exemption: Decimal = _ZERO
    taxable: Decimal = _ZERO
    tax: Decimal | None = _ZERO  # None for slab-rate buckets

    @property
    def net(self) -> Decimal:
        return self.gains - self.losses


@dataclass
class YearSummary:
    fy: str
    buckets: list[Bucket]
    exemption_limit: Decimal
    exemption_used: Decimal
    special_rate_tax: Decimal  # tax on the flat-rate buckets, before cess
    cess: Decimal
    slab_income: Decimal  # add to taxable income; taxed at the person's slab rate
    short_term_loss_carried: Decimal
    long_term_loss_carried: Decimal
    notes: list[str] = field(default_factory=list)


def _label(term: str, category: str, rate: Decimal | None) -> str:
    kind = "Equity" if category == "EQUITY" else "Other"
    t = "STCG" if term == "SHORT" else "LTCG"
    return f"{kind} {t} @ {rate.normalize():f}%" if rate is not None else f"{kind} {t} at slab rate"


def summarize_year(fy: str, lots: list[tuple[LotTax, Decimal]]) -> YearSummary:
    """lots: (classification, gain) for every sale lot transferred in this financial year.

    Set-off follows the Act's limits (a short-term loss can absorb short- or long-term gains; a
    long-term loss only long-term gains). The *order* is chosen to minimise tax, and is stated in
    the notes: slab-rate gains first (the slab may be up to 30%), then higher flat rates, then
    other LTCG, and equity LTCG last because the exemption may cover it anyway."""
    buckets: dict[tuple, Bucket] = {}
    for lt, gain in lots:
        key = (lt.term, lt.category, lt.rate)
        b = buckets.setdefault(key, Bucket(key, _label(*key), lt.term, lt.category, lt.rate))
        if gain >= 0:
            b.gains += gain
        else:
            b.losses += -gain

    def rate_order(b: Bucket) -> Decimal:
        return Decimal(100) if b.rate is None else b.rate  # slab first: it may be the highest

    remaining: dict[tuple, Decimal] = {k: max(b.net, _ZERO) for k, b in buckets.items()}
    st_loss = sum((-b.net for b in buckets.values() if b.term == "SHORT" and b.net < 0), _ZERO)
    lt_loss = sum((-b.net for b in buckets.values() if b.term == "LONG" and b.net < 0), _ZERO)

    def absorb(loss: Decimal, targets: list[Bucket]) -> Decimal:
        for b in targets:
            if loss <= 0:
                break
            used = min(loss, remaining[b.key])
            remaining[b.key] -= used
            b.set_off += used
            loss -= used
        return loss

    gains_st = sorted((b for b in buckets.values() if b.term == "SHORT" and b.net > 0), key=rate_order, reverse=True)
    gains_lt_other = sorted((b for b in buckets.values() if b.term == "LONG" and b.category == "OTHER" and b.net > 0), key=rate_order, reverse=True)
    gains_lt_equity = sorted((b for b in buckets.values() if b.term == "LONG" and b.category == "EQUITY" and b.net > 0), key=rate_order, reverse=True)

    st_loss = absorb(st_loss, gains_st + gains_lt_other + gains_lt_equity)
    lt_loss = absorb(lt_loss, gains_lt_other + gains_lt_equity)

    # s.112A exemption on what's left of equity LTCG, applied to the highest rate first.
    _, fy_end = fy_bounds(fy)
    limit = rules_for(fy_end).equity_ltcg_exemption
    left = limit
    for b in gains_lt_equity:
        used = min(left, remaining[b.key])
        remaining[b.key] -= used
        b.exemption = used
        left -= used
    exemption_used = limit - left

    special_tax = _ZERO
    slab_income = _ZERO
    for b in buckets.values():
        b.taxable = remaining[b.key]
        if b.rate is None:
            b.tax = None
            slab_income += b.taxable
        else:
            b.tax = b.taxable * b.rate / _HUNDRED
            special_tax += b.tax

    notes = [
        "Estimate before surcharge, which depends on your total income.",
        "Brokerage and other charges are deducted as transfer costs. STT isn't deductible by law, so if your charges include STT the gains here are slightly understated.",
    ]
    if slab_income > 0:
        notes.append("Slab-rate gains are added to your other income and taxed at your income-tax slab; that tax isn't included here.")
    if st_loss > 0 or lt_loss > 0:
        notes.append("Unabsorbed capital losses can be carried forward for 8 years if the return is filed on time.")
    if len({lt.rule_set for lt, _ in lots}) > 1:
        notes.append("This year spans the 23 Jul 2024 rate change, so the same kind of gain appears at two rates.")

    ordered = sorted(buckets.values(), key=lambda b: (b.term != "SHORT", b.category != "EQUITY", -rate_order(b)))
    return YearSummary(
        fy=fy,
        buckets=ordered,
        exemption_limit=limit,
        exemption_used=exemption_used,
        special_rate_tax=special_tax,
        cess=special_tax * CESS_RATE / _HUNDRED,
        slab_income=slab_income,
        short_term_loss_carried=st_loss,
        long_term_loss_carried=lt_loss,
        notes=notes,
    )
