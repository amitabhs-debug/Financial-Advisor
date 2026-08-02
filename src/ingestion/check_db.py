"""
Sanity check: query the DB after running ingestion and print a summary.
Run locally: python -m src.ingestion.check_db
"""

from src.models.db import SessionLocal
from src.models.schema import Security, PriceHistory, Fundamentals, FinancialStatement, MutualFundNav


def main():
    session = SessionLocal()
    try:
        securities = session.query(Security).all()
        print(f"Securities in DB: {len(securities)}")
        for s in securities:
            print(f"\n{s.symbol} ({s.name}) [{s.type}]")
            print(f"  Category/Sector: {s.sector} / {s.industry}")

            if s.type == "mutual_fund":
                nav_count = session.query(MutualFundNav).filter_by(security_id=s.id).count()
                latest_nav = (
                    session.query(MutualFundNav)
                    .filter_by(security_id=s.id)
                    .order_by(MutualFundNav.date.desc())
                    .first()
                )
                print(f"  NAV rows: {nav_count}")
                if latest_nav:
                    print(f"  Latest NAV ({latest_nav.date}): {latest_nav.nav}")
                continue

            price_count = session.query(PriceHistory).filter_by(security_id=s.id).count()
            latest_price = (
                session.query(PriceHistory)
                .filter_by(security_id=s.id)
                .order_by(PriceHistory.date.desc())
                .first()
            )
            fund = (
                session.query(Fundamentals)
                .filter_by(security_id=s.id)
                .order_by(Fundamentals.as_of_date.desc())
                .first()
            )
            print(f"  Price rows: {price_count}")
            if latest_price:
                print(f"  Latest close ({latest_price.date}): {latest_price.close}")
            if fund:
                print(f"  P/E: {fund.pe_ratio}, P/B: {fund.pb_ratio}, ROE: {fund.roe}")

            annual_stmts = (
                session.query(FinancialStatement)
                .filter_by(security_id=s.id, period_type="annual")
                .order_by(FinancialStatement.period_end.desc())
                .limit(3)
                .all()
            )
            if annual_stmts:
                print(f"  Annual financials (last {len(annual_stmts)} periods):")
                for stmt in annual_stmts:
                    rev = f"{stmt.revenue:,.0f}" if stmt.revenue else "N/A"
                    profit = f"{stmt.net_profit:,.0f}" if stmt.net_profit else "N/A"
                    print(f"    {stmt.period_end} [{stmt.currency}]: Revenue={rev}  Net Profit={profit}")
    finally:
        session.close()


if __name__ == "__main__":
    main()