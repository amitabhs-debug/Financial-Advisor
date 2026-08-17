"""
One-off diagnostic: what currency is actually stored for INFY's
annual financial statements? Run: python -m src.analysis.debug_infy_currency
"""

from src.models.db import SessionLocal
from src.models.schema import Security, FinancialStatement


def main():
    session = SessionLocal()
    try:
        infy = session.query(Security).filter_by(symbol="INFY.NS").first()
        if not infy:
            print("INFY.NS not found in securities table.")
            return

        stmts = (
            session.query(FinancialStatement)
            .filter_by(security_id=infy.id, period_type="annual")
            .order_by(FinancialStatement.period_end.asc())
            .all()
        )

        if not stmts:
            print("No annual FinancialStatement rows found for INFY.NS.")
            return

        for s in stmts:
            print(f"period_end={s.period_end}  currency={s.currency!r}  "
                  f"revenue={s.revenue}  net_profit={s.net_profit}  "
                  f"free_cash_flow={s.free_cash_flow}")
    finally:
        session.close()


if __name__ == "__main__":
    main()