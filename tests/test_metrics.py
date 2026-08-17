"""
Tests for src/analysis/metrics.py — pure functions, no DB required.
Run: pytest tests/test_metrics.py -v
"""

import pytest
from src.analysis.metrics import (
    compute_margins,
    margin_trend,
    compute_cagr,
    revenue_and_profit_cagr,
    compute_valuation_context,
    compute_dcf,
    DCFAssumptions,
    MarginSnapshot,
    meets_threshold,
    compute_debt_to_assets,
    normalize_debt_to_equity_from_yfinance,
    classify_trend,
)


# --- margins ----------------------------------------------------------------

def test_compute_margins_normal_case():
    m = compute_margins("2024-03-31", revenue=1000, net_profit=150, operating_income=200, gross_profit=400)
    assert m.net_margin == 0.15
    assert m.operating_margin == 0.2
    assert m.gross_margin == 0.4


def test_compute_margins_missing_fields_returns_none_not_error():
    m = compute_margins("2024-03-31", revenue=1000, net_profit=150)
    assert m.net_margin == 0.15
    assert m.operating_margin is None
    assert m.gross_margin is None


def test_compute_margins_zero_revenue_does_not_raise():
    m = compute_margins("2024-03-31", revenue=0, net_profit=150)
    assert m.net_margin is None


def test_margin_trend_improving():
    snapshots = [
        MarginSnapshot("2022", None, None, 0.05),
        MarginSnapshot("2023", None, None, 0.08),
        MarginSnapshot("2024", None, None, 0.12),
    ]
    assert margin_trend(snapshots, "net_margin") == "improving"


def test_margin_trend_flat_within_noise_threshold():
    snapshots = [
        MarginSnapshot("2022", None, None, 0.10),
        MarginSnapshot("2024", None, None, 0.102),
    ]
    assert margin_trend(snapshots, "net_margin") == "flat"


def test_margin_trend_insufficient_data_returns_none():
    assert margin_trend([MarginSnapshot("2024", None, None, 0.10)], "net_margin") is None


# --- CAGR ---------------------------------------------------------------

def test_cagr_normal_case():
    assert compute_cagr(100, 200, 3) == pytest.approx(0.2599, abs=0.001)


def test_cagr_negative_start_value_returns_none():
    assert compute_cagr(-10, 5, 3) is None


def test_cagr_zero_start_value_returns_none():
    assert compute_cagr(0, 100, 3) is None


def test_cagr_missing_values_returns_none():
    assert compute_cagr(None, 100, 3) is None
    assert compute_cagr(100, None, 3) is None


def test_revenue_and_profit_cagr_insufficient_periods_returns_none():
    stmts = [
        {"period_end": "2022", "revenue": 100, "net_profit": 10},
        {"period_end": "2023", "revenue": 110, "net_profit": 12},
    ]
    result = revenue_and_profit_cagr(stmts, years=5)
    assert result["revenue_cagr"] is None
    assert result["years_used"] is None


def test_revenue_and_profit_cagr_exact_window():
    stmts = [
        {"period_end": str(2020 + i), "revenue": 100 * (1.1 ** i), "net_profit": 10 * (1.1 ** i)}
        for i in range(4)
    ]
    result = revenue_and_profit_cagr(stmts, years=3)
    assert result["revenue_cagr"] == pytest.approx(0.10, abs=0.001)
    assert result["years_used"] == 3


# --- valuation context ---------------------------------------------------

def test_valuation_context_normal_case():
    ctx = compute_valuation_context(current_pe=25, historical_pe_series=[10, 15, 20, 25, 30, 35])
    assert ctx.percentile_rank is not None
    assert ctx.historical_pe_min == 10
    assert ctx.historical_pe_max == 35


def test_valuation_context_no_history_returns_none_fields():
    ctx = compute_valuation_context(current_pe=25, historical_pe_series=[])
    assert ctx.percentile_rank is None
    assert "Insufficient" in ctx.note


def test_valuation_context_filters_invalid_pe_values():
    ctx = compute_valuation_context(current_pe=25, historical_pe_series=[10, -5, 0, 20, 30])
    assert ctx.historical_pe_min == 10


# --- DCF -------------------------------------------------------------------

def test_dcf_normal_case_produces_positive_value():
    result = compute_dcf(base_fcf=1000, shares_outstanding=100)
    assert result.intrinsic_value > 0
    assert result.intrinsic_value_per_share == pytest.approx(result.intrinsic_value / 100, abs=0.01)
    assert len(result.projected_fcfs) == 5


def test_dcf_negative_base_fcf_returns_none():
    assert compute_dcf(base_fcf=-500) is None


def test_dcf_zero_base_fcf_returns_none():
    assert compute_dcf(base_fcf=0) is None


def test_dcf_missing_base_fcf_returns_none():
    assert compute_dcf(base_fcf=None) is None


def test_dcf_invalid_assumptions_discount_below_terminal_growth_raises():
    bad = DCFAssumptions(growth_rate_stage1=0.10, projection_years=5, terminal_growth_rate=0.15, discount_rate=0.10)
    with pytest.raises(ValueError):
        compute_dcf(base_fcf=1000, assumptions=bad)


def test_dcf_without_shares_outstanding_still_returns_total_value():
    result = compute_dcf(base_fcf=1000, shares_outstanding=None)
    assert result.intrinsic_value > 0
    assert result.intrinsic_value_per_share is None


def test_dcf_estimated_shares_flag_carries_into_warning():
    result = compute_dcf(base_fcf=1000, shares_outstanding=100, shares_outstanding_is_estimated=True)
    assert result.shares_outstanding_is_estimated is True
    assert "ESTIMATED" in result.warning


# --- CAGR-derived DCF growth rate ---------------------------------------

def test_derive_growth_rate_normal_case_uses_cagr_directly():
    from src.analysis.metrics import derive_growth_rate_from_cagr
    result = derive_growth_rate_from_cagr(0.15)
    assert result.rate == 0.15
    assert result.was_derived is True
    assert result.was_capped is False


def test_derive_growth_rate_caps_unrealistic_high_cagr():
    from src.analysis.metrics import derive_growth_rate_from_cagr
    result = derive_growth_rate_from_cagr(1.50)  # 150% CAGR — a real but misleading spike
    assert result.rate == 0.30  # clamped to cap
    assert result.was_capped is True
    assert result.raw_cagr == 1.50


def test_derive_growth_rate_floors_unrealistic_negative_cagr():
    from src.analysis.metrics import derive_growth_rate_from_cagr
    result = derive_growth_rate_from_cagr(-0.50)
    assert result.rate == -0.10  # clamped to floor
    assert result.was_capped is True


def test_derive_growth_rate_falls_back_to_default_when_no_cagr():
    from src.analysis.metrics import derive_growth_rate_from_cagr, DEFAULT_ASSUMPTIONS
    result = derive_growth_rate_from_cagr(None)
    assert result.rate == DEFAULT_ASSUMPTIONS.growth_rate_stage1
    assert result.was_derived is False
    assert result.was_capped is False


# --- meets_threshold, compute_debt_to_assets, classify_trend -------------

def test_meets_threshold_above_direction_pass():
    assert meets_threshold(0.20, 0.15, "above") is True


def test_meets_threshold_above_direction_fail():
    assert meets_threshold(0.10, 0.15, "above") is False


def test_meets_threshold_below_direction_pass():
    assert meets_threshold(0.3, 0.5, "below") is True


def test_meets_threshold_below_direction_fail():
    assert meets_threshold(0.8, 0.5, "below") is False


def test_meets_threshold_missing_value_returns_none_not_false():
    """
    A missing metric is a data gap, not a failed check — conflating the
    two would silently misrepresent "we don't know" as "this failed."
    """
    assert meets_threshold(None, 0.15, "above") is None


def test_meets_threshold_invalid_direction_raises():
    with pytest.raises(ValueError):
        meets_threshold(0.5, 0.5, "sideways")


def test_compute_debt_to_assets_normal_case():
    assert compute_debt_to_assets(400, 1000) == 0.4


def test_compute_debt_to_assets_missing_values_return_none():
    assert compute_debt_to_assets(None, 1000) is None
    assert compute_debt_to_assets(400, None) is None


def test_compute_debt_to_assets_zero_assets_returns_none():
    assert compute_debt_to_assets(400, 0) is None


def test_classify_trend_increasing():
    assert classify_trend([0.3, 0.5, 0.7]) == "increasing"


def test_classify_trend_decreasing():
    assert classify_trend([0.7, 0.5, 0.3]) == "decreasing"


def test_classify_trend_flat_within_noise():
    assert classify_trend([0.500, 0.502]) == "flat"


def test_classify_trend_insufficient_data_returns_none():
    assert classify_trend([0.5]) is None
    assert classify_trend([]) is None


def test_margin_trend_still_works_after_refactor_to_use_classify_trend():
    """
    Regression guard: margin_trend was refactored to delegate to the new
    generic classify_trend() and map increasing/decreasing to
    improving/declining. Confirms the public behavior is unchanged.
    """
    snapshots = [
        MarginSnapshot("2022", None, None, 0.20),
        MarginSnapshot("2024", None, None, 0.10),
    ]
    assert margin_trend(snapshots, "net_margin") == "declining"


# --- normalize_debt_to_equity_from_yfinance -------------------------------

def test_normalize_debt_to_equity_divides_by_100():
    """
    Regression guard for a real bug: yfinance's debtToEquity comes back
    percentage-scaled (e.g. 36.65 meaning 36.65%), unlike roe/roce which
    are plain decimal fractions. Left uncorrected, a real D/E of 0.37
    reads as 37 and fails every sane threshold check.
    """
    assert normalize_debt_to_equity_from_yfinance(36.65) == 0.3665
    assert normalize_debt_to_equity_from_yfinance(10.21) == 0.1021


def test_normalize_debt_to_equity_none_returns_none():
    assert normalize_debt_to_equity_from_yfinance(None) is None


def test_normalize_debt_to_equity_then_threshold_check_passes_for_low_debt_company():
    """
    End-to-end regression: TCS's raw yfinance D/E of 10.21 should
    normalize to 0.1021 and PASS the 0.5 threshold — reflecting reality
    (TCS is a famously low-debt company), not the pre-fix FAIL.
    """
    normalized = normalize_debt_to_equity_from_yfinance(10.21)
    assert meets_threshold(normalized, 0.5, "below") is True