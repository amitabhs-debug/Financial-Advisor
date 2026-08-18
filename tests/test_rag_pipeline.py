"""
Tests for the Phase 3 RAG layer.

Philosophy matches Phase 1's 13 tests: focus on regression prevention
for the failure modes that are actually likely here -- chunking
correctness, idempotency (re-running shouldn't duplicate data),
doc_type isolation (quarterly vs. annual must not collide), and the
"don't silently answer with nothing" fallback in generation -- rather
than exhaustive coverage of every function.

No live network, DB, or Claude API calls are made in this suite --
everything here is testable offline, same principle as metrics.py
being pure functions in Phase 2. NSE scraping, Chroma storage, and
Claude generation all depend on external state and are meant to be
smoke-tested manually (via each script's __main__ block) rather than
unit tested here.
"""

from __future__ import annotations

import pytest

from src.rag.chunk_and_embed import chunk_text, chunk_id, Chunk
from src.rag.document_log import DocumentLog
from src.rag.retrieve import RetrievedChunk
from src.rag.generate_answer import generate_answer, _format_context


# ---------- chunk_text ----------

def test_chunk_text_empty_string_returns_no_chunks():
    chunks = chunk_text("", symbol="RELIANCE", filing_date="2026-07-01", doc_type="quarterly", source_path="x.pdf")
    assert chunks == []


def test_chunk_text_short_text_produces_single_chunk():
    text = "Revenue grew ten percent this quarter on strong export demand."
    chunks = chunk_text(text, symbol="TCS", filing_date="2026-07-01", doc_type="quarterly", source_path="x.pdf")
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].symbol == "TCS"
    assert chunks[0].doc_type == "quarterly"


def test_chunk_text_long_text_produces_multiple_overlapping_chunks():
    # ~2000 words, well beyond the 500-token chunk size, so this should
    # split into several chunks with sequential indices.
    text = "Segment results improved year over year. " * 400
    chunks = chunk_text(text, symbol="RELIANCE", filing_date="2026-07-01", doc_type="annual", source_path="x.pdf")

    assert len(chunks) > 1
    # indices should be sequential starting at 0, no gaps
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.doc_type == "annual" for c in chunks)


def test_chunk_text_chunks_are_nonempty():
    text = "Net profit margin expanded due to lower input costs. " * 200
    chunks = chunk_text(text, symbol="INFY", filing_date="2026-07-01", doc_type="quarterly", source_path="x.pdf")
    assert all(c.text.strip() for c in chunks)


# ---------- chunk_id (idempotency) ----------

def test_chunk_id_deterministic_for_same_input():
    c1 = Chunk(text="abc", symbol="TCS", filing_date="2026-07-01", doc_type="quarterly", source_path="a.pdf", chunk_index=0)
    c2 = Chunk(text="abc", symbol="TCS", filing_date="2026-07-01", doc_type="quarterly", source_path="a.pdf", chunk_index=0)
    assert chunk_id(c1) == chunk_id(c2)


def test_chunk_id_differs_when_chunk_index_differs():
    # regression guard: this is what makes re-embedding upsert cleanly
    # instead of colliding chunk 0 and chunk 1 of the same filing into
    # the same Chroma ID.
    c1 = Chunk(text="abc", symbol="TCS", filing_date="2026-07-01", doc_type="quarterly", source_path="a.pdf", chunk_index=0)
    c2 = Chunk(text="abc", symbol="TCS", filing_date="2026-07-01", doc_type="quarterly", source_path="a.pdf", chunk_index=1)
    assert chunk_id(c1) != chunk_id(c2)


def test_chunk_id_differs_across_symbols():
    # regression guard against cross-company ID collisions
    c1 = Chunk(text="abc", symbol="TCS", filing_date="2026-07-01", doc_type="quarterly", source_path="a.pdf", chunk_index=0)
    c2 = Chunk(text="abc", symbol="INFY", filing_date="2026-07-01", doc_type="quarterly", source_path="a.pdf", chunk_index=0)
    assert chunk_id(c1) != chunk_id(c2)


def test_chunk_id_differs_across_doc_types():
    # regression guard: a quarterly and annual chunk that otherwise
    # share every field must not collide into the same Chroma ID.
    c1 = Chunk(text="abc", symbol="TCS", filing_date="2026-07-01", doc_type="quarterly", source_path="a.pdf", chunk_index=0)
    c2 = Chunk(text="abc", symbol="TCS", filing_date="2026-07-01", doc_type="annual", source_path="a.pdf", chunk_index=0)
    assert chunk_id(c1) != chunk_id(c2)


# ---------- DocumentLog (idempotency) ----------

def test_document_log_marks_new_filing_as_not_processed(tmp_path):
    log = DocumentLog(db_path=tmp_path / "test_log.db")
    assert log.already_processed("RELIANCE", "2026-07-01", "quarterly", "http://x/a.pdf") is False
    log.close()


def test_document_log_marks_recorded_filing_as_processed(tmp_path):
    log = DocumentLog(db_path=tmp_path / "test_log.db")
    log.record("RELIANCE", "2026-07-01", "quarterly", "http://x/a.pdf", "/local/a.pdf")
    assert log.already_processed("RELIANCE", "2026-07-01", "quarterly", "http://x/a.pdf") is True
    log.close()


def test_document_log_double_record_does_not_error(tmp_path):
    # this is the actual idempotency guarantee: re-running the
    # registration/fetch script on the same filing must not crash or
    # duplicate rows.
    log = DocumentLog(db_path=tmp_path / "test_log.db")
    log.record("TCS", "2026-07-01", "quarterly", "http://x/b.pdf", "/local/b.pdf")
    log.record("TCS", "2026-07-01", "quarterly", "http://x/b.pdf", "/local/b.pdf")  # duplicate call

    cur = log._conn.execute("SELECT COUNT(*) FROM fetched_filings")
    count = cur.fetchone()[0]
    assert count == 1
    log.close()


def test_document_log_distinguishes_different_filings_same_symbol(tmp_path):
    log = DocumentLog(db_path=tmp_path / "test_log.db")
    log.record("HDFCBANK", "2026-04-01", "quarterly", "http://x/q1.pdf", "/local/q1.pdf")
    log.record("HDFCBANK", "2026-07-01", "quarterly", "http://x/q2.pdf", "/local/q2.pdf")

    assert log.already_processed("HDFCBANK", "2026-04-01", "quarterly", "http://x/q1.pdf") is True
    assert log.already_processed("HDFCBANK", "2026-07-01", "quarterly", "http://x/q2.pdf") is True

    cur = log._conn.execute("SELECT COUNT(*) FROM fetched_filings")
    assert cur.fetchone()[0] == 2
    log.close()


def test_document_log_distinguishes_quarterly_from_annual_same_date(tmp_path):
    # regression guard: a quarterly and annual filing that happen to
    # share the same date-label string must both be kept, not treated
    # as a duplicate of each other.
    log = DocumentLog(db_path=tmp_path / "test_log.db")
    log.record("ICICIBANK", "2026-07-18", "quarterly", "manual", "/local/q.pdf")
    log.record("ICICIBANK", "2026-07-18", "annual", "manual", "/local/a.pdf")

    assert log.already_processed("ICICIBANK", "2026-07-18", "quarterly", "manual") is True
    assert log.already_processed("ICICIBANK", "2026-07-18", "annual", "manual") is True

    cur = log._conn.execute("SELECT COUNT(*) FROM fetched_filings")
    assert cur.fetchone()[0] == 2
    log.close()


# ---------- generate_answer / _format_context ----------

def _sample_chunk(symbol="RELIANCE", idx=0, doc_type="quarterly", text="Revenue grew 10% YoY."):
    return RetrievedChunk(
        text=text, symbol=symbol, filing_date="2026-07-01", doc_type=doc_type,
        source_path="x.pdf", chunk_index=idx, distance=0.1,
    )


def test_format_context_includes_source_labels():
    chunks = [_sample_chunk(idx=0), _sample_chunk(idx=1, text="Margins expanded slightly.")]
    context = _format_context(chunks)
    assert "[Excerpt 1]" in context
    assert "[Excerpt 2]" in context
    assert "RELIANCE" in context


def test_format_context_includes_doc_type():
    # doc_type must be visible in the prompt context so Claude can
    # distinguish "this quarter" commentary from older annual-report
    # commentary when both are retrieved together.
    chunks = [_sample_chunk(doc_type="annual")]
    context = _format_context(chunks)
    assert "annual" in context


def test_generate_answer_with_no_chunks_returns_explicit_fallback_without_api_call():
    # this must NOT attempt an API call -- no ANTHROPIC_API_KEY needed
    # for this path, and it must not raise even if the key is unset.
    result = generate_answer("What was revenue?", chunks=[])
    assert result.sources == []
    assert "No relevant filing excerpts" in result.answer


def test_generate_answer_raises_clear_error_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    chunks = [_sample_chunk()]
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        generate_answer("What was revenue?", chunks=chunks)