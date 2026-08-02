"""
The orchestrator. Reads watchlist.yaml (stocks) and mf_watchlist.yaml
(mutual funds) and ingests every entry in both. This is the script you'll
eventually put on a daily schedule.

Run locally: python -m src.ingestion.run_ingestion
"""

import yaml
from pathlib import Path

from src.ingestion.ingest_stock_price import ingest as ingest_stock
from src.ingestion.ingest_financial_statements import ingest as ingest_financials
from src.ingestion.ingest_mutual_fund import ingest as ingest_mf
from src.utils.rate_limit import polite_delay

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STOCK_WATCHLIST_PATH = PROJECT_ROOT / "watchlist.yaml"
MF_WATCHLIST_PATH = PROJECT_ROOT / "mf_watchlist.yaml"


def load_list(path: Path, key: str) -> list:
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get(key, [])


def run_stocks() -> dict:
    symbols = load_list(STOCK_WATCHLIST_PATH, "stocks")
    print(f"Stock watchlist loaded: {len(symbols)} symbols\n")

    results = {"success": [], "failed": []}
    for symbol in symbols:
        try:
            ingest_stock(symbol)
            polite_delay(1.5)
            ingest_financials(symbol)
            results["success"].append(symbol)
        except Exception as e:
            print(f"FAILED {symbol}: {e}")
            results["failed"].append(symbol)
        polite_delay(1.5)
    return results


def run_mutual_funds() -> dict:
    codes = load_list(MF_WATCHLIST_PATH, "mutual_funds")
    print(f"\nMutual fund watchlist loaded: {len(codes)} scheme codes\n")

    results = {"success": [], "failed": []}
    for code in codes:
        try:
            ingest_mf(code)
            results["success"].append(code)
        except Exception as e:
            print(f"FAILED {code}: {e}")
            results["failed"].append(code)
        polite_delay(1.5)
    return results


def main():
    stock_results = run_stocks()
    mf_results = run_mutual_funds()

    print("\n--- Ingestion run complete ---")
    print(f"Stocks   succeeded: {len(stock_results['success'])} -> {stock_results['success']}")
    print(f"Stocks   failed:    {len(stock_results['failed'])} -> {stock_results['failed']}")
    print(f"MF       succeeded: {len(mf_results['success'])} -> {mf_results['success']}")
    print(f"MF       failed:    {len(mf_results['failed'])} -> {mf_results['failed']}")


if __name__ == "__main__":
    main()