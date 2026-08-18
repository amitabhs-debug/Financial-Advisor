"""
Phase 3 (LLM/RAG layer) — Document acquisition.

Fetches quarterly financial results PDFs from NSE's corporate filings
system, per watchlist stock, and saves them locally for the chunking/
embedding stage.

IMPORTANT — a known NSE quirk (document this in your README the way you
documented Screener.in's manual-export limitation):

NSE has no official public API. The data behind
https://www.nseindia.com/companies-listing/corporate-filings-financial-results
is served by an internal API that NSE's own frontend JS calls. That
internal API rejects plain requests — no session, no browser-like
headers, no cookies -> you get a 403 or an empty/garbage response.

The workaround (same one every open-source NSE scraper uses, e.g.
nsepython, jugaad-data): hit nseindia.com's homepage first with a
realistic User-Agent to receive session cookies, then reuse that
same `requests.Session` (cookies + headers) for the actual API call.
This is fragile by nature — NSE can change internal endpoint paths,
tighten headers checks, or rate-limit without notice. If this script
starts silently returning empty results, check that first before
assuming a code bug.

This script is idempotent: re-running it will skip filings already
recorded in document_log.py's tracking file, same idempotency
principle as ingest_stock_price.py in Phase 1.
"""

from __future__ import annotations

import time
import logging
from pathlib import Path
from dataclasses import dataclass

import requests

from src.rag.document_log import DocumentLog

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
# Internal API endpoint NSE's frontend uses for financial-results filings.
# Verified against the corporate-filings-financial-results page as of
# Aug 2026 -- NSE has changed this path before without notice, so if
# fetch_filing_list() starts returning empty lists, check this first.
NSE_FILINGS_API = f"{NSE_BASE}/api/corporate-results"

# NSE's anti-bot checks now inspect a much fuller set of headers than
# a basic User-Agent + Accept -- real browsers send sec-ch-ua,
# sec-fetch-*, layered accept-language, cache-control, etc. A thin
# header set (what an earlier version of this script used) gets a
# 403 even with valid cookies. This set mirrors what maintained NSE
# scraper libraries (nsepython, NseIndiaApi) actually send as of 2026.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9,en-IN;q=0.8,en-GB;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="124", "Not:A-Brand";v="99", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Referer": f"{NSE_BASE}/companies-listing/corporate-filings-financial-results",
}

# The actual page (not just the homepage) that real browsers land on
# before the filings API gets called -- hitting this after the
# homepage picks up additional session cookies NSE checks for. Working
# scrapers do a two-step warm-up (homepage, then a real content page)
# rather than a single homepage hit.
NSE_WARMUP_PAGE = f"{NSE_BASE}/companies-listing/corporate-filings-financial-results"

DOWNLOAD_DIR = Path("data/filings/quarterly")


@dataclass
class FilingRecord:
    symbol: str
    company_name: str
    filing_date: str  # ISO date string as reported by NSE
    pdf_url: str
    period: str | None = None  # e.g. "Q1FY26" if NSE exposes it cleanly


def get_nse_session() -> requests.Session:
    """
    Bootstrap a requests.Session with valid NSE cookies.

    NSE's internal API silently rejects requests that don't carry
    session cookies AND a full browser-like header set obtained from
    a prior real visit. A single homepage hit is not sufficient --
    maintained NSE scraper libraries do a two-step warm-up: homepage
    first, then a real content page (the filings page itself), before
    the API call. This mirrors that pattern.

    If this still 403s after both warm-up steps, NSE has likely
    changed something else (a new header check, a JS challenge that
    plain requests can't satisfy at all, IP-based rate limiting, etc).
    At that point, before debugging headers further, it's worth
    switching to a maintained library (nsepython or jugaad-data) that
    tracks NSE's anti-bot changes on an ongoing basis -- this hand-
    rolled session is inherently playing catch-up to whatever NSE
    does next, the same way any scraper of a site without an official
    API always will be.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # Step 1: homepage, to receive the base session cookies.
    resp = session.get(NSE_BASE, timeout=10)
    resp.raise_for_status()
    time.sleep(1)

    # Step 2: a real content page (not the API) -- this is the step
    # that was missing before. NSE sets additional cookies here that
    # the bare homepage hit doesn't provide, and the API call 403s
    # without them even when the homepage cookies were captured fine.
    resp = session.get(NSE_WARMUP_PAGE, timeout=10)
    resp.raise_for_status()
    time.sleep(1)

    return session


def fetch_filing_list(session: requests.Session, symbol: str) -> list[FilingRecord]:
    """
    Query NSE's corporate-results API for a given symbol (e.g. 'RELIANCE',
    NOT 'RELIANCE.NS' -- NSE's internal API uses bare symbols, unlike
    yfinance's `.NS` suffix convention. Strip the suffix before calling.

    Returns an empty list (not an exception) if NSE returns no filings
    or the response shape is unexpected -- callers should treat an
    empty list as "nothing to do," and check logs for the underlying
    reason (network issue vs. genuinely no new filings) via the log
    messages this function emits.
    """
    params = {
        "index": "equities",
        "symbol": symbol,
    }

    try:
        resp = session.get(NSE_FILINGS_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("NSE request failed for %s: %s", symbol, exc)
        return []
    except ValueError as exc:
        # response wasn't valid JSON -- usually means we got an HTML
        # block/challenge page back instead of the API response, which
        # is NSE's signal that the session/headers weren't accepted.
        logger.error(
            "NSE response for %s was not valid JSON (likely a session/"
            "header rejection, not a data issue): %s", symbol, exc
        )
        return []

    records: list[FilingRecord] = []
    for item in data if isinstance(data, list) else data.get("data", []):
        pdf_url = item.get("xbrlAttachment") or item.get("attachment")
        if not pdf_url:
            continue
        records.append(
            FilingRecord(
                symbol=symbol,
                company_name=item.get("companyName", symbol),
                filing_date=item.get("filingDate") or item.get("broadcastDate", ""),
                pdf_url=pdf_url,
                period=item.get("period"),
            )
        )

    return records


def download_filing(session: requests.Session, record: FilingRecord, dest_dir: Path) -> Path | None:
    """Download a single filing PDF, returns the local path or None on failure."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_date = record.filing_date.replace("/", "-").replace(" ", "_") or "unknown-date"
    filename = f"{record.symbol}_{safe_date}.pdf"
    dest_path = dest_dir / filename

    if dest_path.exists():
        logger.info("Already downloaded, skipping: %s", filename)
        return dest_path

    try:
        resp = session.get(record.pdf_url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to download %s: %s", record.pdf_url, exc)
        return None

    if resp.headers.get("Content-Type", "").lower() not in ("application/pdf", "binary/octet-stream"):
        # NSE sometimes serves an HTML error page with a 200 status
        # instead of a real 404 -- content-sniffing catches what a
        # status-code check alone would miss.
        logger.warning(
            "Unexpected content-type for %s (%s) -- saving anyway but "
            "flag for manual check", record.pdf_url, resp.headers.get("Content-Type")
        )

    dest_path.write_bytes(resp.content)
    logger.info("Downloaded: %s", filename)
    return dest_path


def fetch_all(symbols: list[str], log: DocumentLog) -> list[Path]:
    """
    Fetch new quarterly filings for every symbol in the watchlist.
    Symbols should be bare NSE symbols (RELIANCE, TCS, ...), not the
    `.NS`-suffixed yfinance form -- strip suffix at the call site if
    reusing watchlist.yaml directly.
    """
    session = get_nse_session()
    downloaded: list[Path] = []

    for symbol in symbols:
        records = fetch_filing_list(session, symbol)
        if not records:
            logger.info("No filings found for %s (or request failed -- check logs above)", symbol)
            continue

        for record in records:
            if log.already_processed(record.symbol, record.filing_date, record.pdf_url):
                continue

            path = download_filing(session, record, DOWNLOAD_DIR)
            if path:
                downloaded.append(path)
                log.record(record.symbol, record.filing_date, record.pdf_url, str(path))

            time.sleep(1)  # be polite between downloads, same as between requests

    return downloaded


def load_watchlist_symbols(path: Path = Path("watchlist.yaml")) -> list[str]:
    """
    Loads watchlist.yaml (the same file Phase 1 ingestion uses) and
    strips the yfinance `.NS` / `.BO` suffix, since NSE's internal API
    takes bare symbols (RELIANCE, not RELIANCE.NS).

    Note: this scraper only targets NSE, so `.BO` (BSE) symbols in the
    watchlist are skipped with a warning rather than silently mis-fetched
    -- if you add BSE-listed stocks later, this filing source won't
    cover them and you'd need a separate BSE scraper.
    """
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f)

    raw_symbols = data.get("stocks", [])
    nse_symbols = []
    for sym in raw_symbols:
        if sym.endswith(".NS"):
            nse_symbols.append(sym.removesuffix(".NS"))
        else:
            logger.warning("Skipping non-NSE symbol (this scraper is NSE-only): %s", sym)

    return nse_symbols


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    watchlist_symbols = load_watchlist_symbols()

    doc_log = DocumentLog()
    results = fetch_all(watchlist_symbols, doc_log)
    print(f"Downloaded {len(results)} new filing(s).")