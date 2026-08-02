"""
Shared test fixtures.

Key design point: each ingestion module does `from src.models.db import
SessionLocal` — a *from-import* binds a new name in that module's own
namespace at import time. Patching `src.models.db.SessionLocal` after the
fact does NOT affect that already-bound name. So this fixture patches
`SessionLocal` directly on every ingestion module that uses it, pointing all
of them at one isolated, file-based SQLite test database per test.

`src.models.db.engine` is different: `init_db()` looks up the name `engine`
from its own module's globals at *call time*, not import time, so patching
`src.models.db.engine` alone is enough for `init_db()` to pick up the test
engine regardless of which module called it.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.schema import Base
import src.models.db as db_module
import src.ingestion.ingest_stock_price as ingest_stock_price
import src.ingestion.ingest_financial_statements as ingest_financial_statements
import src.ingestion.ingest_mutual_fund as ingest_mutual_fund
import src.ingestion.ingest_screener_excel as ingest_screener_excel


@pytest.fixture
def test_session_factory(tmp_path, monkeypatch):
    """Creates a fresh SQLite file per test and points every ingestion
    module's SessionLocal at it, so tests never touch the real dev DB."""
    db_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

    # init_db() reads `engine` from src.models.db's globals at call time.
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    # Each ingestion module bound its own `SessionLocal` at import time —
    # patch those bindings individually. ingest_screener_excel is included
    # even though its current tests don't use this fixture (they only call
    # the pure compute_ratios() function) — this is here so any future test
    # that exercises process_file() end-to-end doesn't silently write to the
    # real dev DB instead of this isolated one.
    monkeypatch.setattr(ingest_stock_price, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(ingest_financial_statements, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(ingest_mutual_fund, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(ingest_screener_excel, "SessionLocal", TestSessionLocal)

    return TestSessionLocal