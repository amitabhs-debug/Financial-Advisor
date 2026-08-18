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

Paste this section into README.md, after your existing Phase 1 documentation.
This replaces/consolidates the earlier README_ADDITION_phase2.txt and
README_ADDITION_fx_fallback.txt drafts — use this version, it's the
complete, current state of Phase 2.

===========================================================================
## Phase 2: Fundamentals Engine
===========================================================================

Computes derived analysis metrics from the raw data ingested in Phase 1 —
margins, growth rates, valuation-in-context, ROE/debt health checks, and a
basic DCF — and persists them to a new `computed_fundamentals` table. This
is a decision-support layer, not a return predictor: every output is meant
to inform judgment, not replace it.

### What it computes

- **Margins**: net margin (revenue vs. net profit) per annual period, plus
  a trend classification ("improving" / "declining" / "flat"). Gross and
  operating margin are always `None` — see limitations below.
- **CAGR**: revenue and net profit compound annual growth, over 3-year and
  5-year windows where enough history exists.
- **Valuation-in-context**: current P/E placed against the security's own
  historical P/E range (percentile rank) — not compared to sector peers,
  since no peer/sector-index data is ingested.
- **ROE / Debt-to-Equity health checks**: ROE >= 15% and D/E <= 0.5,
  evaluated from data already ingested in Phase 1 (`Fundamentals.roe`,
  `Fundamentals.debt_to_equity`) but never checked against a threshold
  until now.
- **Balance sheet health**: debt-to-assets ratio
  (`total_liabilities / total_assets`, from data already in
  `financial_statements`) plus its multi-year trend direction.
- **DCF**: a 2-stage discounted free-cash-flow model. The growth
  assumption for the explicit projection period is derived from the
  security's own 3-year revenue CAGR (clamped to -10%/+30% to avoid an
  outlier year distorting the projection), rather than a flat rate
  applied to every stock. Every DCF output carries its assumptions
  alongside it — treat it as a sensitivity exercise, not a target price.

### Architecture

- `src/analysis/metrics.py` — pure calculation functions, no DB
  dependency, fully unit-testable in isolation (`tests/test_metrics.py`).
- `src/analysis/currency.py` — USD→INR normalization for financial
  statements, isolated so a bad FX fetch can't silently corrupt a
  calculation without being flagged.
- `src/analysis/compute_fundamentals.py` — orchestrator, mirrors
  `run_ingestion.py`'s shape (per-symbol failure isolation,
  watchlist-driven).
- `src/analysis/run_fundamentals.py` — CLI entry point.
- `src/analysis/check_fundamentals.py` — quick per-security viewer,
  mirrors `check_db.py`'s style, for spot-checking output after a run.
- `computed_fundamentals` is **append-only**, like `price_history` —
  every run inserts new rows rather than overwriting, so you can see
  how computed metrics evolved over time. Running the engine multiple
  times in one day produces multiple rows sharing the same `as_of_date`;
  use `computed_at` (a real timestamp) to find the true latest row —
  `check_fundamentals.py` orders by `(as_of_date, computed_at)` for
  exactly this reason.

### Bugs found and fixed during Phase 2

Consistent with Phase 1's track record — each of these was caught by
spot-checking output against known real-world numbers, not by the code
raising an error:

1. **Infosys DCF was off by ~83x due to a currency unit bug.** INFY's
   `financial_statements` rows are tagged `currency == "USD"` (a known
   yfinance quirk for this ticker, already documented in Phase 1's
   limitations), but nothing downstream ever checked that tag — revenue,
   net profit, and free cash flow were read as raw numbers and implicitly
   treated as INR. This produced a DCF of ~Rs 15/share for a stock that
   trades around Rs 1,400-1,900. Fixed in `currency.py`: every annual
   statement is normalized to INR (based on its stored `currency` field)
   before any calculation touches it.
2. **DCF used the same flat 10% growth assumption for every stock**,
   regardless of the company's actual growth profile. Fixed by deriving
   the growth assumption from each security's own 3yr revenue CAGR
   (clamped to a sane range) — HDFCBANK/ICICIBANK (faster growers) saw
   their DCF valuations rise; RELIANCE/TCS/INFY (slower growers) saw
   theirs fall, matching their real growth trajectories.
3. **Debt-to-Equity was off by 100x due to a unit mismatch.** yfinance's
   `debtToEquity` field is percentage-scaled (e.g. `36.65` meaning
   36.65%), unlike `returnOnEquity`/`returnOnAssets`, which come back as
   plain decimal fractions. Left uncorrected, RELIANCE/TCS/INFY all
   showed implausible D/E ratios above 9x and failed the 0.5x health
   threshold — despite TCS and Infosys being famously low-debt
   companies. Fixed via `normalize_debt_to_equity_from_yfinance()`
   (divides by 100), flipping all three to a correct PASS.

### Known limitations (Phase 2)

- **Gross and operating margin are always unavailable.**
  `financial_statements` only stores revenue, net profit, total
  assets/liabilities, and cash flow — no COGS or a distinct
  operating-income line. Only net margin is computable from this schema
  today. Flagged as `gross_operating_margin_unavailable_no_cogs_data`.
- **FX conversion uses today's rate, not the historical rate for that
  fiscal period.** A FY2023 USD figure gets converted at whatever the
  USD/INR rate is at computation time. This corrects the "wrong currency
  entirely" class of error but introduces smaller, ongoing imprecision
  from rate drift. Flagged as
  `fx_rate_is_current_not_historical_approximation`.
- **USD/INR conversion has a hardcoded fallback rate.** If the live
  yfinance FX fetch fails, `currency.py` falls back to a static
  approximate rate (`FALLBACK_USD_INR_RATE` in `src/analysis/currency.py`,
  currently 94, last updated August 2026) rather than crashing or
  silently treating USD as INR. This fallback drifts out of date over
  time and needs manual updating — it is not self-refreshing. Flagged as
  `fx_rate_fallback_used_live_fetch_failed` when it fires. A planned
  improvement is to cache the last successfully-fetched live rate and
  fall back to that instead of a static constant — not yet implemented.
- **DCF per-share values depend on `shares_outstanding`**, sourced
  directly from yfinance's `sharesOutstanding` field (added to
  `Fundamentals` in Phase 2). If missing for a security, DCF still
  computes a total intrinsic value but skips the per-share figure.
- **DCF is highly sensitive to its assumptions.** The growth rate is now
  company-specific (see above), but `discount_rate` (12%) and
  `terminal_growth_rate` (4%) are still flat defaults applied to every
  stock, not adjusted for company-specific risk (e.g. a bank's actual
  cost of capital differs meaningfully from an IT company's). All
  assumptions are shown alongside every DCF result via
  `dcf_assumptions_json` — treat as a rough sensitivity exercise, never
  a target price.
- **ROE/D/E thresholds are generic, not sector-adjusted**, except for
  the one case explicitly handled: D/E is flagged
  `debt_to_equity_threshold_not_meaningful_for_banks` for HDFC Bank and
  ICICI Bank, since bank leverage is structurally different (deposits
  are liabilities by nature of the business) and a "healthy" D/E for a
  bank looks nothing like 0.5. ROE remains evaluated normally for banks,
  since — unlike ROCE — it's still a meaningful metric for financial
  companies.
- **Debt-to-Equity and Debt-to-Assets measure different things and can
  look contradictory side by side.** D/E (from yfinance) captures only
  interest-bearing debt. Debt-to-assets (computed here from
  `total_liabilities / total_assets`) captures *all* liabilities —
  accounts payable, deferred tax, lease obligations, etc. A company can
  be genuinely low-debt in the narrow D/E sense while still showing a
  high debt-to-assets ratio (e.g. TCS: D/E 0.10x, debt-to-assets 40%).
  This is not a bug — the two ratios have different denominators by
  design.
- **Bank net margin shares the same caveat as ROCE/EBITDA**, even though
  it isn't currently flagged separately: "revenue" means something
  different for a bank (interest/fee income) than for a non-financial
  company, so net margin comparisons between banks and non-banks (or
  even bank-to-bank without matching business mix) should be treated
  with caution.
- **`ttm_fallback_used` is a static, unconditional flag for Infosys**,
  inherited from the Phase 1 Screener-ingestion limitation — it always
  fires for INFY.NS regardless of what happens in a given run, and is
  unrelated to the currency conversion fix described above. Two separate
  Infosys caveats can appear on the same row; don't conflate them.
- **No sector/peer comparison.** Valuation-in-context is relative to a
  security's own historical P/E only.
- **Data gaps happen even for well-known large-caps.** RELIANCE.NS
  currently shows `roe_data_unavailable` — yfinance simply didn't return
  a `returnOnEquity` value for this ticker at ingestion time. Not a
  code bug, but a reminder that "large, well-covered company" doesn't
  guarantee complete data from any single source.


## Phase 3 — LLM/RAG Layer (Complete)

Adds qualitative, narrative-grounded analysis on top of Phase 1's structured
data and Phase 2's computed fundamentals: retrieval-augmented generation
(RAG) over quarterly filings and annual reports, plus live news sentiment
analysis.

**Stack:** Claude API (generation + sentiment reasoning) +
`sentence-transformers` local embeddings + Chroma (vector store) +
Marketaux (news) — chosen to keep high-volume, low-value-per-call work
(embeddings, article fetching) local/cheap, and reserve paid API calls for
steps where reasoning quality matters most (filing analysis, sentiment
interpretation).

### 3a — Filings RAG

**Pipeline:** manual filing acquisition (`register_manual_filings.py`) →
chunking (fixed 500-token windows, 75-token overlap, `chunk_and_embed.py`)
→ local embedding → Chroma storage (tagged by symbol + doc_type) →
semantic retrieval (`retrieve.py`, filterable by symbol and
quarterly/annual) → Claude generation with mandatory source attribution
(`generate_answer.py` / `rag_query.py`) — every claim traceable to a
specific excerpt/chunk.

Validated end-to-end across all 5 watchlist stocks, both quarterly filings
and annual reports.

#### Why narrative analysis matters alongside the numbers: a real example

While validating this layer against TCS's FY2026 annual report, the RAG
pipeline surfaced this passage:

> "In FY 2026, TCS achieved a year-over-year revenue growth of 4.6%... On a
> constant currency basis, revenue declined by 2.4%. The decline was largely
> a result of one of the Company's large transformation programmes in India
> coming to an end this year."

This is a concrete illustration of why this layer exists. Phase 2's
fundamentals engine computes growth rates from structured numeric fields —
it would report the 4.6% figure accurately, but has no way to know that
figure is partly a currency-translation effect (INR revenue inflated by
rupee depreciation against USD/EUR/GBP, since TCS earns most revenue
abroad but reports in INR) masking an underlying **decline** in real
business volume. Only the filing's own prose discloses that distinction.
A decision-support tool relying on structured numbers alone would present
"4.6% growth" as an unambiguous positive; the RAG layer surfaces the more
accurate — and less flattering — underlying picture.

### 3b — News & Sentiment

**Pipeline:** batched news fetch across the full watchlist in a single
request (`fetch_news.py`) → idempotent storage (`news_log.py`) → Claude
sentiment classification with reasoning, not just a label
(`analyze_sentiment.py`) → per-symbol report (`news_report.py`).

**Source:** Marketaux (free tier, ~100 requests/day). Each article carries
Marketaux's own numeric sentiment score (-1 to 1) per matched entity,
stored alongside Claude's label/reasoning for comparison.

**Sentiment approach:** Claude, not a dedicated classifier (e.g. FinBERT).
At this project's volume (a handful of headlines per stock, periodically,
not high-frequency), Claude's reasoning is more valuable than a
classifier's speed/cost advantage — a headline like "wins large deal but
stock falls on margin concerns" gets a genuine "mixed" read with
explanation, not a forced single label. This also reuses the same
grounded-prompt architecture already validated in the filings RAG layer
rather than introducing a second ML framework for one task.

#### A real symbol-resolution bug, caught and fixed

Initial testing surfaced a genuine data-integrity issue, not just a missing
field: querying Marketaux with bare NSE symbols (`TCS`, `INFY`) returned
**wrong-company matches** — `TCS` resolved to an unrelated US retailer
(The Container Store Group, also ticker `TCS` on a US exchange), and
`INFY` only coincidentally matched the right company via its separate NYSE
ADR listing. Diagnosed via Marketaux's `/entity/search` endpoint
(`fetch_news.py --search "<company name>"`), which revealed Marketaux
indexes NSE equities using the same `.NS` suffix convention as yfinance
(`RELIANCE.NS`, `HDFCBANK.NS`, etc.) — with one irregular exception:
correctly-suffixed `TCS.NS` still collided, and the real identifier is
`TCS-BL.NS`. Fixed with an explicit `MARKETAUX_SYMBOL_OVERRIDES` map
(rather than assuming a uniform pattern) plus a defensive
`expected_symbols` filter that drops any entity outside the watchlist,
even if the query-level filter is ever imperfect.

This is the same class of lesson as Phase 1's currency-normalization and
field-scaling bugs: **never trust an external API's symbol/field
conventions without verifying against a live response first.**

### Known limitations (flagged explicitly, not hidden)

- **NSE filing scraping automation is parked.** `fetch_filings.py` exists
  but currently fails NSE's anti-bot checks (403). Filings are ingested
  manually for now (`register_manual_filings.py`) — pending revisit with a
  maintained scraper library (`nsepython`/`jugaad-data`) if automation is
  needed later.
- **PDF table extraction is degraded.** `pypdf` flattens tables into linear
  text with no row/column structure — fine for narrative sections (MD&A,
  earnings call transcripts), poor for numeric tables. Retrieval for
  narrative questions works well; "what was the exact figure" questions
  are better served by Phase 2's structured data.
- **Source citations are chunk-level, not page-level.** Verifying a
  generated claim against the source PDF currently requires searching for
  a distinctive phrase (Ctrl+F) rather than jumping to an exact page.
- **News volume is naturally sparse per run.** A single fetch may return
  zero articles for some watchlist stocks simply because no matching news
  existed in that window — not a pipeline failure. History builds up with
  repeated runs over time.
- **Only 5 watchlist symbols' Marketaux identifiers have been verified.**
  If the watchlist grows, any new symbol should be diagnosed the same way
  (`--search`) before assuming the standard `.NS` pattern holds.