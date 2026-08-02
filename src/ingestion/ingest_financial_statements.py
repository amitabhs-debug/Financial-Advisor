"""
Phase 1a: fetch raw financial statement line items — revenue, net profit,
total assets/liabilities, operating & free cash flow — and persist them into
`financial_statements`. This is the raw data behind the ratios already sitting
in `fundamentals`; having both lets us recompute margins/growth ourselves and
feed a DCF later, instead of trusting a single vendor's precomputed ratio.

yfinance's row labels have changed across versions, so `_get_row` tries a
list of known aliases for each line item rather than assuming one exact name.

Run locally: python -m src.ingestion.ingest_financial_statements [SYMBOL]
"""

import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.models.db import SessionLocal, init_db
from src.models.schema import Security, FinancialStatement, IngestionLog
from src.utils.rate_limit import retry_with_backoff

TICKER = "RELIANCE.NS"
SKIP_IF_FETCHED_WITHIN_HOURS = 24 * 7  # annual/quarterly data changes rarely — check weekly

# Each line item may appear under different labels depending on yfinance version.
ROW_ALIASES = {
    "revenue": ["Total Revenue", "TotalRevenue"],
    "net_profit": ["Net Income", "Net Income Common Stockholders", "NetIncome"],
    "total_assets": ["Total Assets", "TotalAssets"],
    "total_liabilities": [
        "Total Liabilities Net Minority Interest",
        "Total Liab",
        "TotalLiabilitiesNetMinorityInterest",
    ],
    "operating_cash_flow": [
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
        "CashFlowFromContinuingOperatingActivities",
    ],
    "free_cash_flow": ["Free Cash Flow", "FreeCashFlow"],
}


def _get_row(df: pd.DataFrame, field: str) -> pd.Series | None:
    """Try each known alias for a line item; return the first match found."""
    if df is None or df.empty:
        return None
    for alias in ROW_ALIASES[field]:
        if alias in df.index:
            return df.loc[alias]
    return None


def already_fetched_recently(session, symbol: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SKIP_IF_FETCHED_WITHIN_HOURS)
    stmt = (
        select(IngestionLog)
        .join(Security, IngestionLog.security_id == Security.id)
        .where(
            Security.symbol == symbol,
            IngestionLog.source == "yfinance_financials",
            IngestionLog.status == "success",
            IngestionLog.last_fetched_at > cutoff,
        )
        .order_by(IngestionLog.last_fetched_at.desc())
    )
    return session.execute(stmt).first() is not None


def get_security(session, symbol: str) -> Security | None:
    """Financial statements depend on the security already existing —
    run ingest_stock_price first so `securities` has this symbol."""
    return session.execute(
        select(Security).where(Security.symbol == symbol)
    ).scalar_one_or_none()


@retry_with_backoff(max_attempts=3, base_delay=2.0)
def _fetch_statements(symbol: str, period_type: str):
    """period_type: 'annual' or 'quarterly'."""
    ticker = yf.Ticker(symbol)
    currency = ticker.info.get("financialCurrency", "INR")  # yfinance sometimes reports
                                                             # ADR-listed companies' statements
                                                             # in USD instead of INR — track it
                                                             # explicitly rather than assume.
    if period_type == "annual":
        income = ticker.income_stmt
        balance = ticker.balance_sheet
        cashflow = ticker.cashflow
    else:
        income = ticker.quarterly_income_stmt
        balance = ticker.quarterly_balance_sheet
        cashflow = ticker.quarterly_cashflow

    if income is None or income.empty:
        raise ValueError(f"No {period_type} income statement returned for {symbol}")

    return income, balance, cashflow, currency


def upsert_statements(session, security_id: int, period_type: str,
                       income, balance, cashflow, currency: str) -> int:
    """One row per period_end. Missing line items are stored as NULL rather
    than skipping the whole period — partial data is still useful."""
    revenue_row = _get_row(income, "revenue")
    net_profit_row = _get_row(income, "net_profit")
    assets_row = _get_row(balance, "total_assets")
    liabilities_row = _get_row(balance, "total_liabilities")
    op_cf_row = _get_row(cashflow, "operating_cash_flow")
    fcf_row = _get_row(cashflow, "free_cash_flow")

    periods = income.columns  # Timestamps, one per fiscal period
    rows = []
    for period in periods:
        period_end = period.date() if hasattr(period, "date") else period

        def val(row):
            if row is None or period not in row.index:
                return None
            v = row[period]
            return float(v) if pd.notna(v) else None

        rows.append(dict(
            security_id=security_id,
            period_end=period_end,
            period_type=period_type,
            currency=currency,
            revenue=val(revenue_row),
            net_profit=val(net_profit_row),
            total_assets=val(assets_row),
            total_liabilities=val(liabilities_row),
            operating_cash_flow=val(op_cf_row),
            free_cash_flow=val(fcf_row),
        ))

    if not rows:
        return 0

    stmt = sqlite_insert(FinancialStatement).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["security_id", "period_end", "period_type"],
        set_=dict(
            currency=stmt.excluded.currency,
            revenue=stmt.excluded.revenue,
            net_profit=stmt.excluded.net_profit,
            total_assets=stmt.excluded.total_assets,
            total_liabilities=stmt.excluded.total_liabilities,
            operating_cash_flow=stmt.excluded.operating_cash_flow,
            free_cash_flow=stmt.excluded.free_cash_flow,
        ),
    )
    session.execute(stmt)
    session.commit()
    return len(rows)


def log_ingestion(session, security_id: int | None, status: str, error: str | None = None):
    session.add(IngestionLog(
        source="yfinance_financials",
        security_id=security_id,
        last_fetched_at=datetime.now(timezone.utc),
        status=status,
        error_message=error,
    ))
    session.commit()


def ingest(symbol: str = TICKER):
    init_db()
    session = SessionLocal()

    try:
        if already_fetched_recently(session, symbol):
            print(f"{symbol}: financials fetched within the last "
                  f"{SKIP_IF_FETCHED_WITHIN_HOURS}h, skipping.")
            return

        security = get_security(session, symbol)
        if security is None:
            raise ValueError(
                f"{symbol} not found in securities table — "
                f"run ingest_stock_price for it first."
            )

        total_rows = 0
        for period_type in ("annual", "quarterly"):
            print(f"Fetching {period_type} financial statements for {symbol}...")
            income, balance, cashflow, currency = _fetch_statements(symbol, period_type)
            rows_written = upsert_statements(
                session, security.id, period_type, income, balance, cashflow, currency
            )
            total_rows += rows_written
            print(f"  {period_type}: {rows_written} periods upserted")

        log_ingestion(session, security.id, "success")
        print(f"Done. {symbol}: {total_rows} total statement-periods upserted.")

    except Exception as e:
        print(f"FAILED for {symbol}: {e}", file=sys.stderr)
        log_ingestion(session, None, "failed", str(e))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else TICKER
    ingest(symbol)