"""
Tests for the Phase 3b news/sentiment layer.

Same philosophy as test_rag_pipeline.py: no live network, DB, or
Claude API calls. Focus on parsing correctness (parse_articles),
idempotency (NewsLog), and the JSON-parsing fallback in
analyze_sentiment.py -- the actual failure modes likely here, not
exhaustive coverage.

Marketaux's live response shape is UNVERIFIED (see fetch_news.py's
module docstring) -- these tests validate parse_articles() against
the response shape documented in Marketaux's public docs, not a
confirmed live response. If the real API returns a different shape,
these tests will not catch that; only a manual --debug run will.
"""

from __future__ import annotations

import json

import pytest

from src.news.fetch_news import parse_articles
from src.news.news_log import NewsLog
from src.news.analyze_sentiment import analyze_headline


# ---------- parse_articles ----------

def test_parse_articles_empty_response_returns_empty_list():
    assert parse_articles({"data": []}) == []


def test_parse_articles_missing_data_key_returns_empty_list():
    # defensive: a malformed/error response shouldn't crash parsing
    assert parse_articles({}) == []


def test_parse_articles_extracts_expected_fields():
    raw = {
        "data": [
            {
                "title": "Reliance wins large energy contract",
                "url": "https://example.com/article1",
                "published_at": "2026-08-01T10:00:00.000000Z",
                "source": "example.com",
                "entities": [
                    {"symbol": "RELIANCE.NS", "sentiment_score": 0.42},
                ],
            }
        ]
    }
    records = parse_articles(raw)
    assert len(records) == 1
    assert records[0]["symbol"] == "RELIANCE.NS"  # no canonical_map given -> raw symbol kept as-is
    assert records[0]["headline"] == "Reliance wins large energy contract"
    assert records[0]["marketaux_sentiment_score"] == 0.42


def test_parse_articles_applies_canonical_map():
    # this is the actual fix for the TCS irregularity: Marketaux
    # returns "TCS-BL.NS", but stored/reported symbol should be the
    # bare "TCS" convention used everywhere else in the project.
    raw = {
        "data": [
            {
                "title": "TCS reports quarterly results",
                "url": "https://example.com/real-tcs",
                "published_at": "2026-08-01T10:00:00.000000Z",
                "source": "moneycontrol.com",
                "entities": [
                    {"symbol": "TCS-BL.NS", "name": "Tata Consultancy Services", "sentiment_score": 0.3},
                ],
            }
        ]
    }
    records = parse_articles(raw, canonical_map={"TCS-BL.NS": "TCS"})
    assert len(records) == 1
    assert records[0]["symbol"] == "TCS"


def test_parse_articles_drops_symbol_not_in_expected_set():
    # regression guard for the confirmed live failure: bare "TCS"
    # matched an unrelated US company (The Container Store Group) in
    # a real Marketaux response. expected_symbols is the defensive
    # backstop that must filter this out even if the API-level filter
    # somehow lets a stray match through.
    raw = {
        "data": [
            {
                "title": "Bed Bath & Beyond rebrand news",
                "url": "https://example.com/wrong-tcs",
                "published_at": "2026-08-05T17:48:09.000000Z",
                "source": "forbes.com",
                "entities": [
                    {"symbol": "TCS", "name": "The Container Store Group, Inc.", "sentiment_score": -0.10},
                ],
            }
        ]
    }
    # bare "TCS" is NOT a query symbol this project ever sends (the
    # correct one is "TCS-BL.NS") -- so this must be dropped regardless.
    records = parse_articles(raw, expected_symbols={"RELIANCE.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "TCS-BL.NS"})
    assert records == []


def test_parse_articles_keeps_symbol_in_expected_set():
    raw = {
        "data": [
            {
                "title": "TCS reports quarterly results",
                "url": "https://example.com/real-tcs",
                "published_at": "2026-08-01T10:00:00.000000Z",
                "source": "moneycontrol.com",
                "entities": [
                    {"symbol": "TCS-BL.NS", "name": "Tata Consultancy Services", "sentiment_score": 0.3},
                ],
            }
        ]
    }
    records = parse_articles(raw, expected_symbols={"TCS-BL.NS", "INFY.NS"})
    assert len(records) == 1
    assert records[0]["symbol"] == "TCS-BL.NS"  # no canonical_map passed in this test -> unmapped


def test_parse_articles_one_article_multiple_entities_yields_multiple_records():
    # an article mentioning two watchlist symbols should produce two
    # records, one per symbol, each with that entity's own sentiment score
    raw = {
        "data": [
            {
                "title": "TCS and Infosys both report Q1 growth",
                "url": "https://example.com/article2",
                "published_at": "2026-08-01T10:00:00.000000Z",
                "source": "example.com",
                "entities": [
                    {"symbol": "TCS", "sentiment_score": 0.3},
                    {"symbol": "INFY", "sentiment_score": 0.5},
                ],
            }
        ]
    }
    records = parse_articles(raw)
    assert len(records) == 2
    symbols = {r["symbol"] for r in records}
    assert symbols == {"TCS", "INFY"}


def test_parse_articles_skips_entities_without_symbol():
    raw = {
        "data": [
            {
                "title": "Vague market commentary",
                "url": "https://example.com/article3",
                "published_at": "2026-08-01T10:00:00.000000Z",
                "source": "example.com",
                "entities": [{"sentiment_score": 0.1}],  # no symbol field
            }
        ]
    }
    assert parse_articles(raw) == []


# ---------- NewsLog (idempotency) ----------

def test_news_log_new_article_does_not_exist(tmp_path):
    log = NewsLog(db_path=tmp_path / "test_news.db")
    assert log.article_exists("https://example.com/a") is False
    log.close()


def test_news_log_record_then_exists(tmp_path):
    log = NewsLog(db_path=tmp_path / "test_news.db")
    log.record_article("https://example.com/a", "RELIANCE", "Headline", "2026-08-01", "src.com", 0.5)
    assert log.article_exists("https://example.com/a") is True
    log.close()


def test_news_log_double_record_does_not_duplicate(tmp_path):
    log = NewsLog(db_path=tmp_path / "test_news.db")
    log.record_article("https://example.com/a", "RELIANCE", "Headline", "2026-08-01", "src.com", 0.5)
    log.record_article("https://example.com/a", "RELIANCE", "Headline", "2026-08-01", "src.com", 0.5)

    cur = log._conn.execute("SELECT COUNT(*) FROM articles")
    assert cur.fetchone()[0] == 1
    log.close()


def test_news_log_needs_claude_analysis_returns_unanalyzed_only(tmp_path):
    log = NewsLog(db_path=tmp_path / "test_news.db")
    log.record_article("https://example.com/a", "RELIANCE", "Headline A", "2026-08-01", "src.com", 0.5)
    log.record_article("https://example.com/b", "TCS", "Headline B", "2026-08-01", "src.com", -0.2)

    # analyze one of them
    log.record_claude_analysis("https://example.com/a", "positive", "some reasoning")

    pending = log.needs_claude_analysis()
    pending_urls = {p["article_url"] for p in pending}
    assert pending_urls == {"https://example.com/b"}
    log.close()


def test_news_log_record_claude_analysis_updates_correct_row(tmp_path):
    log = NewsLog(db_path=tmp_path / "test_news.db")
    log.record_article("https://example.com/a", "RELIANCE", "Headline A", "2026-08-01", "src.com", 0.5)
    log.record_claude_analysis("https://example.com/a", "mixed", "Good deal but margin concerns.")

    results = log.get_recent_for_symbol("RELIANCE")
    assert len(results) == 1
    assert results[0]["claude_sentiment_label"] == "mixed"
    assert "margin concerns" in results[0]["claude_reasoning"]
    log.close()


def test_news_log_get_recent_for_symbol_filters_correctly(tmp_path):
    log = NewsLog(db_path=tmp_path / "test_news.db")
    log.record_article("https://example.com/a", "RELIANCE", "Headline A", "2026-08-01", "src.com", 0.5)
    log.record_article("https://example.com/b", "TCS", "Headline B", "2026-08-01", "src.com", -0.2)

    results = log.get_recent_for_symbol("RELIANCE")
    assert len(results) == 1
    assert results[0]["headline"] == "Headline A"
    log.close()


# ---------- analyze_headline JSON parsing ----------

class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeClient:
    def __init__(self, response_text):
        self._response_text = response_text
        self.messages = self

    def create(self, **kwargs):
        return _FakeResponse(self._response_text)


def test_analyze_headline_parses_valid_json():
    fake_client = _FakeClient(json.dumps({"label": "positive", "reasoning": "Strong deal win."}))
    label, reasoning = analyze_headline("Some headline", fake_client)
    assert label == "positive"
    assert reasoning == "Strong deal win."


def test_analyze_headline_handles_malformed_json_gracefully():
    # if Claude ever wraps the JSON in explanation despite instructions,
    # this must not crash -- it should fall back to a labeled "unparsed"
    # state with the raw text preserved for debugging, not silently lose it.
    fake_client = _FakeClient("Sure! Here's the analysis: positive, strong deal.")
    label, reasoning = analyze_headline("Some headline", fake_client)
    assert label == "unparsed"
    assert "positive" in reasoning