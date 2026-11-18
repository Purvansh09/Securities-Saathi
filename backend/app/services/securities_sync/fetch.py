"""Downloads. The only module in the sync that touches the network.

URLs verified on the publishers' sites on 2026-09-29. NSE's archive host rejects requests
without a browser-like User-Agent (CLAUDE.md), and we only use its static archive files, never
the live NSE web pages. BSE's scrip-list API is behind bot protection (403 even with browser
headers), so BSE data comes from its bhavcopy file, which is published openly.
"""

import asyncio
import io
import ssl
import zipfile
from datetime import date, timedelta

import httpx

NSE_EQUITY_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_ETF_LIST = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"
NSE_NIFTY500 = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
AMFI_NAVALL = "https://portal.amfiindia.com/spages/NAVAll.txt"


def _nse_bhav_url(d: date) -> str:
    return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def _bse_bhav_url(d: date) -> str:
    return f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d:%Y%m%d}_F_0000.CSV"


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def make_client() -> httpx.AsyncClient:
    # ssl.create_default_context() trusts the operating system's certificate store rather than
    # certifi's bundle. On a machine whose antivirus inspects HTTPS (e.g. Avast), that's the
    # store its root certificate is installed in; on Linux/CI it's the normal system CAs.
    return httpx.AsyncClient(
        headers=_HEADERS, timeout=httpx.Timeout(60.0), follow_redirects=True, verify=ssl.create_default_context()
    )


async def _get(client: httpx.AsyncClient, url: str, *, referer: str | None = None) -> bytes | None:
    """Body bytes, or None for a 404 (a file that doesn't exist, e.g. a holiday's bhavcopy).
    Transient failures (network, 5xx) are retried twice; anything else raises."""
    headers = {"Referer": referer} if referer else None
    for attempt in range(3):
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 404:
                return None
            if resp.status_code >= 500 and attempt < 2:
                raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.content
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 * (attempt + 1))
    return None  # unreachable


def _decode(body: bytes) -> str:
    return body.decode("utf-8-sig", errors="replace")


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    body = await _get(client, url)
    if body is None:
        raise httpx.HTTPError(f"{url} returned 404")
    return _decode(body)


async def fetch_bhavcopy(
    client: httpx.AsyncClient, exchange: str, on_or_before: date, max_days_back: int = 7
) -> tuple[date, str] | None:
    """The most recent bhavcopy on or before the given date. Walks back over weekends and
    exchange holidays, which simply have no file. Returns (trade date, CSV text) or None."""
    for back in range(max_days_back + 1):
        d = on_or_before - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        if exchange == "NSE":
            body = await _get(client, _nse_bhav_url(d))
            if body is None:
                continue
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                if name is None:
                    continue
                text = _decode(zf.read(name))
        else:
            body = await _get(client, _bse_bhav_url(d), referer="https://www.bseindia.com/")
            if body is None:
                continue
            text = _decode(body)
        # Some servers answer a missing file with an HTML page and a 200 instead of a 404.
        if text.lstrip().startswith("TradDt"):
            return d, text
    return None
