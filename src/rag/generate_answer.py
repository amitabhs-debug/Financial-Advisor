"""
Phase 3 (LLM/RAG layer) -- Generation.

Takes the question + retrieved chunks and calls the Claude API to
produce a grounded answer. "Grounded" here means two concrete things,
not just a vibe:

1. The prompt explicitly instructs Claude to only use the provided
   chunks and to say so plainly when the chunks don't contain the
   answer, rather than filling gaps from general knowledge. This
   matters a lot for financial claims -- a fluent-sounding but
   ungrounded answer about a company's numbers is worse than no
   answer.
2. Every answer is returned alongside the source chunks it was built
   from (symbol, filing date, chunk index) so you can manually verify
   any specific claim against the original filing. This is the same
   "never hide the failure mode" instinct as the rest of the project's
   explicit limitation-flagging.

Requires ANTHROPIC_API_KEY to be set in your environment. Never hard-
code the key in this file.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass

import anthropic
from dotenv import load_dotenv

from src.rag.retrieve import RetrievedChunk

logger = logging.getLogger(__name__)

# Loads ANTHROPIC_API_KEY (and any other vars) from a .env file in the
# project root into os.environ. Without this call, a .env file sitting
# on disk does nothing -- nothing reads it automatically. load_dotenv()
# is safe to call even if .env doesn't exist or the var is already set
# via the shell (e.g. $env:ANTHROPIC_API_KEY) -- it won't override an
# already-set environment variable by default, so an explicit shell
# override still takes precedence if you ever need to test a different
# key temporarily.
load_dotenv()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """You are a financial research assistant answering questions about \
Indian-listed companies using excerpts from their quarterly and/or annual filings.

Rules you must follow:
- Only use information present in the provided excerpts. Do not use outside \
knowledge about the company, even if you believe it to be true.
- If the excerpts do not contain enough information to answer the question, \
say so explicitly rather than guessing or filling the gap.
- Do not give investment advice, price targets, or buy/sell recommendations. \
This is a decision-support tool, not a return predictor -- describe what the \
filing says, not what the reader should do about it.
- When you state a specific figure or claim, note which excerpt (by number) \
it came from, so the reader can verify it against the source filing.
- If excerpts appear to conflict, point out the conflict rather than silently \
picking one. This is especially important when excerpts come from different \
document types or filing dates (e.g. a quarterly update vs. an annual report \
from a different period) -- note the source period explicitly if it affects \
how the reader should interpret the answer, since older annual-report \
commentary may no longer reflect the company's current position.
"""


@dataclass
class GeneratedAnswer:
    answer: str
    sources: list[RetrievedChunk]


def _format_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(
            f"[Excerpt {i}] Source: {chunk.symbol} {chunk.doc_type} filing dated "
            f"{chunk.filing_date} (chunk {chunk.chunk_index})\n{chunk.text}"
        )
    return "\n\n".join(parts)


def generate_answer(question: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
    if not chunks:
        return GeneratedAnswer(
            answer=(
                "No relevant filing excerpts were retrieved for this question. "
                "This usually means either the collection doesn't have filings "
                "for this stock yet (run fetch_filings.py + chunk_and_embed.py "
                "first), or the symbol_filter excluded everything -- it does not "
                "mean the answer is 'no'."
            ),
            sources=[],
        )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it in your environment before "
            "calling generate_answer() -- never hardcode it in source."
        )

    client = anthropic.Anthropic()
    context = _format_context(chunks)

    user_message = (
        f"Question: {question}\n\n"
        f"Filing excerpts:\n\n{context}\n\n"
        f"Answer the question using only the excerpts above, per your instructions."
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        logger.error("Claude API call failed: %s", exc)
        raise

    answer_text = "".join(
        block.text for block in response.content if block.type == "text"
    )

    return GeneratedAnswer(answer=answer_text, sources=chunks)