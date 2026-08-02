"""
Step 0: validate the data source works before building anything on top of it.
Run this locally: python -m src.ingestion.validate_yfinance
"""

import yfinance as yf

TICKER = "RELIANCE.NS"

def main():
    stock = yf.Ticker(TICKER)
    hist = stock.history(period="1mo")

    if hist.empty:
        print(f"No data returned for {TICKER}. Check ticker symbol or network.")
        return

    print(f"Last 30 days of prices for {TICKER}:\n")
    print(hist[["Open", "High", "Low", "Close", "Volume"]].tail(30))

    info = stock.info
    print("\nA few fundamentals fields (sanity check they exist):")
    for key in ["trailingPE", "priceToBook", "returnOnEquity", "marketCap"]:
        print(f"  {key}: {info.get(key)}")

if __name__ == "__main__":
    main()
