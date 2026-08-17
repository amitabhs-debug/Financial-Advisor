"""
Core database schema for the stock/mutual fund analysis tool.

Design notes:
- `securities` is the master table; everything else references it by FK.
- Composite unique constraints prevent duplicate rows on repeated ingestion runs
  (idempotency), so re-running a script twice never corrupts data.
- SQLite now, but this is written against the SQLAlchemy ORM so switching to
  Postgres later is a one-line change to the connection string.
"""

from datetime import datetime, date, timezone
from sqlalchemy import (
    String, Integer, Float, Date, DateTime, ForeignKey, UniqueConstraint, Index, Boolean, Text
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

   

class Base(DeclarativeBase):
    pass


class Security(Base):
    """Master record for any stock or mutual fund we track."""
    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # e.g. "RELIANCE.NS"
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(16))  # "stock" | "mutual_fund"
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True)  # NSE / BSE
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    price_history: Mapped[list["PriceHistory"]] = relationship(back_populates="security")
    fundamentals: Mapped[list["Fundamentals"]] = relationship(back_populates="security")


class PriceHistory(Base):
    """Daily OHLCV data. One row per security per trading day."""
    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint("security_id", "date", name="uq_price_security_date"),
        Index("ix_price_security_date", "security_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adjusted_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)

    security: Mapped["Security"] = relationship(back_populates="price_history")


class Fundamentals(Base):
    """Point-in-time snapshot of key ratios. Updated weekly/monthly, not daily."""
    __tablename__ = "fundamentals"
    __table_args__ = (
        UniqueConstraint("security_id", "as_of_date", name="uq_fund_security_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    as_of_date: Mapped[date] = mapped_column(Date)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)
    roce: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares_outstanding = mapped_column(Float, nullable=True)
    book_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    ebitda: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoter_holding_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pledged_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)

    security: Mapped["Security"] = relationship(back_populates="fundamentals")


class FinancialStatement(Base):
    """Raw quarterly/annual statement line items behind the ratios above."""
    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("security_id", "period_end", "period_type",
                          name="uq_stmt_security_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(16))  # "quarterly" | "annual"
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)  # "INR" | "USD" etc.
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_assets: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_liabilities: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    free_cash_flow: Mapped[float | None] = mapped_column(Float, nullable=True)


class ComputedFundamentals(Base):
    """
    Phase 2: one row per (security, as_of_date) snapshot of everything
    the fundamentals engine computed — margins, CAGR, valuation context,
    ROE/D/E threshold checks, balance sheet health, DCF. Kept append-only
    (not upserted in place) so you can see how computed metrics evolved
    as new financial statements landed, the same way price_history is a
    time series rather than a single latest-value row.
    """
    __tablename__ = "computed_fundamentals"

    id = mapped_column(Integer, primary_key=True)
    security_id = mapped_column(Integer, ForeignKey("securities.id"), nullable=False, index=True)
    as_of_date = mapped_column(Date, nullable=False, index=True)

    # Margins (most recent annual period). gross_margin/operating_margin
    # will be NULL for every row until FinancialStatement gains COGS /
    # operating_income fields — this schema doesn't have them yet.
    gross_margin = mapped_column(Float, nullable=True)
    operating_margin = mapped_column(Float, nullable=True)
    net_margin = mapped_column(Float, nullable=True)
    margin_trend_net = mapped_column(String, nullable=True)  # "improving" / "declining" / "flat" / None

    # CAGR
    revenue_cagr_3y = mapped_column(Float, nullable=True)
    profit_cagr_3y = mapped_column(Float, nullable=True)
    revenue_cagr_5y = mapped_column(Float, nullable=True)
    profit_cagr_5y = mapped_column(Float, nullable=True)

    # Valuation context (vs. own history only — no sector/peer data)
    current_pe = mapped_column(Float, nullable=True)
    pe_percentile_rank = mapped_column(Float, nullable=True)  # 0-100 within own history

    # ROE / Debt-to-Equity (already ingested by ingest_stock_price.py,
    # newly evaluated against thresholds here: ROE >= 15%, D/E <= 0.5)
    roe = mapped_column(Float, nullable=True)
    roe_meets_threshold = mapped_column(Boolean, nullable=True)
    debt_to_equity = mapped_column(Float, nullable=True)
    debt_to_equity_meets_threshold = mapped_column(Boolean, nullable=True)  # meaningless for banks, see flags

    # Balance sheet health (total_assets/total_liabilities already
    # ingested into financial_statements, newly used here)
    debt_to_assets_ratio = mapped_column(Float, nullable=True)
    debt_to_assets_trend = mapped_column(String, nullable=True)  # "increasing" / "decreasing" / "flat" / None

    # DCF
    dcf_intrinsic_value = mapped_column(Float, nullable=True)
    dcf_intrinsic_value_per_share = mapped_column(Float, nullable=True)
    dcf_shares_outstanding_estimated = mapped_column(Boolean, nullable=True)  # True if derived from net_profit/eps
    dcf_assumptions_json = mapped_column(Text, nullable=True)  # DCFAssumptions used, for auditability

    # Data quality — populated by compute_fundamentals.py, not metrics.py.
    # Comma-separated flags, e.g. "bank_roce_ebitda_not_meaningful,gross_operating_margin_unavailable_no_cogs_data"
    data_quality_flags = mapped_column(Text, nullable=True)

    computed_at = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    security = relationship("Security", backref="computed_fundamentals")

    def __repr__(self):
        return f"<ComputedFundamentals security_id={self.security_id} as_of={self.as_of_date}>"


class MutualFundNav(Base):
    """Daily NAV for mutual funds, sourced from AMFI."""
    __tablename__ = "mutual_fund_nav"
    __table_args__ = (
        UniqueConstraint("security_id", "date", name="uq_nav_security_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    date: Mapped[date] = mapped_column(Date)
    nav: Mapped[float] = mapped_column(Float)


class IngestionLog(Base):
    """Tracks every ingestion attempt so we don't re-fetch unnecessarily
    and can debug failures without re-running everything."""
    __tablename__ = "ingestion_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # "yfinance" | "amfi" | "screener" | "bhavcopy"
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id"), nullable=True)
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(16))  # "success" | "failed"
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)