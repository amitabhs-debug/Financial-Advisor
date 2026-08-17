"""
CLI entry point for the fundamentals engine.
Run locally: python -m src.analysis.run_fundamentals

Confirmed against your actual watchlist.yaml: {"stocks": [...]} at the
project root, same structure and path pattern as run_ingestion.py's
load_list().
"""

import yaml
from pathlib import Path
from src.analysis.compute_fundamentals import run_compute_fundamentals

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STOCK_WATCHLIST_PATH = PROJECT_ROOT / "watchlist.yaml"


def load_watchlist() -> list[str]:
    with open(STOCK_WATCHLIST_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("stocks", [])


def main():
    symbols = load_watchlist()
    print(f"Computing fundamentals for {len(symbols)} securities...")

    results = run_compute_fundamentals(symbols)

    print(f"\nSucceeded: {len(results['succeeded'])}")
    for s in results["succeeded"]:
        print(f"  OK  {s}")

    if results["failed"]:
        print(f"\nFailed: {len(results['failed'])}")
        for symbol, error in results["failed"]:
            print(f"  FAIL  {symbol}: {error}")


if __name__ == "__main__":
    main()