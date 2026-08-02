# Indian Equity & Mutual Fund Research Tool — Data Layer (Phase 1)

A personal research-automation tool for Indian equities and mutual funds.
It is a **decision-support system**, not a return predictor: it automates
the tedious parts of fundamental analysis (pulling financials, computing
ratios, keeping data current) so the actual investment judgment stays with
the user, informed by better and faster data than manual lookup allows.

Phase 1 (the data layer) is complete. This README documents what's built,
how to run it, and — importantly — what its limitations are.

---

## What's built

### Data sources
- **yfinance** — daily price history (OHLCV), basic fundamentals (P/E, P/B,
  ROE, market cap), and quarterly/annual financial statements (revenue, net
  profit, balance sheet, cash flow) for NSE-listed stocks.
- **AMFI (via `mftool`)** — daily NAV history and scheme metadata for mutual
  funds.
- **Screener.in** (manual export + parser) — fills gaps yfinance leaves:
  ROCE, EPS, and EBITDA, computed from Screener's raw annual/quarterly data
  and cross-validated against the yfinance-sourced figures already in the
  database.
- **Screener.in shareholding pattern** (manual CSV entry) — promoter
  holding % and pledged shares %, not available in any automated export.

### Database schema (SQLite via SQLAlchemy ORM)
Six tables: `securities` (master record for stocks and mutual funds alike),
`price_history`, `fundamentals`, `financial_statements`, `mutual_fund_nav`,
`ingestion_log`. All upserts are idempotent — re-running any ingestion
script never creates duplicate rows.

### Ingestion scripts (`src/ingestion/`)
- `ingest_stock_price.py` — price + basic fundamentals for one stock
- `ingest_financial_statements.py` — revenue/net profit/cash flow, with
  explicit currency tracking (INR vs USD)
- `ingest_mutual_fund.py` — NAV history for one mutual fund scheme
- `search_mf_scheme.py` — look up AMFI scheme codes by keyword
- `run_ingestion.py` — orchestrator; reads `watchlist.yaml` and
  `mf_watchlist.yaml`, ingests everything, isolates per-symbol failures
- `ingest_screener_excel.py` — parses manually-exported Screener.in Excel
  files, computes ROCE/EPS/EBITDA, cross-checks against existing DB data
- `ingest_shareholding.py` — reads a manually-filled CSV of promoter
  holding % and pledged shares %, writes them into `fundamentals`
- `check_db.py` — prints a summary of everything currently in the DB

### Tests (`tests/`, run via `pytest`)
Tests covering: idempotency (no duplicate rows on repeated ingestion),
currency-tracking correctness, missing-field handling, orchestrator failure
isolation (one bad symbol doesn't kill the whole run), unit-conversion
correctness in the Screener parser (Rs. Crore → raw INR for absolute
values, left unconverted for ratios), and shareholding CSV validation.

---

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m src.models.db
```

## Running ingestion

```powershell
python -m src.ingestion.run_ingestion      # stocks + mutual funds
python -m src.ingestion.check_db           # verify what's in the DB
pytest                                     # run the test suite
```

For Screener.in ratio data (optional, fills ROCE/EPS/EBITDA):
1. Log into screener.in (free account)
2. For each watchlist stock, open its **consolidated** page and click
   "Export to Excel"
3. Save files into `data/screener_exports/` named `SYMBOL.xlsx` — the
   symbol must match the root of the NSE ticker exactly as it appears in
   `watchlist.yaml` (e.g. `INFY.xlsx`, not `INFOSYS.xlsx`)
4. Run `python -m src.ingestion.ingest_screener_excel`

For shareholding data (promoter holding %, pledged shares %):
1. Open `data/shareholding.csv`
2. For each stock, look up its Shareholding Pattern on Screener.in (below
   the main ratios on the company page) and fill in the current values
3. Run `python -m src.ingestion.ingest_shareholding`

---

## Known limitations

This section exists so these gaps are remembered, not rediscovered. Some
are fundamental constraints of the data sources; others are conscious
scope decisions for a personal project.

**Screener.in has no API.** Their terms explicitly say so. The Excel
export used here is a legitimate account feature (not automated scraping),
which is why it's a manual step rather than something `run_ingestion.py`
does automatically. This means Screener-derived data (ROCE, EPS, EBITDA,
promoter holding, pledged shares) goes stale between manual updates — it
is not kept current the way yfinance/AMFI data is.

**The Screener Excel parser is validated against real files (Reliance,
HDFC Bank) with a real bug found and fixed along the way** (a
label-lookup collision that silently misread the wrong section of the
sheet). The fix is covered by unit tests for the *unit-conversion
arithmetic* (Rs. Crore → raw INR), but there is no automated test for the
*row-extraction* logic itself, because that depends on Screener's actual
file layout, which isn't something to hardcode into a unit test. **If
Screener changes their export format, nothing in this test suite will
catch it** — only re-running the parser against a fresh export and
eyeballing the output would surface a problem. Treat any new company's
first Screener import with the same scrutiny the original two got:
cross-check the net profit figure against what's already in
`financial_statements` before trusting the rest.

**Companies whose yfinance financial statements are tagged USD can never
use the Screener parser's "annual-direct" path — they always fall back to
TTM.** Concretely: this happened for Infosys. The cross-check in
`ingest_screener_excel.py` only compares against `financial_statements`
rows where `currency == "INR"` (since Screener's export is always in
Rs. Crore). Infosys's yfinance data is tagged USD (the same ADR-listing
quirk caught earlier in Phase 1a), so there is no INR row to compare
against — the script finds nothing to verify with and, by design, defaults
to the safer TTM-from-quarters method rather than trusting unverified
annual data. This is not a bug: it's the intended conservative behavior
when verification isn't possible. But it does mean Infosys's ROCE/EPS/
EBITDA are computed on a rolling 4-quarter window rather than its true
fiscal year, while INR-reporting companies (Reliance, TCS, HDFC Bank,
ICICI Bank) get the more precise annual figures. TCS is also ADR-adjacent
but happened to be tagged INR by yfinance, so it does not hit this path —
this is a per-company yfinance quirk, not a rule about IT companies or
ADR-listed companies generally. Fixing this properly would require a
USD→INR conversion step (needing an exchange-rate source), which is a
deliberate scope decision to defer, not an oversight.

**ROCE and EBITDA are not meaningful for banks.** Both metrics assume
"interest" is a financing cost to add back and that "capital employed" is
a coherent concept — neither holds for a bank, where interest income/expense
*is* the core business. The parser will still compute a number for
HDFC Bank / ICICI Bank, but it should not be trusted the way it can be for
an industrial company like Reliance or TCS. ROE remains meaningful for
banks; ROCE and EBITDA do not. Proper bank-specific ratios (NIM, CASA
ratio, gross/net NPA) are not implemented — this was flagged at project
start and remains an open gap.

**Shareholding data (promoter holding %, pledged shares %) is entered by
hand, with no automated validation against a live source.** Screener's
"Export to Excel" feature does not include shareholding pattern data at
all — it lives on a separate page tab, not in the export — so there is no
file to parse. `ingest_shareholding.py` only validates that entered values
are plausible percentages (0-100) and catches obvious typos; it cannot
catch a value that's simply wrong but plausible (e.g. transcribing last
quarter's figure instead of the current one). This is the least reliable
data source in the project, precisely because it's the most manual.

**Currency mismatches are tracked, not resolved.** ADR-listed companies
sometimes report financial statements in USD via yfinance while others
report in INR. This is now tracked explicitly (a `currency` column on
`financial_statements`), which prevents silent corruption — but no
currency *conversion* is performed. Comparing an INR-reporting company
against a USD-reporting one still requires manual awareness of this until
a conversion step is built. (See also the Infosys/TTM-fallback limitation
above, which is a direct downstream consequence of this.)

**Standalone vs. consolidated is not verified programmatically.** yfinance
and Screener are both assumed to be providing consolidated figures for
group companies with subsidiaries. This was checked manually for Reliance
and HDFC Bank but is not automatically verified for any new company added
to the watchlist. Mismatched scope (standalone vs. consolidated) would be
a silent, high-impact data error if it occurred.

**The TTM (trailing-twelve-month) fallback is an approximation.** When the
Screener parser's sanity check fails, or can't run at all (e.g. Infosys,
see above), it computes ROCE/ROE/EBITDA/EPS by summing the last 4 reported
quarters instead of using true fiscal-year figures. This is a standard and
reasonable approximation, but it is not identical to a company's actual
reported annual numbers, and the two should not be treated as
interchangeable in downstream analysis.

**SQLite, not built for concurrent access.** Fine for a single-user local
tool; would need migrating to Postgres before any multi-user or concurrent
-write scenario (e.g., a FastAPI server handling multiple simultaneous
requests that also write data).

**No sector-relative or historical-percentile context yet.** The
`fundamentals` table stores point-in-time snapshots. There is no logic yet
comparing a stock's current P/E against its own 5-year history or its
sector peers — that's Phase 2 (fundamentals engine), not Phase 1.

**Test coverage is for ingestion logic, not data quality.** All tests mock
network calls and verify code *behavior* (idempotency, failure isolation,
unit conversion). None of them verify that a given real API response is
*correct* — that kind of check can only happen by manual
cross-referencing, which is how the currency bug, the label-collision bug,
and the Infosys/TTM-fallback behavior were all actually discovered.

---

## What's next (Phase 2)

The fundamentals engine: turning the raw data in this database into an
actual scorecard — margin trends, revenue/profit CAGR, valuation-in-context
(current P/E vs. the stock's own history and sector peers), and a basic
DCF. Pure calculation, no ML, no LLM — that comes in later phases.