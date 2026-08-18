"""
Phase 3b (News & sentiment layer) -- News acquisition via Marketaux.

Fetches recent financial news for your watchlist stocks from Marketaux
(https://api.marketaux.com/v1/news/all), which tags each article with
the entities (tickers) it mentions and a built-in numeric sentiment
score per entity (-1 to 1).

IMPORTANT -- CONFIRMED issue, not a hypothetical caveat: an initial live
test showed Marketaux matching "TCS" to an unrelated US company (The
Container Store Group, also ticker TCS on a US exchange) with country
tagged "us", and "INFY" only matched correctly because Infosys happens
to also trade as an NYSE ADR under the same symbol. Bare ticker symbols
are NOT globally unique, and Marketaux's free-tier symbol matching does
not appear to disambiguate by exchange unless explicitly told to.

Fix applied: fetch_news_raw() now REQUIRES countries="in" as a filter
(not optional), and parse_articles() takes an expected_symbols set to
defensively drop any entity match outside your actual watchlist, as a
second line of defense. Even so:
    1. Run --debug for one batch FIRST after any change here.
    2. Manually confirm returned entities are your actual Indian
       companies (check company name, not just symbol).
    3. Only then trust batch runs.
This is a stronger version of the same discipline applied to NSE's
JSON field names -- here the failure mode is worse (silently wrong
data, not just missing data), so verification matters more, not less.

Marketaux's free tier is roughly 100 requests/day. This script fetches
all watchlist symbols in a SINGLE request (symbols=A,B,C,...) rather
than one request per stock, since the API supports comma-separated
symbols -- this matters a lot for staying well within that quota.
"""

from __future__ import annotations

import os
import json
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

from src.news.news_log import NewsLog

load_dotenv()

logger = logging.getLogger(__name__)

MARKETAUX_BASE = "https://api.marketaux.com/v1/news/all"

# Overrides for watchlist symbols that don't follow Marketaux's standard
# "{NSE_SYMBOL}.NS" pattern. Confirmed via /entity/search diagnostics
# (see load_watchlist_symbols docstring) -- add entries here only after
# confirming the correct identifier with --search, not by guessing.
MARKETAUX_SYMBOL_OVERRIDES = {
    "TCS.NS": "TCS-BL.NS",  # bare "TCS.NS"/"TCS" collides with an unrelated US entity
}


def load_watchlist_symbols(path: Path = Path("watchlist.yaml")) -> tuple[list[str], dict[str, str]]:
    """
    Loads watchlist.yaml and returns (marketaux_query_symbols,
    marketaux_to_canonical_map).

    marketaux_query_symbols: what to actually send to Marketaux's API
    (e.g. "RELIANCE.NS", "TCS-BL.NS" per MARKETAUX_SYMBOL_OVERRIDES).

    marketaux_to_canonical_map: maps what Marketaux RETURNS (e.g.
    "RELIANCE.NS", "TCS-BL.NS") back to the bare symbol convention
    used elsewhere in this project (RELIANCE, TCS) -- same convention
    src/rag/rag_query.py's --symbol flag uses. Without this mapping,
    news_report.py would need callers to pass "TCS-BL.NS" instead of
    "TCS", which is inconsistent and easy to get wrong.
    """
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f)

    raw_symbols = data.get("stocks", [])  # e.g. ["RELIANCE.NS", "TCS.NS", ...]

    query_symbols = []
    canonical_map = {}
    for raw in raw_symbols:
        canonical = raw.rsplit(".", 1)[0] if raw.endswith((".NS", ".BO")) else raw
        marketaux_symbol = MARKETAUX_SYMBOL_OVERRIDES.get(raw, raw)
        query_symbols.append(marketaux_symbol)
        canonical_map[marketaux_symbol] = canonical

    return query_symbols, canonical_map


def fetch_news_raw(symbols: list[str], api_token: str, language: str = "en", countries: str = "in") -> dict:
    """
    Single request for all symbols at once (comma-separated), to
    conserve the free tier's daily quota.

    countries="in" is REQUIRED, not optional, based on a confirmed live
    failure: without it, bare symbol matching collided "TCS" with an
    unrelated US company (The Container Store Group, also ticker TCS
    on a US exchange) and returned country="us" even for INFY, which
    only matched because Infosys happens to also trade as an NYSE ADR
    under the same symbol. Bare tickers are not globally unique --
    country filtering is the fix, not a nice-to-have.

    Still verify manually after this change (see debug_raw_response) --
    a country filter narrows the search space but doesn't guarantee
    every remaining match is correct; Marketaux's entity resolution
    quality for NSE-specific tickers beyond your 5 watchlist stocks is
    unconfirmed.
    """
    params = {
        "symbols": ",".join(symbols),
        "filter_entities": "true",
        "language": language,
        "countries": countries,
        "api_token": api_token,
    }
    resp = requests.get(MARKETAUX_BASE, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_articles(
    raw_response: dict,
    expected_symbols: set[str] | None = None,
    canonical_map: dict[str, str] | None = None,
) -> list[dict]:
    """
    Extracts (symbol, headline, url, published_at, source,
    marketaux_sentiment_score) tuples from the raw Marketaux response.

    One article can mention multiple entities/symbols -- this yields
    one record PER (article, matched watchlist symbol) pair, so a
    single article about both RELIANCE and a subsidiary would appear
    twice, once per symbol, each with that symbol's own sentiment_score
    (Marketaux scores sentiment per-entity, not per-article, since the
    same article can be positive for one company and negative for
    another it mentions).

    expected_symbols: if given, entities whose symbol isn't in this set
    are silently dropped rather than stored. Defensive backstop against
    the confirmed ticker-collision failure mode (see fetch_news_raw's
    docstring) -- pass the same query_symbols used to fetch (e.g.
    {"RELIANCE.NS", "TCS-BL.NS", ...}), not bare symbols.

    canonical_map: if given, maps the stored symbol from Marketaux's
    identifier (e.g. "TCS-BL.NS") back to this project's bare-symbol
    convention (e.g. "TCS") -- keeps news_report.py's --symbol argument
    consistent with src/rag/rag_query.py's.
    """
    records = []
    for article in raw_response.get("data", []):
        headline = article.get("title", "")
        url = article.get("url", "")
        published_at = article.get("published_at")
        source = article.get("source")

        for entity in article.get("entities", []):
            symbol = entity.get("symbol")
            if not symbol:
                continue
            if expected_symbols is not None and symbol not in expected_symbols:
                logger.warning(
                    "Dropping unexpected entity symbol %r (not in watchlist) from "
                    "article %r -- possible ticker collision, see fetch_news_raw docstring.",
                    symbol, headline[:80],
                )
                continue

            stored_symbol = canonical_map.get(symbol, symbol) if canonical_map else symbol

            records.append({
                "symbol": stored_symbol,
                "headline": headline,
                "url": url,
                "published_at": published_at,
                "source": source,
                "marketaux_sentiment_score": entity.get("sentiment_score"),
            })

    return records


def search_entity(company_name: str, api_token: str) -> dict:
    """
    Diagnostic tool -- looks up how Marketaux actually identifies a
    company by name, rather than guessing at symbol format. Use this
    when symbol-based queries return zero results (as happened with
    countries="in" + bare NSE symbols) to see what symbol/exchange/
    country Marketaux actually has on file, before spending more
    quota on guesses.

    Uses /v1/entity/search, a separate lightweight endpoint from
    /v1/news/all -- cheap way to answer "does Marketaux know this
    company at all, and under what identifier" before burning news
    queries on the wrong symbol.
    """
    resp = requests.get(
        "https://api.marketaux.com/v1/entity/search",
        params={"search": company_name, "api_token": api_token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def debug_raw_response(query_symbols: list[str], api_token: str) -> None:
    """
    Run this FIRST, before trusting parse_articles(). Prints the raw
    response so you can manually confirm: (1) articles are actually
    about your Indian companies -- check the entity "name" field, not
    just the symbol, given the confirmed TCS/Container-Store collision
    -- (2) the entity/symbol field names match what parse_articles()
    expects.
    """
    raw = fetch_news_raw(query_symbols, api_token, countries="in")
    print(json.dumps(raw, indent=2)[:5000])  # truncated -- full response can be large
    print(f"\n... (truncated). Total articles returned: {len(raw.get('data', []))}")


def fetch_and_store(query_symbols: list[str], api_token: str, log: NewsLog, canonical_map: dict[str, str]) -> int:
    raw = fetch_news_raw(query_symbols, api_token)
    records = parse_articles(raw, expected_symbols=set(query_symbols), canonical_map=canonical_map)

    stored = 0
    for rec in records:
        if log.article_exists(rec["url"]):
            continue
        log.record_article(
            article_url=rec["url"],
            symbol=rec["symbol"],
            headline=rec["headline"],
            published_at=rec["published_at"],
            source=rec["source"],
            marketaux_sentiment_score=rec["marketaux_sentiment_score"],
        )
        stored += 1

    return stored


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    api_token = os.environ.get("MARKETAUX_API_TOKEN")
    if not api_token:
        print("MARKETAUX_API_TOKEN not set in .env -- get a free token at marketaux.com and add it.")
        raise SystemExit(1)

    query_symbols, canonical_map = load_watchlist_symbols()

    if "--debug" in sys.argv:
        # Run this once first to sanity-check the API response shape
        # and confirm ticker matching before trusting batch runs.
        debug_raw_response(query_symbols, api_token)
        raise SystemExit(0)

    if "--search" in sys.argv:
        # Diagnostic: look up one company by name to see how Marketaux
        # actually identifies it, rather than guessing at symbol
        # format. Usage: python -m src.news.fetch_news --search "Reliance Industries"
        idx = sys.argv.index("--search")
        if idx + 1 >= len(sys.argv):
            print('Usage: python -m src.news.fetch_news --search "Company Name"')
            raise SystemExit(1)
        company_name = sys.argv[idx + 1]
        result = search_entity(company_name, api_token)
        print(json.dumps(result, indent=2))
        raise SystemExit(0)

    log = NewsLog()
    count = fetch_and_store(query_symbols, api_token, log, canonical_map)
    log.close()
    print(f"Stored {count} new article(s). Run analyze_sentiment.py next.")