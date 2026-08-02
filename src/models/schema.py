"""
Core database schema for the stock/mutual fund analysis tool.

Design notes:
- `securities` is the master table; everything else references it by FK.
- Composite unique constraints prevent duplicate rows on repeated ingestion runs
  (idempotency), so re-running a script twice never corrupts data.
- SQLite now, but this is written against the SQLAlchemy ORM so switching to
  Postgres later is a one-line change to the connection string.
"""

from datetime import datetime, date
from sqlalchemy import (
    String, Integer, Float, Date, DateTime, ForeignKey, UniqueConstraint, Index
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