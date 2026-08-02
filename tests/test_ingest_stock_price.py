"""
Tests for the core upsert/idempotency guarantee in ingest_stock_price.py.
Network calls are mocked — these tests never hit the real yfinance API.
"""

import pandas as pd
from datetime import datetime

import src.ingestion.ingest_stock_price as mod
from src.models.schema import Security, PriceHistory, IngestionLog


def _fake_yfinance_response():
    """A small, deterministic stand-in for what yfinance.Ticker returns."""
    info = {
        "longName": "Fake Corp Limited",
        "sector": "Technology",
        "industry": "Software",
        "isin": "INE000TEST01",
        "trailingPE": 25.0,
        "priceToBook": 5.0,
        "debtToEquity": 10.0,
        "returnOnEquity": 0.30,
        "marketCap": 1_000_000_000,
        "bookValue": 50.0,
        "trailingEps": 10.0,
        "dividendYield": 0.01,
    }
    dates = pd.date_range(end=datetime.today(), periods=5, freq="D")
    hist = pd.DataFrame({
        "Open": [100, 101, 102, 103, 104],
        "High": [105, 106, 107, 108, 109],
        "Low": [95, 96, 97, 98, 99],
        "Close": [102, 103, 104, 105, 106],
        "Volume": [1000, 1100, 1200, 1300, 1400],
    }, index=dates)
    return info, hist


def test_ingest_creates_expected_rows(test_session_factory, monkeypatch):
    """Baseline: a single ingest() call writes exactly the rows we fed it."""
    info, hist = _fake_yfinance_response()
    monkeypatch.setattr(mod, "_fetch_from_yfinance", lambda symbol: (info, hist))

    mod.ingest("FAKE.NS")

    session = test_session_factory()
    try:
        security = session.query(Security).filter_by(symbol="FAKE.NS").one()
        assert security.name == "Fake Corp Limited"
        price_rows = session.query(PriceHistory).filter_by(security_id=security.id).count()
        assert price_rows == 5
    finally:
        session.close()


def test_ingest_twice_does_not_duplicate_rows(test_session_factory, monkeypatch):
    """The idempotency guarantee: running ingest() twice for the same symbol
    must never double the price_history row count."""
    info, hist = _fake_yfinance_response()
    monkeypatch.setattr(mod, "_fetch_from_yfinance", lambda symbol: (info, hist))

    # Force both calls to actually hit the "fetch" path (bypassing the
    # time-based skip) by clearing the skip window for this test.
    monkeypatch.setattr(mod, "SKIP_IF_FETCHED_WITHIN_HOURS", 0)

    mod.ingest("FAKE.NS")
    mod.ingest("FAKE.NS")

    session = test_session_factory()
    try:
        security = session.query(Security).filter_by(symbol="FAKE.NS").one()
        price_rows = session.query(PriceHistory).filter_by(security_id=security.id).count()
        assert price_rows == 5, "duplicate rows were created on the second ingest() run"

        # Exactly one security row too — upsert, not insert-again.
        security_count = session.query(Security).filter_by(symbol="FAKE.NS").count()
        assert security_count == 1
    finally:
        session.close()


def test_second_call_within_skip_window_does_not_refetch(test_session_factory, monkeypatch):
    """If we just fetched successfully, a second call within the skip window
    should skip the network call entirely."""
    info, hist = _fake_yfinance_response()
    call_count = {"n": 0}

    def counting_fetch(symbol):
        call_count["n"] += 1
        return info, hist

    monkeypatch.setattr(mod, "_fetch_from_yfinance", counting_fetch)
    monkeypatch.setattr(mod, "SKIP_IF_FETCHED_WITHIN_HOURS", 12)  # default-like window

    mod.ingest("FAKE.NS")
    mod.ingest("FAKE.NS")

    assert call_count["n"] == 1, "second call should have been skipped, not re-fetched"


def test_failed_symbol_logs_failure_and_raises(test_session_factory, monkeypatch):
    """A bad symbol should log a 'failed' ingestion_log row (not silently
    vanish) and re-raise, so an orchestrator can catch it and move on."""
    def broken_fetch(symbol):
        raise ValueError("No price history returned for BADSYM.NS")

    monkeypatch.setattr(mod, "_fetch_from_yfinance", broken_fetch)

    raised = False
    try:
        mod.ingest("BADSYM.NS")
    except ValueError:
        raised = True

    assert raised, "ingest() should propagate the failure, not swallow it"

    session = test_session_factory()
    try:
        failed_logs = session.query(IngestionLog).filter_by(status="failed").all()
        assert len(failed_logs) == 1
        assert "No price history" in failed_logs[0].error_message
        # No security should exist for a symbol whose fetch never succeeded.
        assert session.query(Security).filter_by(symbol="BADSYM.NS").count() == 0
    finally:
        session.close()
