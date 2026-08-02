"""
Tests for ingest_screener_excel.py's unit handling.

Screener's exports are in Rs. Crore. Everywhere else in this DB (market_cap,
financial_statements.revenue, etc.) uses raw INR. compute_ratios() must:
  - convert absolute values (EPS, EBITDA) from Cr to raw INR
  - NOT convert ratios (ROCE, ROE) — Cr cancels out in a ratio, so applying
    the conversion there would silently produce a wildly wrong number
    (multiplying a ~0.10 ratio by 1e7 would give 1,000,000 instead of 10%).

These tests use small, hand-picked numbers so the expected output can be
verified by hand, rather than relying on real company data matching by
coincidence.
"""

from src.ingestion.ingest_screener_excel import compute_ratios


def _minimal_annual(net_profit_cr, pbt_cr, interest_cr, depreciation_cr,
                     equity_cr, reserves_cr, borrowings_cr, num_shares):
    """Build a two-period annual dict (compute_ratios needs [-1] and [-2]
    for the capital-employed average) — both periods identical for simplicity
    except where the test needs to isolate a single period's effect."""
    return {
        "net_profit": (net_profit_cr, net_profit_cr),
        "pbt": (pbt_cr, pbt_cr),
        "interest": (interest_cr, interest_cr),
        "depreciation": (depreciation_cr, depreciation_cr),
        "equity": (equity_cr, equity_cr),
        "reserves": (reserves_cr, reserves_cr),
        "borrowings": (borrowings_cr, borrowings_cr),
        "num_shares": (num_shares, num_shares),
    }


def _minimal_quarters():
    """Not used when the annual block passes the sanity check, but
    compute_ratios still needs the key present."""
    return {
        "net_profit": (0, 0, 0, 0),
        "pbt": (0, 0, 0, 0),
        "interest": (0, 0, 0, 0),
        "depreciation": (0, 0, 0, 0),
    }


def test_ebitda_converts_crore_to_raw_inr():
    """100 Cr PBT + 10 Cr Interest + 20 Cr Depreciation = 130 Cr EBITDA.
    In raw INR that must be 130 * 1e7 = 1,300,000,000 — not 130."""
    annual = _minimal_annual(
        net_profit_cr=80, pbt_cr=100, interest_cr=10, depreciation_cr=20,
        equity_cr=500, reserves_cr=1500, borrowings_cr=1000, num_shares=100_000_000,
    )
    quarters = _minimal_quarters()

    # db_net_profit_inr matches exactly (80 Cr = 800,000,000 INR) so the
    # sanity check passes and the annual block (not TTM fallback) is used.
    result = compute_ratios(annual, quarters, db_net_profit_inr=800_000_000)

    assert result["method"] == "annual-direct"
    expected_ebitda_raw_inr = 130 * 1e7
    assert result["ebitda"] == expected_ebitda_raw_inr, (
        f"expected {expected_ebitda_raw_inr:,.0f} INR (130 Cr converted), "
        f"got {result['ebitda']:,.0f} — the Cr->INR conversion may have been dropped"
    )


def test_eps_converts_crore_to_raw_inr():
    """80 Cr net profit / 100,000,000 shares = Rs 8.00 EPS.
    This only works if net_profit is converted to raw INR before dividing —
    dividing 80 (Cr) by 100,000,000 (raw share count) directly would give
    a nonsensical EPS of 0.0000008."""
    annual = _minimal_annual(
        net_profit_cr=80, pbt_cr=100, interest_cr=10, depreciation_cr=20,
        equity_cr=500, reserves_cr=1500, borrowings_cr=1000, num_shares=100_000_000,
    )
    quarters = _minimal_quarters()
    result = compute_ratios(annual, quarters, db_net_profit_inr=800_000_000)

    assert result["eps"] == 8.0, (
        f"expected EPS of Rs 8.00, got {result['eps']} — "
        f"check the *1e7 conversion in the EPS calculation"
    )


def test_roce_and_roe_are_not_over_converted():
    """ROCE/ROE are ratios of two Cr-denominated figures, so Cr cancels out
    naturally. If someone "fixes" these by also multiplying by 1e7 (copying
    the EBITDA/EPS pattern without thinking), the ratio would become
    astronomically large instead of a normal percentage. This test catches
    that specific mistake."""
    annual = _minimal_annual(
        net_profit_cr=80, pbt_cr=100, interest_cr=10, depreciation_cr=20,
        equity_cr=500, reserves_cr=1500, borrowings_cr=1000, num_shares=100_000_000,
    )
    quarters = _minimal_quarters()
    result = compute_ratios(annual, quarters, db_net_profit_inr=800_000_000)

    # ROE = 80 / (500 + 1500) = 0.04 (4%)
    assert abs(result["roe"] - 0.04) < 1e-9, f"ROE should be 0.04, got {result['roe']}"
    # ROCE = (100 + 10) / ((500+1500+1000 + 500+1500+1000) / 2) = 110 / 3000 = 0.0367
    expected_roce = 110 / 3000
    assert abs(result["roce"] - expected_roce) < 1e-9, f"ROCE should be ~{expected_roce:.4f}, got {result['roce']}"

    # Both must be small ratios (well under 1.0 for any realistic company),
    # never in the millions — that's the signature of an accidental 1e7 conversion.
    assert result["roe"] < 1.0
    assert result["roce"] < 1.0


def test_ttm_fallback_also_converts_units_correctly():
    """Same conversion checks, but forcing the TTM-fallback path (via a
    sanity-check mismatch) to make sure the conversion logic isn't only
    correct on the annual-direct branch."""
    annual = _minimal_annual(
        net_profit_cr=80, pbt_cr=100, interest_cr=10, depreciation_cr=20,
        equity_cr=500, reserves_cr=1500, borrowings_cr=1000, num_shares=100_000_000,
    )
    quarters = {
        "net_profit": (20, 20, 20, 20),   # sums to 80 Cr TTM
        "pbt": (25, 25, 25, 25),          # sums to 100 Cr TTM
        "interest": (2.5, 2.5, 2.5, 2.5), # sums to 10 Cr TTM
        "depreciation": (5, 5, 5, 5),     # sums to 20 Cr TTM
    }

    # Deliberately mismatched DB figure to force the TTM fallback path.
    result = compute_ratios(annual, quarters, db_net_profit_inr=1)

    assert result["method"] == "ttm-fallback"
    assert result["ebitda"] == 130 * 1e7
    assert result["eps"] == 8.0