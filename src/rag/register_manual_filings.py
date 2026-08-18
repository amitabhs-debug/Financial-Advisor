"""
Phase 3 (LLM/RAG layer) -- Manual filing registration.

NSE's anti-bot posture made automated scraping (fetch_filings.py)
unreliable for now (see fetch_filings.py's docstring for the 403
history). Filings are downloaded manually -- via browser, from NSE's
filing pages or company IR pages -- and dropped into the appropriate
folder below.

This script's only job: scan both folders, and register any PDFs not
already in document_log.py's tracking table. This keeps manual and
automated ingestion consistent -- if fetch_filings.py resumes later,
it won't re-download or conflict with anything registered here, since
both write to the same DocumentLog.

Folder structure (doc_type is inferred from which folder a PDF sits in,
not from its filename -- keeps the naming convention identical for both
types):
    data/filings/quarterly/{SYMBOL}_{date}.pdf
    data/filings/annual/{SYMBOL}_{date}.pdf

SYMBOL must be the bare NSE symbol (RELIANCE, not RELIANCE.NS) --
same convention chunk_and_embed.py expects when it parses filenames.
The {date} portion doesn't need to be a real parseable date -- it's
treated as an opaque label, so a suffix like "2026-07-18_transcript"
is fine and often useful for keeping multiple files per symbol/period
unique (see chunk_and_embed.py's filename docs).

Run this any time after adding new PDFs, before running chunk_and_embed.py:
    python -m src.rag.register_manual_filings
"""

from __future__ import annotations

import re
import logging
from pathlib import Path

from src.rag.document_log import DocumentLog

logger = logging.getLogger(__name__)

FILINGS_ROOT = Path("data/filings")
DOC_TYPE_DIRS = {
    "quarterly": FILINGS_ROOT / "quarterly",
    "annual": FILINGS_ROOT / "annual",
}
FILENAME_PATTERN = re.compile(r"^([A-Z]+)_(.+)\.pdf$")


def register_manual_filings(doc_type_dirs: dict[str, Path] = DOC_TYPE_DIRS) -> int:
    log = DocumentLog()
    registered = 0
    skipped_bad_name = []

    for doc_type, filings_dir in doc_type_dirs.items():
        if not filings_dir.exists():
            logger.info("No %s directory found at %s, skipping (create it and add PDFs if needed).", doc_type, filings_dir)
            continue

        for pdf_path in filings_dir.glob("*.pdf"):
            match = FILENAME_PATTERN.match(pdf_path.name)
            if not match:
                skipped_bad_name.append(pdf_path.name)
                continue

            symbol, filing_date = match.groups()
            # pdf_url is a placeholder ("manual") rather than a real URL --
            # there's no source URL for a manually-downloaded file. Combined
            # with doc_type in the uniqueness key, this means two manually-
            # added PDFs for the same symbol, date, and doc_type would
            # collide -- which is correct (they'd be duplicates of the same
            # filing).
            pdf_url = "manual"

            if log.already_processed(symbol, filing_date, doc_type, pdf_url):
                continue

            log.record(symbol, filing_date, doc_type, pdf_url, str(pdf_path))
            logger.info("Registered [%s]: %s", doc_type, pdf_path.name)
            registered += 1

    log.close()

    if skipped_bad_name:
        print(
            f"\nSkipped {len(skipped_bad_name)} file(s) with names that don't match "
            f"the required SYMBOL_date.pdf pattern:"
        )
        for name in skipped_bad_name:
            print(f"  - {name}")
        print("Rename these to match, e.g. RELIANCE_2026-07-15.pdf, then re-run.\n")

    return registered


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    count = register_manual_filings()
    print(f"Registered {count} new filing(s). Ready for: python -m src.rag.chunk_and_embed")