"""
Tests for ingest_shareholding.py's validation logic — the part that catches
obvious typos (e.g. entering 5000 instead of 50.0) before they land in the
database. Does not test the DB-writing path itself since that's a thin,
low-risk wrapper; the validation logic is where a real mistake would
otherwise silently corrupt data.
"""

from datetime import date

from src.ingestion.ingest_shareholding import _parse_pct, _parse_date


def test_valid_percentage_parses_correctly():
    assert _parse_pct("50.5", "promoter_holding_pct", "TEST.NS") == 50.5


def test_blank_value_returns_none():
    assert _parse_pct("", "promoter_holding_pct", "TEST.NS") is None
    assert _parse_pct("   ", "promoter_holding_pct", "TEST.NS") is None


def test_non_numeric_value_returns_none_not_crash():
    assert _parse_pct("fifty", "promoter_holding_pct", "TEST.NS") is None


def test_boundary_values_are_accepted():
    assert _parse_pct("0", "promoter_holding_pct", "TEST.NS") == 0.0
    assert _parse_pct("100", "promoter_holding_pct", "TEST.NS") == 100.0


def test_out_of_range_typo_is_rejected():
    """The realistic typo this guards against: entering 5000 instead of
    50.00, or 100.5 instead of 10.05."""
    assert _parse_pct("5000", "promoter_holding_pct", "TEST.NS") is None
    assert _parse_pct("100.5", "promoter_holding_pct", "TEST.NS") is None
    assert _parse_pct("-10", "promoter_holding_pct", "TEST.NS") is None


def test_date_defaults_to_today_when_blank():
    assert _parse_date("") == date.today()
    assert _parse_date("   ") == date.today()


def test_date_parses_iso_format():
    assert _parse_date("2026-08-02") == date(2026, 8, 2)