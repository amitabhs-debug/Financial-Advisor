"""
Phase 1 (final piece): promoter holding % and pledged shares %.

Screener.in's "Export to Excel" feature does NOT include shareholding
pattern data — it lives on a separate page tab, not in the export — so
there is no file to parse here. This is the most manual data source in the
project: the user looks up each stock's Shareholding Pattern on Screener.in
directly and fills in data/shareholding.csv by hand.

This script only validates that entered values are PLAUSIBLE (0-100,
parses as a number) — it cannot catch a value that's wrong but plausible
(e.g. an old quarter's figure entered by mistake). See README "Known
limitations" for the full caveat.

Run locally: python -m src.ingestion.ingest_shareholding
"""

import csv
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from src.models.db import SessionLocal, init_db
from src.models.schema import Security, Fundamentals, IngestionLog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = PROJECT_ROOT / "data" / "shareholding.csv"


def _parse_pct(raw: str, field_name: str, symbol: str) -> float | None:
    """Parses a percentage value, returns None (with a warning printed) if
    blank, unparseable, or outside the plausible 0-100 range."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        print(f"  [{symbol}] Could not parse {field_name}='{raw}' as a number — skipping this field.")
        return None
    if not (0 <= value <= 100):
        print(f"  [{symbol}] {field_name}={value} is outside the plausible 0-100% range — "
              f"skipping this field. Check for a typo (e.g. extra digit).")
        return None
    return value


def _parse_date(raw: str) -> date:
    raw = (raw or "").strip()
    if not raw:
        return date.today()
    return datetime.strptime(raw, "%Y-%m-%d").date()


def get_security(session, symbol: str):
    return session.execute(
        select(Security).where(Security.symbol == symbol)
    ).scalar_one_or_none()


def update_shareholding(session, security_id: int, promoter_pct, pledged_pct, as_of_date):
    """Updates the most recent fundamentals row. Does not create a new row —
    this data supplements an existing snapshot, it doesn't stand alone."""
    stmt = (
        select(Fundamentals)
        .where(Fundamentals.security_id == security_id)
        .order_by(Fundamentals.as_of_date.desc())
    )
    row = session.execute(stmt).scalars().first()
    if row is None:
        print(f"  No existing fundamentals row for security_id={security_id} — "
              f"run ingest_stock_price first. Skipping.")
        return False

    if promoter_pct is not None:
        row.promoter_holding_pct = promoter_pct
    if pledged_pct is not None:
        row.pledged_pct = pledged_pct
    session.commit()
    return True


def log_ingestion(session, security_id, status, error=None):
    session.add(IngestionLog(
        source="manual_shareholding",
        security_id=security_id,
        last_fetched_at=datetime.utcnow(),
        status=status,
        error_message=error,
    ))
    session.commit()


def main():
    if not CSV_PATH.exists():
        print(f"No shareholding CSV found at {CSV_PATH}. "
              f"Create it with columns: symbol,promoter_holding_pct,pledged_pct,as_of_date")
        return

    init_db()
    session = SessionLocal()

    updated, skipped_no_data, skipped_no_security = [], [], []

    try:
        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = (row.get("symbol") or "").strip()
                if not symbol:
                    continue

                promoter_pct = _parse_pct(row.get("promoter_holding_pct"), "promoter_holding_pct", symbol)
                pledged_pct = _parse_pct(row.get("pledged_pct"), "pledged_pct", symbol)

                if promoter_pct is None and pledged_pct is None:
                    skipped_no_data.append(symbol)
                    continue

                security = get_security(session, symbol)
                if security is None:
                    print(f"  [{symbol}] Not found in securities table — "
                          f"run ingest_stock_price for it first. Skipping.")
                    skipped_no_security.append(symbol)
                    continue

                as_of = _parse_date(row.get("as_of_date"))
                success = update_shareholding(session, security.id, promoter_pct, pledged_pct, as_of)

                if success:
                    log_ingestion(session, security.id, "success")
                    updated.append(symbol)
                    print(f"  [{symbol}] Updated: promoter_holding={promoter_pct}, pledged={pledged_pct}")

    finally:
        session.close()

    print("\n--- Shareholding ingestion complete ---")
    print(f"Updated: {len(updated)} -> {updated}")
    if skipped_no_data:
        print(f"Skipped (no data filled in yet): {len(skipped_no_data)} -> {skipped_no_data}")
    if skipped_no_security:
        print(f"Skipped (symbol not in DB): {len(skipped_no_security)} -> {skipped_no_security}")


if __name__ == "__main__":
    main()