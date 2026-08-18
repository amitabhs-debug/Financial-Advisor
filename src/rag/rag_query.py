"""
Phase 3 (LLM/RAG layer) -- CLI entrypoint.

Usage:
    python -m src.rag.rag_query "What were the key revenue drivers this quarter?" --symbol RELIANCE
    python -m src.rag.rag_query "What are the company's stated long-term risks?" --symbol TCS --doc-type annual
    python -m src.rag.rag_query "How did margins trend across the watchlist?"

If --symbol is omitted, retrieval searches across all companies in the
Chroma collection -- useful for genuinely cross-stock questions, but
be aware answers may blend excerpts from multiple companies unless the
question is specific. For single-company questions, always pass
--symbol to avoid cross-contamination between similarly-worded filings
(see the symbol_filter note in retrieve.py).

If --doc-type is omitted, retrieval searches across both quarterly and
annual material. Pass it when the question specifically wants one
timeframe's content (e.g. "risks" questions are often better answered
from annual reports' dedicated risk-factors sections, while "this
quarter" questions should stay quarterly-only).

Prerequisite pipeline order:
    1. register_manual_filings.py -- register PDFs placed in data/filings/{quarterly,annual}/
    2. chunk_and_embed.py         -- extract, chunk, embed, store in Chroma
    3. rag_query.py                -- this script, ask questions
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.rag.chunk_and_embed import Embedder, get_chroma_collection
from src.rag.retrieve import retrieve, DEFAULT_TOP_K
from src.rag.generate_answer import generate_answer

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a question over ingested filings.")
    parser.add_argument("question", type=str, help="The question to ask.")
    parser.add_argument(
        "--symbol", type=str, default=None,
        help="Restrict retrieval to one NSE symbol (e.g. RELIANCE). Strongly recommended for single-stock questions.",
    )
    parser.add_argument(
        "--doc-type", type=str, default=None, choices=["quarterly", "annual"],
        help="Restrict retrieval to one document type. Omit to search across both.",
    )
    parser.add_argument(
        "--top-k", type=int, default=DEFAULT_TOP_K,
        help=f"Number of chunks to retrieve (default: {DEFAULT_TOP_K}).",
    )
    parser.add_argument(
        "--show-chunks", action="store_true",
        help="Print retrieved chunk text before the answer, for debugging retrieval quality.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    embedder = Embedder()
    collection = get_chroma_collection()

    chunks = retrieve(
        args.question, embedder, collection,
        top_k=args.top_k, symbol_filter=args.symbol, doc_type_filter=args.doc_type,
    )

    if not chunks:
        print(
            "\nNo chunks retrieved. Check: (1) chunk_and_embed.py has been run, "
            "(2) --symbol matches a symbol actually present in the collection "
            "(bare NSE symbol, e.g. RELIANCE, not RELIANCE.NS), "
            "(3) --doc-type (if set) actually has ingested filings of that type.\n"
        )
        sys.exit(1)

    if args.show_chunks:
        print(f"\n--- Retrieved {len(chunks)} chunk(s) ---")
        for i, c in enumerate(chunks, 1):
            print(f"[{i}] {c.symbol} | {c.doc_type} | {c.filing_date} | distance={c.distance:.4f}")
        print()

    result = generate_answer(args.question, chunks)

    print("=" * 70)
    print("ANSWER")
    print("=" * 70)
    print(result.answer)
    print()
    print("=" * 70)
    print("SOURCES")
    print("=" * 70)
    for i, src in enumerate(result.sources, 1):
        print(f"[{i}] {src.symbol} | {src.doc_type} | filed {src.filing_date} | {src.source_path} (chunk {src.chunk_index})")


if __name__ == "__main__":
    main()