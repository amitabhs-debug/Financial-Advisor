"""
Tracks which filings have already been fetched, so fetch_filings.py /
register_manual_filings.py are idempotent on re-run -- same purpose as
Phase 1's `ingestion_log` table.

Deliberately kept as its own standalone SQLite file (not wired into
your main SQLAlchemy schema) for now, so this whole RAG layer can be
built and tested in isolation without touching the existing schema.py.
If you want it folded into the main DB later, this is a straightforward
port to a `mapped_column`-style table -- same shape, just move the
CREATE TABLE into schema.py and swap the sqlite3 calls for a Session.

doc_type ('quarterly' or 'annual') is part of the uniqueness key, not
just descriptive metadata -- without it, a quarterly and an annual
filing for the same symbol that happened to share a date string would
collide and one would be silently skipped as a "duplicate."

MIGRATION NOTE: if you already have a data/filings/document_log.db
from before doc_type was added, delete that file and re-run
register_manual_filings.py -- it's idempotent and cheap to rebuild,
simpler than an ALTER TABLE migration for a personal project at this
scale.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_LOG_PATH = Path("data/filings/document_log.db")


class DocumentLog:
    def __init__(self, db_path: Path = DEFAULT_LOG_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fetched_filings (
                symbol TEXT NOT NULL,
                filing_date TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                pdf_url TEXT NOT NULL,
                local_path TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, filing_date, doc_type, pdf_url)
            )
            """
        )
        self._conn.commit()

    def already_processed(self, symbol: str, filing_date: str, doc_type: str, pdf_url: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM fetched_filings WHERE symbol = ? AND filing_date = ? AND doc_type = ? AND pdf_url = ?",
            (symbol, filing_date, doc_type, pdf_url),
        )
        return cur.fetchone() is not None

    def record(self, symbol: str, filing_date: str, doc_type: str, pdf_url: str, local_path: str) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO fetched_filings
                (symbol, filing_date, doc_type, pdf_url, local_path, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (symbol, filing_date, doc_type, pdf_url, local_path, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()