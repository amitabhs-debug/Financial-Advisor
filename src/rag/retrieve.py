"""
Phase 3 (LLM/RAG layer) -- Retrieval.

Embeds a user's question with the same local model used for chunking
(embedding query and documents with mismatched models is a common RAG
bug -- their vector spaces aren't comparable, so distances become
meaningless), then pulls the top-N nearest chunks from Chroma.

Kept as pure retrieval with no generation logic, so it can be tested
and inspected independently of the Claude API call -- you can sanity-
check *what* would be retrieved for a question without spending any
API budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.rag.chunk_and_embed import Embedder, get_chroma_collection

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5


@dataclass
class RetrievedChunk:
    text: str
    symbol: str
    filing_date: str
    doc_type: str
    source_path: str
    chunk_index: int
    distance: float  # lower = more similar. Chroma default is L2 distance, not a 0-1 similarity score.


def retrieve(
    question: str,
    embedder: Embedder,
    collection,
    top_k: int = DEFAULT_TOP_K,
    symbol_filter: str | None = None,
    doc_type_filter: str | None = None,
) -> list[RetrievedChunk]:
    """
    Retrieve the top_k most relevant chunks for a question.

    symbol_filter: if given (e.g. "RELIANCE"), restricts retrieval to
    chunks from that stock only. Important once the collection holds
    multiple companies' filings -- without this, a question about
    Reliance could retrieve a superficially-similar-sounding chunk
    from TCS's filing, which would silently produce a wrong-company
    answer. Always pass this when the question is about one specific
    stock; leave it unset only for genuinely cross-stock questions.

    doc_type_filter: if given ("quarterly" or "annual"), restricts to
    that document type only. Leave unset to search across both --
    useful for questions that don't care which period the commentary
    came from, but be aware mixed results can pull recent-quarter and
    year-old-annual-report commentary into the same answer without
    the answer necessarily distinguishing them, unless the question
    or the generation prompt make timeframe explicit.
    """
    query_embedding = embedder.embed([question])[0]

    conditions = []
    if symbol_filter:
        conditions.append({"symbol": symbol_filter})
    if doc_type_filter:
        conditions.append({"doc_type": doc_type_filter})

    if len(conditions) == 0:
        where = None
    elif len(conditions) == 1:
        where = conditions[0]
    else:
        where = {"$and": conditions}

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where,
    )

    chunks: list[RetrievedChunk] = []
    if not results["ids"] or not results["ids"][0]:
        logger.warning(
            "No chunks retrieved for question (symbol_filter=%s, doc_type_filter=%s). "
            "Collection may be empty, or filters matched nothing -- "
            "check chunk_and_embed.py has been run for this symbol/doc_type.",
            symbol_filter, doc_type_filter,
        )
        return chunks

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for doc, meta, dist in zip(documents, metadatas, distances):
        chunks.append(
            RetrievedChunk(
                text=doc,
                symbol=meta["symbol"],
                filing_date=meta["filing_date"],
                doc_type=meta.get("doc_type", "quarterly"),  # default for chunks embedded before doc_type existed
                source_path=meta["source_path"],
                chunk_index=meta["chunk_index"],
                distance=dist,
            )
        )

    return chunks


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    question = sys.argv[1] if len(sys.argv) > 1 else "What were the key financial highlights?"
    symbol = sys.argv[2] if len(sys.argv) > 2 else None
    doc_type = sys.argv[3] if len(sys.argv) > 3 else None

    embedder = Embedder()
    collection = get_chroma_collection()

    results = retrieve(question, embedder, collection, symbol_filter=symbol, doc_type_filter=doc_type)

    print(f"\nTop {len(results)} chunks for: {question!r}\n")
    for i, chunk in enumerate(results, 1):
        print(f"--- [{i}] {chunk.symbol} | {chunk.doc_type} | {chunk.filing_date} | distance={chunk.distance:.4f} ---")
        print(chunk.text[:300].replace("\n", " ") + "...")
        print()