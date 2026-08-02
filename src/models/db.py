"""
Database engine + session factory.
Swapping SQLite for Postgres later means changing DATABASE_URL in .env only —
nothing else in the codebase needs to change.
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.schema import Base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/stock_analyzer.db")

# check_same_thread=False is needed for SQLite when used outside a single thread
# (e.g. later with FastAPI). Harmless for now.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db():
    """Create all tables if they don't exist yet. Safe to call repeatedly."""
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DATABASE_URL}")
