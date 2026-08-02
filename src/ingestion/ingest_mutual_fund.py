"""
Phase 1b: fetch mutual fund scheme metadata + historical NAV via mftool
(which wraps AMFI's data) and persist to `securities` / `mutual_fund_nav`.

Mirrors ingest_stock_price.py's pattern:
  1. check ingestion_log — skip if fetched recently
  2. fetch from source
  3. upsert into `securities` (type="mutual_fund")
  4. bulk upsert into `mutual_fund_nav`
  5. log the outcome

Run locally: python -m src.ingestion.ingest_mutual_fund [SCHEME_CODE]
"""

import sys
from datetime import datetime, timedelta

import pandas as pd
from mftool import Mftool
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.models.db import SessionLocal, init_db
from src.models.schema import Security, MutualFundNav, IngestionLog
from src.utils.rate_limit import retry_with_backoff

SCHEME_CODE = "120503"  # placeholder default; watchlist drives the real run
SKIP_IF_FETCHED_WITHIN_HOURS = 20  # NAV updates once per business day


def already_fetched_recently(session, symbol: str) -> bool:
    cutoff = datetime.utcnow() - timedelta(hours=SKIP_IF_FETCHED_WITHIN_HOURS)
    stmt = (
        select(IngestionLog)
        .join(Security, IngestionLog.security_id == Security.id)
        .where(
            Security.symbol == symbol,
            IngestionLog.source == "amfi_mftool",
            IngestionLog.status == "success",
            IngestionLog.last_fetched_at > cutoff,
        )
        .order_by(IngestionLog.last_fetched_at.desc())
    )
    return session.execute(stmt).first() is not None


def upsert_security(session, symbol: str, details: dict) -> Security:
    """symbol here is the AMFI scheme code, stored as a string in the same
    `symbol` column stocks use — keeps `securities` a single unified table."""
    name = details.get("scheme_name", symbol)
    # Reusing `sector`/`industry` columns for fund category/fund house rather
    # than adding fund-specific columns — keeps one securities table for both
    # asset types, at the cost of the column names being a bit stock-flavored.
    category = details.get("scheme_category")
    fund_house = details.get("fund_house")

    stmt = (
        sqlite_insert(Security)
        .values(
            symbol=symbol,
            name=name,
            type="mutual_fund",
            exchange=None,
            sector=category,
            industry=fund_house,
            isin=None,
        )
        .on_conflict_do_update(
            index_elements=["symbol"],
            set_=dict(name=name, sector=category, industry=fund_house),
        )
        .returning(Security.id)
    )
    security_id = session.execute(stmt).scalar_one()
    session.commit()
    return session.get(Security, security_id)


def upsert_nav_history(session, security_id: int, nav_df: pd.DataFrame) -> int:
    """Bulk upsert NAV rows. nav_df expected with a date-parseable index and
    a 'nav' column, as returned by mftool's get_scheme_historical_nav."""
    rows = []
    for idx, row in nav_df.iterrows():
        try:
            nav_date = pd.to_datetime(idx, format="%d-%m-%Y").date()
        except (ValueError, TypeError):
            nav_date = pd.to_datetime(idx).date()

        nav_value = row.get("nav")
        if nav_value is None or pd.isna(nav_value):
            continue

        rows.append(dict(
            security_id=security_id,
            date=nav_date,
            nav=float(nav_value),
        ))

    if not rows:
        return 0

    stmt = sqlite_insert(MutualFundNav).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["security_id", "date"],
        set_=dict(nav=stmt.excluded.nav),
    )
    session.execute(stmt)
    session.commit()
    return len(rows)


def log_ingestion(session, security_id: int | None, status: str, error: str | None = None):
    session.add(IngestionLog(
        source="amfi_mftool",
        security_id=security_id,
        last_fetched_at=datetime.utcnow(),
        status=status,
        error_message=error,
    ))
    session.commit()


@retry_with_backoff(max_attempts=3, base_delay=2.0)
def _fetch_from_amfi(scheme_code: str):
    mf = Mftool()
    details = mf.get_scheme_details(scheme_code)
    if not details:
        raise ValueError(f"No scheme details returned for code {scheme_code} — "
                          f"check the code is correct via search_mf_scheme.py")

    nav_df = mf.get_scheme_historical_nav(scheme_code, as_Dataframe=True)
    if nav_df is None or nav_df.empty:
        raise ValueError(f"No historical NAV returned for scheme {scheme_code}")

    return details, nav_df


def ingest(scheme_code: str = SCHEME_CODE):
    scheme_code = str(scheme_code)
    init_db()
    session = SessionLocal()

    try:
        if already_fetched_recently(session, scheme_code):
            print(f"{scheme_code}: fetched within the last "
                  f"{SKIP_IF_FETCHED_WITHIN_HOURS}h, skipping.")
            return

        print(f"Fetching scheme {scheme_code} from AMFI...")
        details, nav_df = _fetch_from_amfi(scheme_code)

        security = upsert_security(session, scheme_code, details)
        rows_written = upsert_nav_history(session, security.id, nav_df)
        log_ingestion(session, security.id, "success")

        print(f"Done. {details.get('scheme_name', scheme_code)}: "
              f"{rows_written} NAV rows upserted.")

    except Exception as e:
        print(f"FAILED for {scheme_code}: {e}", file=sys.stderr)
        log_ingestion(session, None, "failed", str(e))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else SCHEME_CODE
    ingest(code)