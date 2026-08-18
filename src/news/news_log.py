"""
Tracks which news articles have already been fetched and analyzed, so
fetch_news.py and analyze_sentiment.py are idempotent on re-run --
same pattern as document_log.py in the RAG layer.

Standalone SQLite table, same rationale as document_log.py: keeps this
module testable in isolation without touching the main ORM schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_LOG_PATH = Path("data/news/news_log.db")


class NewsLog:
    def __init__(self, db_path: Path = DEFAULT_LOG_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                article_url TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                headline TEXT NOT NULL,
                published_at TEXT,
                source TEXT,
                marketaux_sentiment_score REAL,
                claude_sentiment_label TEXT,
                claude_reasoning TEXT,
                fetched_at TEXT NOT NULL,
                analyzed_at TEXT
            )
            """
        )
        self._conn.commit()

    def article_exists(self, article_url: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM articles WHERE article_url = ?", (article_url,)
        )
        return cur.fetchone() is not None

    def record_article(
        self, article_url: str, symbol: str, headline: str,
        published_at: str | None, source: str | None,
        marketaux_sentiment_score: float | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO articles
                (article_url, symbol, headline, published_at, source,
                 marketaux_sentiment_score, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_url, symbol, headline, published_at, source,
                marketaux_sentiment_score, datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def needs_claude_analysis(self, limit: int = 50) -> list[dict]:
        """
        Returns articles that have been fetched but not yet run through
        Claude's sentiment analysis (claude_sentiment_label IS NULL).
        This is the idempotency boundary for analyze_sentiment.py -- an
        article already analyzed is never re-sent to the API, so re-
        running the script never re-spends API budget on old articles.
        """
        cur = self._conn.execute(
            """
            SELECT article_url, symbol, headline, published_at, source
            FROM articles
            WHERE claude_sentiment_label IS NULL
            LIMIT ?
            """,
            (limit,),
        )
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def record_claude_analysis(self, article_url: str, label: str, reasoning: str) -> None:
        self._conn.execute(
            """
            UPDATE articles
            SET claude_sentiment_label = ?, claude_reasoning = ?, analyzed_at = ?
            WHERE article_url = ?
            """,
            (label, reasoning, datetime.now(timezone.utc).isoformat(), article_url),
        )
        self._conn.commit()

    def get_recent_for_symbol(self, symbol: str, limit: int = 20) -> list[dict]:
        cur = self._conn.execute(
            """
            SELECT article_url, headline, published_at, source,
                   marketaux_sentiment_score, claude_sentiment_label, claude_reasoning
            FROM articles
            WHERE symbol = ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (symbol, limit),
        )
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()