"""
Step 3: fetch one ticker via yfinance and persist it to the database.

This is the template every future ingestion script will follow:
  1. check ingestion_log — skip if fetched recently
  2. fetch from source
  3. upsert into `securities`
  4. bulk upsert into `price_history` / `fundamentals`
  5. log the outcome to `ingestion_log`

Run locally: python -m src.ingestion.ingest_stock_price
"""

import sys
from datetime import date , datetime, timedelta, timezone

import yfinance as yf
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import select

from src.models.db import SessionLocal, init_db
from src.models.schema import Security, PriceHistory, Fundamentals, IngestionLog
from src.utils.rate_limit import retry_with_backoff

TICKER = "RELIANCE.NS"
SKIP_IF_FETCHED_WITHIN_HOURS = 12  # don't re-hit the API if we just fetched


def already_fetched_recently(session, symbol: str) -> bool:
    """Idempotency guard: check ingestion_log before hitting the API again."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SKIP_IF_FETCHED_WITHIN_HOURS)
    stmt = (
        select(IngestionLog)
        .join(Security, IngestionLog.security_id == Security.id)
        .where(
            Security.symbol == symbol,
            IngestionLog.source == "yfinance",
            IngestionLog.status == "success",
            IngestionLog.last_fetched_at > cutoff,
        )
        .order_by(IngestionLog.last_fetched_at.desc())
    )
    return session.execute(stmt).first() is not None


def upsert_security(session, symbol: str, info: dict) -> Security:
    """Insert the security if new, otherwise update its metadata."""
    stmt = (
        sqlite_insert(Security)
        .values(
            symbol=symbol,
            name=info.get("longName", symbol),
            type="stock",
            exchange="NSE" if symbol.endswith(".NS") else "BSE",
            sector=info.get("sector"),
            industry=info.get("industry"),
            isin=info.get("isin"),
        )
        .on_conflict_do_update(
            index_elements=["symbol"],
            set_=dict(
                name=info.get("longName", symbol),
                sector=info.get("sector"),
                industry=info.get("industry"),
            ),
        )
        .returning(Security.id)
    )
    security_id = session.execute(stmt).scalar_one()
    session.commit()
    return session.get(Security, security_id)


def upsert_price_history(session, security_id: int, hist) -> int:
    """Bulk upsert OHLCV rows. Returns number of rows written."""
    rows = []
    for idx, row in hist.iterrows():
        rows.append(dict(
            security_id=security_id,
            date=idx.date() if hasattr(idx, "date") else idx,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            adjusted_close=float(row.get("Close", row["Close"])),
            volume=int(row["Volume"]) if row["Volume"] == row["Volume"] else None,
        ))

    if not rows:
        return 0

    stmt = sqlite_insert(PriceHistory).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["security_id", "date"],
        set_=dict(
            open=stmt.excluded.open,
            high=stmt.excluded.high,
            low=stmt.excluded.low,
            close=stmt.excluded.close,
            adjusted_close=stmt.excluded.adjusted_close,
            volume=stmt.excluded.volume,
        ),
    )
    session.execute(stmt)
    session.commit()
    return len(rows)


def upsert_fundamentals(session, security_id: int, info: dict) -> None:
    """One fundamentals snapshot per day, upserted."""
    stmt = (
        sqlite_insert(Fundamentals)
        .values(
            security_id=security_id,
            as_of_date=date.today(),
            pe_ratio=info.get("trailingPE"),
            pb_ratio=info.get("priceToBook"),
            debt_to_equity=info.get("debtToEquity"),
            roe=info.get("returnOnEquity"),
            roce=None,  # yfinance doesn't provide ROCE directly — Screener.in later
            market_cap=info.get("marketCap"),
            book_value=info.get("bookValue"),
            eps=info.get("trailingEps"),
            dividend_yield=info.get("dividendYield"),
        )
        .on_conflict_do_update(
            index_elements=["security_id", "as_of_date"],
            set_=dict(
                pe_ratio=info.get("trailingPE"),
                pb_ratio=info.get("priceToBook"),
                debt_to_equity=info.get("debtToEquity"),
                roe=info.get("returnOnEquity"),
                market_cap=info.get("marketCap"),
                book_value=info.get("bookValue"),
                eps=info.get("trailingEps"),
                dividend_yield=info.get("dividendYield"),
            ),
        )
    )
    session.execute(stmt)
    session.commit()


def log_ingestion(session, security_id: int | None, status: str, error: str | None = None):
    session.add(IngestionLog(
        source="yfinance",
        security_id=security_id,
        last_fetched_at=datetime.now(timezone.utc),
        status=status,
        error_message=error,
    ))
    session.commit()


@retry_with_backoff(max_attempts=3, base_delay=2.0)
def _fetch_from_yfinance(symbol: str):
    """Isolated so retry/backoff only wraps the actual network call, not DB writes."""
    ticker = yf.Ticker(symbol)
    info = ticker.info
    hist = ticker.history(period="1y")
    if hist.empty:
        raise ValueError(f"No price history returned for {symbol}")
    return info, hist


def ingest(symbol: str = TICKER):
    init_db()
    session = SessionLocal()

    try:
        if already_fetched_recently(session, symbol):
            print(f"{symbol}: fetched within the last {SKIP_IF_FETCHED_WITHIN_HOURS}h, skipping.")
            return

        print(f"Fetching {symbol} from yfinance...")
        info, hist = _fetch_from_yfinance(symbol)

        security = upsert_security(session, symbol, info)
        rows_written = upsert_price_history(session, security.id, hist)
        upsert_fundamentals(session, security.id, info)
        log_ingestion(session, security.id, "success")

        print(f"Done. {symbol}: {rows_written} price rows upserted, fundamentals updated.")

    except Exception as e:
        print(f"FAILED for {symbol}: {e}", file=sys.stderr)
        log_ingestion(session, None, "failed", str(e))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else TICKER
    ingest(symbol)