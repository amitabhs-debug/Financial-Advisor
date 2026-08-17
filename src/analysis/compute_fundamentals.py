"""
Orchestrator for the fundamentals engine. Mirrors run_ingestion.py's
shape: loop over securities, isolate per-symbol failures, log outcomes.

Requires ComputedFundamentals to exist in src/models/schema.py first
(see PASTE_INTO_schema.py.txt) and the table to have been created via
Base.metadata.create_all().
"""

import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from src.models.db import SessionLocal
from src.models.schema import Security, Fundamentals, FinancialStatement, ComputedFundamentals

from src.analysis.metrics import (
    compute_margins,
    margin_trend,
    revenue_and_profit_cagr,
    compute_valuation_context,
    compute_dcf,
    derive_growth_rate_from_cagr,
    meets_threshold,
    compute_debt_to_assets,
    normalize_debt_to_equity_from_yfinance,
    classify_trend,
    DEFAULT_ASSUMPTIONS,
    DCFAssumptions,
)
from src.analysis.currency import convert_to_inr, get_usd_inr_rate

# Thresholds from the standard quant health checklist. Generic and not
# sector-adjusted — see BANKS_NOT_MEANINGFUL_FOR_ROCE_EBITDA below for
# where that matters (D/E specifically, not ROE).
ROE_THRESHOLD = 0.15   # ROE >= 15% is considered healthy
DEBT_TO_EQUITY_THRESHOLD = 0.5  # D/E <= 0.5 is considered healthy

# Known-limitation flags, carried over from the README. Extend as you
# find more — this project's track record is that this list grows.
BANKS_NOT_MEANINGFUL_FOR_ROCE_EBITDA = {"HDFCBANK.NS", "ICICIBANK.NS"}
TTM_FALLBACK_SECURITIES = {"INFY.NS"}  # Infosys: USD-tagged yfinance financials


def flag_data_quality(symbol: str) -> list[str]:
    flags = []
    if symbol in BANKS_NOT_MEANINGFUL_FOR_ROCE_EBITDA:
        flags.append("bank_roce_ebitda_not_meaningful")
        # D/E is structurally different for banks — deposits are
        # liabilities by the nature of the business, so a "healthy"
        # D/E for a bank looks nothing like 0.5. ROE remains a
        # meaningful metric for banks, unlike ROCE, so it's NOT flagged
        # here — only D/E is.
        flags.append("debt_to_equity_threshold_not_meaningful_for_banks")
    if symbol in TTM_FALLBACK_SECURITIES:
        flags.append("ttm_fallback_used")
    return flags


def normalize_statement_to_inr(stmt, usd_inr_rate: float) -> dict:
    """
    Converts one FinancialStatement row's monetary fields to INR based on
    its stored currency tag. Returns a plain dict (period_end, revenue,
    net_profit, free_cash_flow, total_assets, total_liabilities, all in
    INR) plus a `currency_flags` list noting what happened, so the caller
    can roll flags up per-security rather than per-statement.
    """
    flags = []
    revenue = convert_to_inr(stmt.revenue, stmt.currency, usd_inr_rate)
    net_profit = convert_to_inr(stmt.net_profit, stmt.currency, usd_inr_rate)
    fcf = convert_to_inr(getattr(stmt, "free_cash_flow", None), stmt.currency, usd_inr_rate)
    total_assets = convert_to_inr(getattr(stmt, "total_assets", None), stmt.currency, usd_inr_rate)
    total_liabilities = convert_to_inr(getattr(stmt, "total_liabilities", None), stmt.currency, usd_inr_rate)

    conversions = [revenue, net_profit, fcf, total_assets, total_liabilities]
    if any(c.was_converted for c in conversions):
        flags.append(f"currency_converted_usd_to_inr_{stmt.period_end}")
    if any(c.unrecognized_currency for c in conversions):
        flags.append(f"unrecognized_currency_{stmt.currency}_{stmt.period_end}")

    return {
        "period_end": str(stmt.period_end),
        "revenue": revenue.value,
        "net_profit": net_profit.value,
        "free_cash_flow": fcf.value,
        "total_assets": total_assets.value,
        "total_liabilities": total_liabilities.value,
        "currency_flags": flags,
    }


def compute_for_security(session: Session, security: Security) -> ComputedFundamentals:
    flags = flag_data_quality(security.symbol)

    annual_stmts = (
        session.query(FinancialStatement)
        .filter_by(security_id=security.id, period_type="annual")
        .order_by(FinancialStatement.period_end.asc())
        .all()
    )

    if not annual_stmts:
        flags.append("no_financial_statements_available")
        return ComputedFundamentals(
            security_id=security.id,
            as_of_date=datetime.now(timezone.utc).date(),
            data_quality_flags=",".join(flags),
        )

    # Normalize every statement to INR before any calculation touches it.
    # This is the fix for the Infosys-style bug: previously revenue/
    # net_profit/free_cash_flow were read raw, silently assuming INR
    # even when currency == "USD".
    usd_inr_rate, rate_is_fallback = get_usd_inr_rate()
    if rate_is_fallback:
        flags.append("fx_rate_fallback_used_live_fetch_failed")

    normalized_stmts = [normalize_statement_to_inr(stmt, usd_inr_rate) for stmt in annual_stmts]
    any_conversion_applied = False
    for ns in normalized_stmts:
        flags.extend(ns["currency_flags"])
        if any("converted" in f for f in ns["currency_flags"]):
            any_conversion_applied = True
    if any_conversion_applied:
        flags.append("fx_rate_is_current_not_historical_approximation")

    latest_normalized = normalized_stmts[-1]

    # --- margins -------------------------------------------------------
    # gross_profit / operating_income don't exist in FinancialStatement —
    # always None here, so gross_margin/operating_margin always come
    # back None too. Flagged rather than silently omitted.
    flags.append("gross_operating_margin_unavailable_no_cogs_data")

    margin_snapshots = [
        compute_margins(
            period_end=ns["period_end"],
            revenue=ns["revenue"],
            net_profit=ns["net_profit"],
        )
        for ns in normalized_stmts
    ]
    latest_margins = margin_snapshots[-1]
    net_margin_trend = margin_trend(margin_snapshots, "net_margin")

    # --- CAGR ------------------------------------------------------------
    stmt_dicts = [
        {"period_end": ns["period_end"], "revenue": ns["revenue"], "net_profit": ns["net_profit"]}
        for ns in normalized_stmts
    ]
    cagr_3y = revenue_and_profit_cagr(stmt_dicts, years=3)
    cagr_5y = revenue_and_profit_cagr(stmt_dicts, years=5)

    if cagr_3y["revenue_cagr"] is None:
        flags.append("insufficient_history_for_3y_cagr")
    if cagr_5y["revenue_cagr"] is None:
        flags.append("insufficient_history_for_5y_cagr")

    # --- valuation context -------------------------------------------------
    fundamentals_history = (
        session.query(Fundamentals)
        .filter_by(security_id=security.id)
        .order_by(Fundamentals.as_of_date.asc())
        .all()
    )
    latest_fund = fundamentals_history[-1] if fundamentals_history else None
    current_pe = latest_fund.pe_ratio if latest_fund else None
    historical_pe_series = [f.pe_ratio for f in fundamentals_history[:-1]]
    valuation = compute_valuation_context(current_pe, historical_pe_series)

    # --- ROE / Debt-to-Equity threshold checks -----------------------------
    # Both already ingested by ingest_stock_price.py (info.get("returnOnEquity"),
    # info.get("debtToEquity")) but never evaluated until now.
    roe = latest_fund.roe if latest_fund else None
    debt_to_equity = normalize_debt_to_equity_from_yfinance(latest_fund.debt_to_equity if latest_fund else None)

    roe_meets_threshold = meets_threshold(roe, ROE_THRESHOLD, "above")
    debt_to_equity_meets_threshold = meets_threshold(debt_to_equity, DEBT_TO_EQUITY_THRESHOLD, "below")

    if roe is None:
        flags.append("roe_data_unavailable")
    elif roe_meets_threshold is False:
        flags.append(f"roe_below_{int(ROE_THRESHOLD * 100)}pct_threshold")

    if debt_to_equity is None:
        flags.append("debt_to_equity_data_unavailable")
    elif debt_to_equity_meets_threshold is False and security.symbol not in BANKS_NOT_MEANINGFUL_FOR_ROCE_EBITDA:
        flags.append(f"debt_to_equity_above_{DEBT_TO_EQUITY_THRESHOLD}x_threshold")

    # --- Balance sheet health (debt-to-assets) ------------------------------
    # total_assets/total_liabilities were already ingested into
    # financial_statements but never used until now.
    debt_to_assets_series = [
        compute_debt_to_assets(ns["total_liabilities"], ns["total_assets"])
        for ns in normalized_stmts
    ]
    latest_debt_to_assets = debt_to_assets_series[-1] if debt_to_assets_series else None
    debt_to_assets_trend = classify_trend(debt_to_assets_series)

    if latest_debt_to_assets is None:
        flags.append("debt_to_assets_data_unavailable")

    # --- DCF -----------------------------------------------------------------
    base_fcf = latest_normalized["free_cash_flow"]

    # Real field now (Fundamentals.shares_outstanding, sourced directly
    # from yfinance's sharesOutstanding) — no more net_profit/eps estimate.
    shares_outstanding = latest_fund.shares_outstanding if latest_fund else None

    if shares_outstanding is None and base_fcf and base_fcf > 0:
        flags.append("shares_outstanding_unavailable_per_share_dcf_skipped")

    # Use the company's own 3yr revenue CAGR as the DCF's growth
    # assumption instead of the same flat 10% for every stock. Falls
    # back to the flat default if 3yr CAGR isn't available; gets clamped
    # (and flagged) if the raw CAGR is an unreasonable outlier.
    growth = derive_growth_rate_from_cagr(cagr_3y["revenue_cagr"])
    dcf_assumptions = DCFAssumptions(
        growth_rate_stage1=growth.rate,
        projection_years=DEFAULT_ASSUMPTIONS.projection_years,
        terminal_growth_rate=DEFAULT_ASSUMPTIONS.terminal_growth_rate,
        discount_rate=DEFAULT_ASSUMPTIONS.discount_rate,
    )
    if growth.was_derived:
        flags.append(f"dcf_growth_rate_derived_from_revenue_cagr_3y_{round(growth.rate * 100, 1)}pct")
        if growth.was_capped:
            flags.append(f"dcf_growth_rate_capped_raw_cagr_was_{round(growth.raw_cagr * 100, 1)}pct")
    else:
        flags.append("dcf_growth_rate_using_flat_default_no_cagr_available")

    dcf_result = compute_dcf(base_fcf, shares_outstanding, False, dcf_assumptions)

    if dcf_result is None and base_fcf is not None:
        flags.append("dcf_skipped_nonpositive_fcf")
    elif base_fcf is None:
        flags.append("dcf_skipped_no_fcf_data")

    return ComputedFundamentals(
        security_id=security.id,
        as_of_date=datetime.now(timezone.utc).date(),
        gross_margin=latest_margins.gross_margin,
        operating_margin=latest_margins.operating_margin,
        net_margin=latest_margins.net_margin,
        margin_trend_net=net_margin_trend,
        revenue_cagr_3y=cagr_3y["revenue_cagr"],
        profit_cagr_3y=cagr_3y["profit_cagr"],
        revenue_cagr_5y=cagr_5y["revenue_cagr"],
        profit_cagr_5y=cagr_5y["profit_cagr"],
        current_pe=valuation.current_pe,
        pe_percentile_rank=valuation.percentile_rank,
        roe=roe,
        roe_meets_threshold=roe_meets_threshold,
        debt_to_equity=debt_to_equity,
        debt_to_equity_meets_threshold=debt_to_equity_meets_threshold,
        debt_to_assets_ratio=latest_debt_to_assets,
        debt_to_assets_trend=debt_to_assets_trend,
        dcf_intrinsic_value=dcf_result.intrinsic_value if dcf_result else None,
        dcf_intrinsic_value_per_share=dcf_result.intrinsic_value_per_share if dcf_result else None,
        dcf_shares_outstanding_estimated=False if dcf_result else None,
        dcf_assumptions_json=json.dumps(dcf_result.assumptions.__dict__) if dcf_result else None,
        data_quality_flags=",".join(flags) if flags else None,
    )


def run_compute_fundamentals(symbols: list[str]) -> dict:
    """
    Batch entry point. Opens its own session (matches check_db.py's
    pattern of SessionLocal() per run), isolates per-symbol failures.
    """
    session = SessionLocal()
    results = {"succeeded": [], "failed": []}

    try:
        for symbol in symbols:
            security = session.query(Security).filter_by(symbol=symbol).first()
            if not security:
                results["failed"].append((symbol, "not found in DB — run ingestion first"))
                continue
            try:
                computed = compute_for_security(session, security)
                session.add(computed)
                session.commit()
                results["succeeded"].append(symbol)
            except Exception as e:
                session.rollback()
                results["failed"].append((symbol, str(e)))
    finally:
        session.close()

    return results