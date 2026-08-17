"""
Sanity check for Phase 2 output: print the latest computed_fundamentals
row per security. Run locally: python -m src.analysis.check_fundamentals
"""

from src.models.db import SessionLocal
from src.models.schema import Security, ComputedFundamentals


def main():
    session = SessionLocal()
    try:
        securities = session.query(Security).filter(Security.type == "stock").all()

        for s in securities:
            latest = (
                session.query(ComputedFundamentals)
                .filter_by(security_id=s.id)
                .order_by(ComputedFundamentals.as_of_date.desc(), ComputedFundamentals.computed_at.desc())
                .first()
            )

            print(f"\n{s.symbol} ({s.name})")
            if not latest:
                print("  No computed_fundamentals row found.")
                continue

            def pct(v):
                return f"{v:.2%}" if v is not None else "N/A"

            print(f"  As of: {latest.as_of_date}")
            print(f"  Net margin: {pct(latest.net_margin)}  (trend: {latest.margin_trend_net or 'N/A'})")
            print(f"  Gross margin: {pct(latest.gross_margin)}  Operating margin: {pct(latest.operating_margin)}")
            print(f"  Revenue CAGR 3y: {pct(latest.revenue_cagr_3y)}  5y: {pct(latest.revenue_cagr_5y)}")
            print(f"  Profit CAGR 3y: {pct(latest.profit_cagr_3y)}  5y: {pct(latest.profit_cagr_5y)}")
            print(f"  Current P/E: {latest.current_pe}  Percentile vs own history: {latest.pe_percentile_rank}")

            roe_str = pct(latest.roe) if latest.roe is not None else "N/A"
            roe_pass = "PASS" if latest.roe_meets_threshold else ("FAIL" if latest.roe_meets_threshold is False else "N/A")
            print(f"  ROE: {roe_str} ({roe_pass} vs 15% threshold)")

            de_str = f"{latest.debt_to_equity:.2f}x" if latest.debt_to_equity is not None else "N/A"
            de_pass = "PASS" if latest.debt_to_equity_meets_threshold else ("FAIL" if latest.debt_to_equity_meets_threshold is False else "N/A")
            print(f"  Debt-to-Equity: {de_str} ({de_pass} vs 0.5x threshold)")

            das_str = pct(latest.debt_to_assets_ratio) if latest.debt_to_assets_ratio is not None else "N/A"
            print(f"  Debt-to-Assets: {das_str}  (trend: {latest.debt_to_assets_trend or 'N/A'})")

            if latest.dcf_intrinsic_value is not None:
                print(f"  DCF intrinsic value: Rs {latest.dcf_intrinsic_value:,.0f}")
                if latest.dcf_intrinsic_value_per_share is not None:
                    print(f"  DCF per share: Rs {latest.dcf_intrinsic_value_per_share:,.2f}")
            else:
                print("  DCF: not computed")

            print(f"  Data quality flags: {latest.data_quality_flags or 'none'}")

    finally:
        session.close()


if __name__ == "__main__":
    main()