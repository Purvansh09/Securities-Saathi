"""Securities sync: parsers, merge precedence and safety checks. The fixture text below is cut
from the real files (fetched 2026-09-29), trimmed to a few rows."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.securities_sync import merge, sources
from app.services.securities_sync.sources import SourceFormatError

NSE_EQUITY = """SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE
RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995,10,1,INE002A01018,10
20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5
BADROW,Broken Isin Limited,EQ,01-JAN-2020,10,1,NOTANISIN,10
"""

NSE_ETF = """Symbol,Underlying Asset,SecurityName,DateofListing,MarketLot,ISINNumber,FaceValue,ETF Underlying,Underlying Key
NIFTYBEES,Nifty 50,NIPINDETFNIFTYBEES,08-Jan-02,1,INF204KB14I2,1,EQUITY,Nifty 50
JUNIORBEES,Nippon India ETF Nifty Next 50 Junior BeES,NIP IND ETF JUNIOR BEES,06-Mar-03,1,INF732E01045,1.25,EQUITY,Nifty Next 50
"""

NIFTY500 = """Company Name,Industry,Symbol,Series,ISIN Code
Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018
"""

AMFI = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)

HDFC Mutual Fund

119018;INF179K01YV8;-;HDFC Large Cap Fund;Direct Plan;Growth Option;1234.5678;28-Sep-2026
119019;INF179K01YW6;INF179K01YX4;HDFC Large Cap Fund;Direct Plan;IDCW Option;45.1000;28-Sep-2026
119020;INF179K01YZ9;-;HDFC Large Cap Fund;Regular Plan;Growth Option;N.A.;N.A.

Nippon India Mutual Fund

140088;INF204KB14I2;-;Nippon India ETF Nifty 50 BeES;Other;Growth Option;280.1200;28-Sep-2026
"""

BHAV_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,"
    "OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
    "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
)


def _bhav_row(isin: str, symbol: str, series: str, close: str, src: str = "NSE", inst_id: str = "1", name: str = "X") -> str:
    return (
        f"2026-09-28,2026-09-28,CM,{src},STK,{inst_id},{isin},{symbol},{series},,,,,{name},"
        f"1,1,1,{close},{close},1,,1,,,1,1,1,F1,1,,,,,\n"
    )


# --- parsers --------------------------------------------------------------------------------


def test_nse_equity_list_strips_padded_headers_and_skips_bad_isins():
    rows = sources.parse_nse_equity_list(NSE_EQUITY)
    assert [r.isin for r in rows] == ["INE002A01018", "INE144J01027"]
    assert rows[0].symbol == "RELIANCE" and rows[0].type == "STOCK" and rows[0].exchange == "NSE"
    assert rows[0].metadata == {"nse_symbol": "RELIANCE", "nse_series": "EQ"}


def test_missing_column_fails_loudly():
    with pytest.raises(SourceFormatError, match="ISIN NUMBER"):
        sources.parse_nse_equity_list("SYMBOL,NAME OF COMPANY,SERIES\nRELIANCE,Reliance,EQ\n")


def test_etf_name_prefers_a_real_fund_name():
    by_symbol = {r.symbol: r for r in sources.parse_nse_etf_list(NSE_ETF)}
    assert by_symbol["JUNIORBEES"].name == "Nippon India ETF Nifty Next 50 Junior BeES"
    # "Nifty 50" is just the index, so it's labelled rather than used as the fund's name.
    assert by_symbol["NIFTYBEES"].name == "NIFTYBEES ETF (Nifty 50)"


def test_amfi_sections_amc_options_and_prices():
    secs, prices = sources.parse_amfi_navall(AMFI)
    by_isin = {s.isin: s for s in secs}

    growth = by_isin["INF179K01YV8"]
    assert growth.name == "HDFC Large Cap Fund - Direct Plan - Growth"  # same shape as the seed data
    assert growth.metadata == {
        "scheme_code": "119018",
        "amc": "HDFC Mutual Fund",
        "category": "Equity Scheme - Large Cap Fund",
        "structure": "Open Ended",
        "plan": "Direct",
        "option": "Growth",
    }
    # One row, two ISINs: each is its own security (CLAUDE.md).
    assert by_isin["INF179K01YW6"].name.endswith("IDCW - Payout")
    assert by_isin["INF179K01YX4"].name.endswith("IDCW - Reinvestment")
    assert by_isin["INF204KB14I2"].metadata["amc"] == "Nippon India Mutual Fund"

    # The N.A. row keeps its security but gets no price.
    assert "INF179K01YZ9" in by_isin
    nav = {p.isin: p for p in prices}
    assert "INF179K01YZ9" not in nav
    assert nav["INF179K01YV8"].price == Decimal("1234.5678")
    assert nav["INF179K01YV8"].price_date == date(2026, 9, 28)


def test_amfi_layout_change_fails_loudly():
    with pytest.raises(SourceFormatError, match="Net Asset Value"):
        sources.parse_amfi_navall("Scheme Code;ISIN;Scheme Name;NAV;Date\n1;INF179K01YV8;X;1;28-Sep-2026\n")


def test_bhavcopy_parses_decimal_closes():
    rows = sources.parse_bhavcopy(BHAV_HEADER + _bhav_row("INE002A01018", "RELIANCE", "EQ", "1450.50"), "NSE")
    assert rows[0].close == Decimal("1450.50") and rows[0].trade_date == date(2026, 9, 28)


def test_equity_share_isin_filter():
    assert sources.is_equity_share_isin("INE002A01018")  # Reliance shares
    assert not sources.is_equity_share_isin("INE002A07015")  # a debenture of the same issuer
    assert not sources.is_equity_share_isin("INF204KB14I2")  # a fund unit
    assert not sources.is_equity_share_isin("IN0020230085")  # a government bond


# --- merge ----------------------------------------------------------------------------------


def _merged(bse_text: str = "", sectors: str = NIFTY500):
    bse = sources.parse_bhavcopy(BHAV_HEADER + bse_text, "BSE") if bse_text else []
    amfi, _ = sources.parse_amfi_navall(AMFI)
    return {
        s.isin: s
        for s in merge.merge_securities(
            sources.parse_nse_equity_list(NSE_EQUITY),
            sources.parse_nse_etf_list(NSE_ETF),
            amfi,
            bse,
            sources.parse_nifty500_sectors(sectors),
        )
    }


def test_merge_one_row_per_isin_with_both_exchange_symbols():
    m = _merged(_bhav_row("INE002A01018", "RELIANCE", "A", "1451", "BSE", "500325"))
    rel = m["INE002A01018"]
    assert rel.exchange == "NSE"
    assert rel.metadata["nse_symbol"] == "RELIANCE" and rel.metadata["bse_code"] == "500325"
    assert rel.sector == "Oil Gas & Consumable Fuels"
    assert m["INE144J01027"].sector is None  # not in the Nifty 500 -> left unclassified


def test_etf_listed_by_amfi_stays_an_etf_with_scheme_info():
    etf = _merged()["INF204KB14I2"]
    assert etf.type == "ETF" and etf.exchange == "NSE"
    assert etf.metadata["scheme_code"] == "140088" and etf.metadata["nse_symbol"] == "NIFTYBEES"


def test_bse_only_equity_added_but_bse_bonds_ignored():
    m = _merged(
        _bhav_row("INE999Z01011", "BSEONLY", "B", "12", "BSE", "540000", "BSE ONLY LTD")
        + _bhav_row("INE999Z07018", "NCD", "F", "1000", "BSE", "950000", "SOME NCD")
    )
    assert m["INE999Z01011"].exchange == "BSE" and m["INE999Z01011"].type == "STOCK"
    assert "INE999Z07018" not in m


def test_minimum_row_counts_block_a_truncated_download():
    merge.check_minimums({"NSE equity list": 2300, "NSE ETF list": 300, "AMFI NAVAll": 15000})
    with pytest.raises(SourceFormatError, match="AMFI NAVAll has 12 rows"):
        merge.check_minimums({"NSE equity list": 2300, "NSE ETF list": 300, "AMFI NAVAll": 12})


def test_price_precedence_nse_then_bse_then_amfi_and_eq_series_preferred():
    nse = sources.parse_bhavcopy(
        BHAV_HEADER
        + _bhav_row("INE002A01018", "RELIANCE", "BL", "1400")  # block-deal series first
        + _bhav_row("INE002A01018", "RELIANCE", "EQ", "1450"),
        "NSE",
    )
    bse = sources.parse_bhavcopy(
        BHAV_HEADER
        + _bhav_row("INE002A01018", "RELIANCE", "A", "1451", "BSE")
        + _bhav_row("INE999Z01011", "BSEONLY", "B", "12", "BSE"),
        "BSE",
    )
    _, amfi = sources.parse_amfi_navall(AMFI)
    prices = {p.isin: p for p in merge.pick_prices(nse, bse, amfi)}

    assert (prices["INE002A01018"].price, prices["INE002A01018"].source) == (Decimal("1450"), "NSE")
    assert prices["INE999Z01011"].source == "BSE"
    assert prices["INF179K01YV8"].source == "AMFI"


# --- internal endpoints ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/internal/sync-securities", "/api/v1/internal/refresh-prices"])
def test_internal_jobs_need_the_secret(path):
    client = TestClient(app)
    assert client.post(path).status_code == 401
    assert client.post(path, headers={"X-Internal-Secret": "wrong"}).status_code == 401
