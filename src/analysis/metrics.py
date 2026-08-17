"""
Pure calculation functions for the fundamentals engine — margins, CAGR,
valuation context, and DCF. No DB dependency, no I/O — every function
takes plain numbers/lists in and returns plain numbers/dicts/dataclasses
out, so each formula is unit-testable in isolation (see tests/test_metrics.py).

compute_fundamentals.py is responsible for pulling real rows out of
Security / Fundamentals / FinancialStatement and handing plain numbers here.
"""

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Margins
# ---------------------------------------------------------------------------

@dataclass
class MarginSnapshot:
    period_end: str
    gross_margin: Optional[float]
    operating_margin: Optional[float]
    net_margin: Optional[float]


def compute_margins(
    period_end: str,
    revenue: Optional[float],
    net_profit: Optional[float],
    operating_income: Optional[float] = None,
    gross_profit: Optional[float] = None,
) -> MarginSnapshot:
    """
    Compute margins for a single reporting period.

    NOTE on this project's actual schema: FinancialStatement stores
    revenue, net_profit, total_assets, total_liabilities,
    operating_cash_flow, and free_cash_flow — it does NOT store COGS
    or a distinct operating-income line. So in practice, called from
    compute_fundamentals.py, operating_income and gross_profit will
    always be None and those two margins will always come back None.
    Net margin (revenue vs net_profit) is the only one reliably
    computable from this schema today. Kept general here in case you
    extend FinancialStatement with those fields later.
    """
    def safe_ratio(numerator: Optional[float]) -> Optional[float]:
        if numerator is None or revenue in (None, 0):
            return None
        return round(numerator / revenue, 4)

    return MarginSnapshot(
        period_end=period_end,
        gross_margin=safe_ratio(gross_profit),
        operating_margin=safe_ratio(operating_income),
        net_margin=safe_ratio(net_profit),
    )


def classify_trend(values: list, noise_threshold: float = 0.005) -> Optional[str]:
    """
    Generic direction classifier: "increasing" / "decreasing" / "flat" / None
    (fewer than 2 usable points). Compares first vs. last value in the
    series against a noise threshold. Shared by margin_trend (which maps
    the result to "improving"/"declining" — higher margin is good) and
    debt_to_assets_trend (which reports direction as-is, since whether
    "increasing" is good or bad depends on context the caller decides).
    """
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None

    delta = clean[-1] - clean[0]
    if delta > noise_threshold:
        return "increasing"
    elif delta < -noise_threshold:
        return "decreasing"
    return "flat"


def margin_trend(snapshots: list[MarginSnapshot], field_name: str) -> Optional[str]:
    """
    "improving" / "declining" / "flat" / None (fewer than 2 usable points).
    Endpoint-slope check against a 0.5pp noise threshold — a signal, not
    a statistical claim.
    """
    values = [getattr(s, field_name) for s in snapshots if getattr(s, field_name) is not None]
    trend = classify_trend(values)
    if trend == "increasing":
        return "improving"
    elif trend == "decreasing":
        return "declining"
    return trend  # "flat" or None


# ---------------------------------------------------------------------------
# CAGR
# ---------------------------------------------------------------------------

def compute_cagr(start_value: Optional[float], end_value: Optional[float], years: float) -> Optional[float]:
    """
    (end/start)^(1/years) - 1. Returns None (never raises, never fabricates
    a number) when either value is missing, start_value <= 0, or years <= 0.
    start_value <= 0 matters especially for net_profit, which can go
    negative in a bad year — CAGR off a negative base is meaningless.
    """
    if start_value is None or end_value is None or years <= 0:
        return None
    if start_value <= 0:
        return None
    return round((end_value / start_value) ** (1 / years) - 1, 4)


def revenue_and_profit_cagr(annual_statements: list[dict], years: int) -> dict:
    """
    annual_statements: list of dicts with 'period_end', 'revenue',
    'net_profit', sorted oldest -> newest by the caller.

    Returns None for both if fewer than `years`+1 periods exist —
    a "5yr CAGR" computed from 3 years of data would be exactly the
    kind of silent accuracy bug this project has caught before
    (Infosys currency tag, Screener label collision).
    """
    if len(annual_statements) < years + 1:
        return {"revenue_cagr": None, "profit_cagr": None, "years_used": None}

    start = annual_statements[-(years + 1)]
    end = annual_statements[-1]

    return {
        "revenue_cagr": compute_cagr(start.get("revenue"), end.get("revenue"), years),
        "profit_cagr": compute_cagr(start.get("net_profit"), end.get("net_profit"), years),
        "years_used": years,
    }


# ---------------------------------------------------------------------------
# Balance sheet health & threshold checks (ROE, D/E, debt-to-assets)
# ---------------------------------------------------------------------------

def meets_threshold(value: Optional[float], threshold: float, direction: str) -> Optional[bool]:
    """
    Generic pass/fail check against a benchmark, e.g. "ROE >= 15%" or
    "D/E <= 0.5". Returns None (not False) when value is missing — a
    missing metric is a data gap, not a failed check, and conflating
    the two would silently misrepresent "we don't know" as "this failed."

    direction: "above" means value >= threshold is a pass (e.g. ROE).
               "below" means value <= threshold is a pass (e.g. D/E).
    """
    if value is None:
        return None
    if direction == "above":
        return value >= threshold
    elif direction == "below":
        return value <= threshold
    raise ValueError(f"direction must be 'above' or 'below', got {direction!r}")


def compute_debt_to_assets(total_liabilities: Optional[float], total_assets: Optional[float]) -> Optional[float]:
    """
    total_liabilities / total_assets — the fraction of the company's
    assets financed by debt rather than equity. Returns None if either
    input is missing or total_assets is zero, rather than raising.
    """
    if total_liabilities is None or total_assets in (None, 0):
        return None
    return round(total_liabilities / total_assets, 4)


def normalize_debt_to_equity_from_yfinance(raw_debt_to_equity: Optional[float]) -> Optional[float]:
    """
    yfinance's `debtToEquity` field is percentage-scaled (e.g. 36.65
    meaning 36.65%), unlike returnOnEquity/returnOnAssets, which come
    back as plain decimal fractions (e.g. 0.1384 meaning 13.84%). This
    inconsistency is a real, documented yfinance quirk, not a data
    error — but if left uncorrected it silently produces D/E ratios
    two orders of magnitude too large (a real company at 0.37 read as
    37), which would then fail every sane threshold check.

    Divides by 100 to bring it in line with the decimal-ratio convention
    used everywhere else in this project (roe, roce, margins are all
    decimal fractions). Returns None if input is None.
    """
    if raw_debt_to_equity is None:
        return None
    return round(raw_debt_to_equity / 100, 4)


# ---------------------------------------------------------------------------
# Valuation in context (vs. own history — no sector/peer data available)
# ---------------------------------------------------------------------------

@dataclass
class ValuationContext:
    current_pe: Optional[float]
    historical_pe_min: Optional[float]
    historical_pe_max: Optional[float]
    historical_pe_median: Optional[float]
    percentile_rank: Optional[float]
    note: str = ""


def compute_valuation_context(current_pe: Optional[float], historical_pe_series: list[float]) -> ValuationContext:
    """
    percentile_rank: % of historical P/E observations below the current
    one. High = rich vs. own history, low = cheap vs. own history.
    Descriptive positioning only, not a buy/sell signal.
    """
    clean_history = [v for v in historical_pe_series if v is not None and v > 0]

    if current_pe is None or not clean_history:
        return ValuationContext(
            current_pe=current_pe,
            historical_pe_min=None,
            historical_pe_max=None,
            historical_pe_median=None,
            percentile_rank=None,
            note="Insufficient historical P/E data for context.",
        )

    sorted_hist = sorted(clean_history)
    below = sum(1 for v in sorted_hist if v < current_pe)
    percentile = round(100 * below / len(sorted_hist), 1)
    median = sorted_hist[len(sorted_hist) // 2]

    return ValuationContext(
        current_pe=current_pe,
        historical_pe_min=sorted_hist[0],
        historical_pe_max=sorted_hist[-1],
        historical_pe_median=median,
        percentile_rank=percentile,
        note="Relative to this security's own historical P/E range only, not sector peers.",
    )


# ---------------------------------------------------------------------------
# Basic DCF (2-stage, FCF-based)
# ---------------------------------------------------------------------------

@dataclass
class DCFAssumptions:
    growth_rate_stage1: float
    projection_years: int
    terminal_growth_rate: float
    discount_rate: float


@dataclass
class DCFResult:
    base_fcf: float
    assumptions: DCFAssumptions
    projected_fcfs: list[float]
    pv_of_projected_fcfs: float
    terminal_value: float
    pv_of_terminal_value: float
    intrinsic_value: float
    intrinsic_value_per_share: Optional[float]
    shares_outstanding_used: Optional[float]
    shares_outstanding_is_estimated: bool
    warning: str


DEFAULT_ASSUMPTIONS = DCFAssumptions(
    growth_rate_stage1=0.10,
    projection_years=5,
    terminal_growth_rate=0.04,
    discount_rate=0.12,
)


@dataclass
class DerivedGrowthRate:
    rate: float
    was_derived: bool     # True if sourced from revenue_cagr_3y at all
    was_capped: bool       # True if the raw CAGR was outside the sane range and got clamped
    raw_cagr: Optional[float]


def derive_growth_rate_from_cagr(
    revenue_cagr_3y: Optional[float],
    floor: float = -0.10,
    cap: float = 0.30,
    fallback: float = DEFAULT_ASSUMPTIONS.growth_rate_stage1,
) -> DerivedGrowthRate:
    """
    Use the company's own 3yr revenue CAGR as the DCF's stage-1 growth
    assumption, instead of a flat 10% applied to every stock regardless
    of its actual growth profile.

    Clamped to [floor, cap] because a raw 3yr CAGR can be a wild number —
    a company recovering from one bad year can show a triple-digit CAGR
    that has nothing to do with sustainable forward growth, and projecting
    that forward 5 years would produce a DCF that looks precise but is
    nonsense. Default range (-10% to +30%) is deliberately generous but
    bounded; when clamping actually changes the value, that's flagged by
    the caller rather than silently substituting a different number.

    Falls back to the flat default (10%) when no 3yr CAGR is available at
    all (insufficient history) — same behavior as before this change,
    just no longer the ONLY behavior.
    """
    if revenue_cagr_3y is None:
        return DerivedGrowthRate(rate=fallback, was_derived=False, was_capped=False, raw_cagr=None)

    clamped = max(floor, min(cap, revenue_cagr_3y))
    was_capped = clamped != revenue_cagr_3y

    return DerivedGrowthRate(rate=clamped, was_derived=True, was_capped=was_capped, raw_cagr=revenue_cagr_3y)


def compute_dcf(
    base_fcf: Optional[float],
    shares_outstanding: Optional[float] = None,
    shares_outstanding_is_estimated: bool = False,
    assumptions: DCFAssumptions = DEFAULT_ASSUMPTIONS,
) -> Optional[DCFResult]:
    """
    2-stage DCF off free cash flow. Returns None if base_fcf is missing
    or non-positive — a DCF off a negative/zero base would produce a
    number that LOOKS like a real intrinsic value but is meaningless.

    shares_outstanding_is_estimated: this schema doesn't store share
    count directly. compute_fundamentals.py derives an approximation
    as net_profit / eps when both exist — pass that through here so it
    gets carried into the result and flagged, rather than presented as
    if it were as reliable as a real reported share count.
    """
    if base_fcf is None or base_fcf <= 0:
        return None

    if assumptions.discount_rate <= assumptions.terminal_growth_rate:
        raise ValueError(
            "discount_rate must exceed terminal_growth_rate "
            f"(got discount_rate={assumptions.discount_rate}, "
            f"terminal_growth_rate={assumptions.terminal_growth_rate})"
        )

    projected_fcfs = []
    fcf = base_fcf
    for _ in range(assumptions.projection_years):
        fcf = fcf * (1 + assumptions.growth_rate_stage1)
        projected_fcfs.append(round(fcf, 2))

    pv_fcfs = sum(
        cf / ((1 + assumptions.discount_rate) ** year)
        for year, cf in enumerate(projected_fcfs, start=1)
    )

    terminal_fcf = projected_fcfs[-1] * (1 + assumptions.terminal_growth_rate)
    terminal_value = terminal_fcf / (assumptions.discount_rate - assumptions.terminal_growth_rate)
    pv_terminal_value = terminal_value / ((1 + assumptions.discount_rate) ** assumptions.projection_years)

    intrinsic_value = pv_fcfs + pv_terminal_value

    intrinsic_value_per_share = None
    if shares_outstanding and shares_outstanding > 0:
        intrinsic_value_per_share = round(intrinsic_value / shares_outstanding, 2)

    warning = (
        "Highly sensitive to growth_rate_stage1, discount_rate, and "
        "terminal_growth_rate assumptions above. Treat as a rough "
        "sensitivity exercise, not a target price."
    )
    if shares_outstanding_is_estimated:
        warning += (
            " Per-share value uses an ESTIMATED share count "
            "(net_profit / eps), which may mismatch if the two figures "
            "come from different periods (e.g. annual net_profit vs "
            "TTM eps) — treat per-share figure as rougher still than "
            "the total intrinsic value above it."
        )

    return DCFResult(
        base_fcf=base_fcf,
        assumptions=assumptions,
        projected_fcfs=projected_fcfs,
        pv_of_projected_fcfs=round(pv_fcfs, 2),
        terminal_value=round(terminal_value, 2),
        pv_of_terminal_value=round(pv_terminal_value, 2),
        intrinsic_value=round(intrinsic_value, 2),
        intrinsic_value_per_share=intrinsic_value_per_share,
        shares_outstanding_used=shares_outstanding,
        shares_outstanding_is_estimated=shares_outstanding_is_estimated,
        warning=warning,
    )