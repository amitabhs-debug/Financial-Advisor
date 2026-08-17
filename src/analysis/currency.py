"""
Currency normalization for the fundamentals engine.

ingest_financial_statements.py already tags every row with its
financialCurrency (mostly INR, occasionally USD for Infosys — yfinance
sometimes reports ADR-listed companies' statements in USD). Until now,
nothing downstream actually looked at that tag — compute_fundamentals.py
read revenue/net_profit/free_cash_flow as raw numbers and implicitly
assumed INR. That's the root cause of the ~83x-too-small Infosys DCF
per-share figure.

This module is split into two pieces on purpose:
  - convert_to_inr(): pure function, no I/O, fully unit-testable
  - get_usd_inr_rate(): the one network call, isolated so a fetch
    failure can't silently corrupt every calculation — it falls back
    to a hardcoded approximate rate and flags that fallback explicitly,
    the same way DCF assumptions are always surfaced rather than hidden.
"""

from dataclasses import dataclass
from typing import Optional
import yfinance as yf

# Fallback only used if the live USD/INR fetch fails. Rough, deliberately
# approximate — anything computed using this fallback gets flagged so you
# know to treat it with extra caution, not trust it at face value.
FALLBACK_USD_INR_RATE = 94.0


@dataclass
class ConversionResult:
    value: Optional[float]
    was_converted: bool          # True if a USD->INR conversion was applied
    unrecognized_currency: bool  # True if currency wasn't None/INR/USD


def convert_to_inr(value: Optional[float], currency: Optional[str], usd_inr_rate: float) -> ConversionResult:
    """
    Pure conversion — no network calls, no DB. currency is whatever
    FinancialStatement.currency holds for that row (from yfinance's
    financialCurrency, defaults to "INR" per ingest_financial_statements.py).
    """
    if value is None:
        return ConversionResult(value=None, was_converted=False, unrecognized_currency=False)

    normalized_currency = (currency or "INR").upper()

    if normalized_currency == "INR":
        return ConversionResult(value=value, was_converted=False, unrecognized_currency=False)

    if normalized_currency == "USD":
        return ConversionResult(value=value * usd_inr_rate, was_converted=True, unrecognized_currency=False)

    # Some other currency (rare, but yfinance data is inconsistent enough
    # that this shouldn't crash). Don't guess a conversion — surface it
    # as unrecognized so the caller can flag and exclude rather than
    # silently using a wrong number.
    return ConversionResult(value=value, was_converted=False, unrecognized_currency=True)


def get_usd_inr_rate() -> tuple[float, bool]:
    """
    Live USD/INR rate via yfinance. Returns (rate, is_fallback).

    NOTE: this is the CURRENT exchange rate, applied to figures that may
    be from a past fiscal year — a real limitation, not fully solved
    here. A FY2023 USD figure converted at today's rate will be off by
    whatever the rupee has moved since. Good enough to fix the
    "off-by-83x, showing the wrong country's currency" class of error;
    not good enough for precise historical accuracy. Worth documenting
    in the README the same way TTM fallback and standalone-vs-consolidated
    are documented as known-imprecise-but-tracked.
    """
    try:
        rate = yf.Ticker("INR=X").fast_info["last_price"]
        if rate and rate > 0:
            return float(rate), False
    except Exception:
        pass
    return FALLBACK_USD_INR_RATE, True