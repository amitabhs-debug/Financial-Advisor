# Stock/Mutual Fund Analyzer — Data Layer (v0)

## Setup (run these locally — this sandbox has no network access)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

## Step 1 — validate the data source (no DB yet)

```bash
python -m src.ingestion.validate_yfinance
```

Expect: 30 rows of RELIANCE.NS OHLCV data printed, plus a few fundamentals
fields (PE, P/B, ROE, market cap). If `info.get(...)` returns `None` for
some fields, that's normal — yfinance's Indian ticker coverage is inconsistent,
which is exactly why we'll add Screener.in later for deeper fundamentals.

## Step 2 — initialize the database

```bash
python -m src.models.db
```

Creates `data/stock_analyzer.db` with all 6 tables (securities, price_history,
fundamentals, financial_statements, mutual_fund_nav, ingestion_log).

## Step 3 — next script to build

Once both of the above run cleanly, the next step is an ingestion script that
combines them: fetch RELIANCE.NS via yfinance, upsert into `securities`, bulk
insert its price history, and log the run to `ingestion_log`. Say the word
and we'll build that next — it's the first script that actually touches the
database.

## Project layout

```
src/
  models/       # SQLAlchemy schema + DB session (schema.py, db.py)
  ingestion/    # scripts that pull from yfinance / AMFI / Screener / bhavcopy
  utils/        # shared helpers (rate limiting, validation, etc.)
tests/          # pytest tests
data/           # SQLite DB lives here (gitignored)
```

