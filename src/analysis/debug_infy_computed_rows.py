"""
One-off diagnostic: list every computed_fundamentals row for INFY.NS,
ordered by computed_at (has real timestamp precision, unlike as_of_date
which is date-only). Run: python -m src.analysis.debug_infy_computed_rows
"""

from src.models.db import SessionLocal
from src.models.schema import Security, ComputedFundamentals


def main():
    session = SessionLocal()
    try:
        infy = session.query(Security).filter_by(symbol="INFY.NS").first()
        if not infy:
            print("INFY.NS not found.")
            return

        rows = (
            session.query(ComputedFundamentals)
            .filter_by(security_id=infy.id)
            .order_by(ComputedFundamentals.computed_at.asc())
            .all()
        )

        print(f"{len(rows)} computed_fundamentals row(s) found for INFY.NS:\n")
        for r in rows:
            print(f"  id={r.id}  as_of_date={r.as_of_date}  computed_at={r.computed_at}")
            print(f"    dcf_intrinsic_value_per_share={r.dcf_intrinsic_value_per_share}")
            print(f"    data_quality_flags={r.data_quality_flags}")
            print()
    finally:
        session.close()


if __name__ == "__main__":
    main()