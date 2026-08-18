"""
Phase 3 (LLM/RAG layer) -- Chunking + embedding.

Takes downloaded filing PDFs (quarterly and/or annual), extracts text,
splits into fixed-size overlapping chunks, embeds them locally with
sentence-transformers, and stores the vectors in a single local Chroma
collection for retrieval, tagged with doc_type so quarterly and annual
material can be filtered independently or searched together.

Chunking strategy: fixed token size with overlap, not section-aware.
Simpler and more robust than parsing PDF headers (which vary in
formatting company to company -- the same "don't trust PDF structure"
lesson as Phase 1's Screener Excel row-collision bug). Overlap exists
so a fact split across a chunk boundary (e.g. a number in one chunk,
its label in the next) still has a decent chance of being retrieved
whole in at least one chunk.

Token counting uses tiktoken as an approximation -- it's not Claude's
actual tokenizer, but it's close enough for sizing chunks consistently,
and avoids adding a heavier dependency for something that only needs
to be roughly right.

NOTE on annual reports: these run 100+ pages vs. quarterly docs' few
pages, so expect proportionally more chunks per file (hundreds, not
tens) and a longer embedding run the first time you process a batch
of them. Nothing about the pipeline changes for this -- it's just
slower, not different.
"""

from __future__ import annotations

import re
import logging
import hashlib
from pathlib import Path
from dataclasses import dataclass

import tiktoken
import chromadb
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

CHUNK_SIZE_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 75  # ~15% overlap -- enough to catch boundary-split facts without much redundant storage

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"  # small, fast, solid quality for this scale; runs fine on CPU
CHROMA_DIR = Path("data/chroma")
COLLECTION_NAME = "filings"  # renamed from "quarterly_filings" now that this holds both doc types

FILINGS_ROOT = Path("data/filings")
DOC_TYPE_DIRS = {
    "quarterly": FILINGS_ROOT / "quarterly",
    "annual": FILINGS_ROOT / "annual",
}

_tokenizer = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    text: str
    symbol: str
    filing_date: str
    doc_type: str
    source_path: str
    chunk_index: int


def extract_text(pdf_path: Path) -> str:
    """
    Extract raw text from a PDF. No layout/table reconstruction --
    pypdf flattens tables into linear text, which loses row/column
    alignment. For results-heavy filings (mostly numbers in tables),
    this means a chunk might read like a jumble of numbers without
    clear row context. This is a known, accepted limitation for v1:
    it's good enough for narrative sections (MD&A commentary,
    highlights) which is most of what RAG retrieval is useful for
    anyway. If table fidelity turns out to matter, revisit with a
    table-aware extractor (e.g. pdfplumber's table extraction) later
    -- flagging here rather than silently shipping degraded table
    data unflagged.

    Handles encrypted PDFs (common for company-issued investor PDFs --
    usually an owner/permissions lock, not a real reader password).
    Attempts decryption with an empty password, which resolves most
    such files. If a PDF genuinely can't be read (real password, or
    the `cryptography` package isn't installed for AES-encrypted
    files), this returns an empty string and logs why, rather than
    raising and crashing the whole batch -- one bad file in a
    multi-file run should not lose progress already made on the
    others. Requires `pip install cryptography` for AES-encrypted
    PDFs; a missing-dependency failure here is reported the same way
    as a real bad-password failure so the log always tells you which
    file to look at, even if the underlying cause differs.
    """
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        logger.error("Could not open %s (unrelated to encryption): %s", pdf_path.name, exc)
        return ""

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            logger.error(
                "Could not decrypt %s -- if this is a DependencyError, run "
                "`pip install cryptography` and retry; if it persists, this "
                "PDF likely needs a real password and must be handled "
                "manually (e.g. re-saved without encryption). Skipping for "
                "now: %s", pdf_path.name, exc,
            )
            return ""

    pages_text = []
    for page_num, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("Failed to extract text from page %d of %s: %s", page_num, pdf_path.name, exc)
            text = ""
        pages_text.append(text)
    return "\n".join(pages_text)


def chunk_text(text: str, symbol: str, filing_date: str, doc_type: str, source_path: str) -> list[Chunk]:
    """
    Fixed-size token chunking with overlap. Operates on whitespace-
    joined text, not sentence boundaries -- simpler and avoids
    depending on sentence-splitting behaving well on table-flattened
    PDF text (which often lacks normal punctuation/sentence structure).
    """
    tokens = _tokenizer.encode(text)
    if not tokens:
        return []

    chunks: list[Chunk] = []
    start = 0
    chunk_index = 0
    step = CHUNK_SIZE_TOKENS - CHUNK_OVERLAP_TOKENS

    while start < len(tokens):
        end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_str = _tokenizer.decode(chunk_tokens)

        if chunk_str.strip():
            chunks.append(
                Chunk(
                    text=chunk_str,
                    symbol=symbol,
                    filing_date=filing_date,
                    doc_type=doc_type,
                    source_path=source_path,
                    chunk_index=chunk_index,
                )
            )
            chunk_index += 1

        if end == len(tokens):
            break
        start += step

    return chunks


def chunk_id(chunk: Chunk) -> str:
    """
    Deterministic ID so re-embedding the same PDF doesn't create
    duplicate vectors in Chroma -- same idempotency principle as the
    rest of the project. Chroma's `upsert` will overwrite on matching
    ID, so re-running this script on an already-embedded PDF is safe.
    doc_type is included so a quarterly and annual chunk that happened
    to share every other field would never collide.
    """
    raw = f"{chunk.symbol}|{chunk.filing_date}|{chunk.doc_type}|{chunk.source_path}|{chunk.chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class Embedder:
    """Thin wrapper so the model loads once and is reused across calls."""

    def __init__(self, model_name: str = EMBEDDING_MODEL_NAME):
        logger.info("Loading embedding model: %s", model_name)
        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, show_progress_bar=False).tolist()


def embed_and_store(chunks: list[Chunk], embedder: Embedder, collection) -> int:
    """Embeds chunks and upserts them into the Chroma collection. Returns count stored."""
    if not chunks:
        return 0

    texts = [c.text for c in chunks]
    embeddings = embedder.embed(texts)
    ids = [chunk_id(c) for c in chunks]
    metadatas = [
        {
            "symbol": c.symbol,
            "filing_date": c.filing_date,
            "doc_type": c.doc_type,
            "source_path": c.source_path,
            "chunk_index": c.chunk_index,
        }
        for c in chunks
    ]

    collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
    return len(chunks)


def get_chroma_collection():
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(name=COLLECTION_NAME)


def process_pdf(pdf_path: Path, symbol: str, filing_date: str, doc_type: str, embedder: Embedder, collection) -> int:
    """End-to-end: one PDF -> extracted text -> chunks -> embedded -> stored. Returns chunks stored."""
    text = extract_text(pdf_path)
    if not text.strip():
        logger.warning("No extractable text in %s -- likely a scanned/image PDF, skipping", pdf_path.name)
        return 0

    chunks = chunk_text(text, symbol=symbol, filing_date=filing_date, doc_type=doc_type, source_path=str(pdf_path))
    stored = embed_and_store(chunks, embedder, collection)
    logger.info("Stored %d chunk(s) from %s [%s]", stored, pdf_path.name, doc_type)
    return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    any_dir_found = any(d.exists() for d in DOC_TYPE_DIRS.values())
    if not any_dir_found:
        print(f"No filings directories found under {FILINGS_ROOT} -- add PDFs to quarterly/ and/or annual/ first.")
        raise SystemExit(1)

    embedder = Embedder()
    collection = get_chroma_collection()

    total_chunks = 0
    failed_files = []
    for doc_type, filings_dir in DOC_TYPE_DIRS.items():
        if not filings_dir.exists():
            logger.info("No %s directory found at %s, skipping.", doc_type, filings_dir)
            continue

        for pdf_path in filings_dir.glob("*.pdf"):
            # filenames are "{SYMBOL}_{filing_date}.pdf" -- doc_type comes
            # from which folder the file is in, not parsed from the name.
            match = re.match(r"^([A-Z]+)_(.+)\.pdf$", pdf_path.name)
            if not match:
                logger.warning("Skipping file with unexpected name pattern: %s", pdf_path.name)
                continue
            symbol, filing_date = match.groups()
            try:
                total_chunks += process_pdf(pdf_path, symbol, filing_date, doc_type, embedder, collection)
            except Exception as exc:
                # One bad file (corrupt PDF, unexpected structure, etc.)
                # should not lose progress already made on the rest of the
                # batch -- log it, keep going, and report it in the summary.
                logger.error("Failed to process %s, skipping: %s", pdf_path.name, exc)
                failed_files.append(pdf_path.name)

    print(f"Done. {total_chunks} chunk(s) embedded and stored in Chroma collection '{COLLECTION_NAME}'.")
    if failed_files:
        print(f"\n{len(failed_files)} file(s) failed and were skipped:")
        for name in failed_files:
            print(f"  - {name}")
        print("Check the logs above for the reason for each, then re-run this script after fixing.")