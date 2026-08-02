"""
Tests for run_ingestion.py's failure isolation: one bad symbol in the
watchlist must not prevent the remaining symbols from being ingested.
"""

import src.ingestion.run_ingestion as orch


def test_one_bad_symbol_does_not_stop_the_rest(monkeypatch):
    processed = []

    def fake_ingest_stock(symbol):
        if symbol == "BAD.NS":
            raise ValueError("simulated failure for BAD.NS")
        processed.append(symbol)

    def fake_ingest_financials(symbol):
        pass  # no-op; not under test here

    def fake_load_list(path, key):
        return ["GOOD1.NS", "BAD.NS", "GOOD2.NS"]

    monkeypatch.setattr(orch, "ingest_stock", fake_ingest_stock)
    monkeypatch.setattr(orch, "ingest_financials", fake_ingest_financials)
    monkeypatch.setattr(orch, "load_list", fake_load_list)
    monkeypatch.setattr(orch, "polite_delay", lambda seconds: None)  # skip real sleeps

    results = orch.run_stocks()

    assert processed == ["GOOD1.NS", "GOOD2.NS"], \
        "both good symbols should have been processed despite the failure"
    assert results["success"] == ["GOOD1.NS", "GOOD2.NS"]
    assert results["failed"] == ["BAD.NS"]


def test_mutual_fund_loop_also_isolates_failures(monkeypatch):
    processed = []

    def fake_ingest_mf(code):
        if code == "999999":
            raise ValueError("simulated failure for scheme 999999")
        processed.append(code)

    def fake_load_list(path, key):
        return ["111111", "999999", "222222"]

    monkeypatch.setattr(orch, "ingest_mf", fake_ingest_mf)
    monkeypatch.setattr(orch, "load_list", fake_load_list)
    monkeypatch.setattr(orch, "polite_delay", lambda seconds: None)

    results = orch.run_mutual_funds()

    assert processed == ["111111", "222222"]
    assert results["success"] == ["111111", "222222"]
    assert results["failed"] == ["999999"]
