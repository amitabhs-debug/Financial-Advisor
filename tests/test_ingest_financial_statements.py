"""
Tests for ingest_financial_statements.py — specifically the currency-tracking
fix (the Infosys USD-vs-INR bug caught during manual testing). These tests
lock that behavior in so a future refactor can't silently reintroduce it.
"""

import pandas as pd

import src.ingestion.ingest_stock_price as stock_mod
import src.ingestion.ingest_financial_statements as mod
from src.models.schema import FinancialStatement


def _fake_stock_response():
    info = {"longName": "Fake Corp Limited", "sector": "Technology", "industry": "Software"}
    dates = pd.date_range(end=pd.Timestamp.today(), periods=3, freq="D")
    hist = pd.DataFrame({
        "Open": [100, 101, 102], "High": [105, 106, 107], "Low": [95, 96, 97],
        "Close": [102, 103, 104], "Volume": [1000, 1100, 1200],
    }, index=dates)
    return info, hist


def _fake_statements(currency: str):
    """Two annual periods, using the exact row-label aliases the real code
    looks for, so this test exercises _get_row's alias-matching too."""
    periods = pd.to_datetime(["2025-03-31", "2024-03-31"])
    income = pd.DataFrame(
        {periods[0]: [1000.0, 100.0], periods[1]: [900.0, 90.0]},
        index=["Total Revenue", "Net Income"],
    )
    balance = pd.DataFrame(
        {periods[0]: [5000.0, 2000.0], periods[1]: [4500.0, 1800.0]},
        index=["Total Assets", "Total Liabilities Net Minority Interest"],
    )
    cashflow = pd.DataFrame(
        {periods[0]: [150.0, 120.0], periods[1]: [140.0, 110.0]},
        index=["Operating Cash Flow", "Free Cash Flow"],
    )
    return income, balance, cashflow, currency


def test_currency_is_recorded_for_inr_company(test_session_factory, monkeypatch):
    info, hist = _fake_stock_response()
    monkeypatch.setattr(stock_mod, "_fetch_from_yfinance", lambda symbol: (info, hist))
    stock_mod.ingest("FAKEINR.NS")

    monkeypatch.setattr(
        mod, "_fetch_statements",
        lambda symbol, period_type: _fake_statements("INR"),
    )
    mod.ingest("FAKEINR.NS")

    session = test_session_factory()
    try:
        stmts = session.query(FinancialStatement).all()
        assert len(stmts) > 0
        assert all(s.currency == "INR" for s in stmts), \
            "every financial_statements row must have a currency recorded"
        # Sanity: the actual figures came through correctly too.
        latest = max(stmts, key=lambda s: s.period_end)
        assert latest.revenue == 1000.0
        assert latest.net_profit == 100.0
    finally:
        session.close()


def test_currency_distinguishes_usd_reporting_company(test_session_factory, monkeypatch):
    """Reproduces the Infosys case: an ADR-listed company whose statements
    come back in USD must be tagged USD, not silently assumed INR."""
    info, hist = _fake_stock_response()
    monkeypatch.setattr(stock_mod, "_fetch_from_yfinance", lambda symbol: (info, hist))
    stock_mod.ingest("FAKEUSD.NS")

    monkeypatch.setattr(
        mod, "_fetch_statements",
        lambda symbol, period_type: _fake_statements("USD"),
    )
    mod.ingest("FAKEUSD.NS")

    session = test_session_factory()
    try:
        stmts = session.query(FinancialStatement).all()
        assert all(s.currency == "USD" for s in stmts)
    finally:
        session.close()


def test_missing_line_item_is_stored_as_null_not_skipped(test_session_factory, monkeypatch):
    """If one line item (e.g. Free Cash Flow) is missing from the source,
    the whole period should still be stored with that field as NULL,
    rather than the period being dropped entirely."""
    info, hist = _fake_stock_response()
    monkeypatch.setattr(stock_mod, "_fetch_from_yfinance", lambda symbol: (info, hist))
    stock_mod.ingest("FAKEPARTIAL.NS")

    def statements_missing_fcf(symbol, period_type):
        income, balance, cashflow, currency = _fake_statements("INR")
        cashflow = cashflow.drop(index="Free Cash Flow")  # simulate missing data
        return income, balance, cashflow, currency

    monkeypatch.setattr(mod, "_fetch_statements", statements_missing_fcf)
    mod.ingest("FAKEPARTIAL.NS")

    session = test_session_factory()
    try:
        stmts = session.query(FinancialStatement).all()
        assert len(stmts) > 0
        assert all(s.free_cash_flow is None for s in stmts)
        assert all(s.revenue is not None for s in stmts), \
            "other fields should still populate even when one is missing"
    finally:
        session.close()
