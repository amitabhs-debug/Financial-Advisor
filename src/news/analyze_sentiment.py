"""
Phase 3b (News & sentiment layer) -- Sentiment analysis via Claude.

Takes articles fetched by fetch_news.py (that haven't been analyzed
yet) and asks Claude to classify sentiment AND explain its reasoning
-- not just a bare label, per the project's decision-support
philosophy (explaining "why," not just handing back a signal).

Chosen over a dedicated classifier (e.g. FinBERT) deliberately: at this
project's volume (a handful of headlines per stock, periodically, not
high-frequency), Claude's reasoning is more valuable than a classifier's
speed/cost advantage, and it reuses the same grounded-prompt pattern
already validated in src/rag/generate_answer.py rather than introducing
a second ML framework for one task. See project conversation history
for the full trade-off if this decision is ever revisited at higher
volume.

Marketaux's own sentiment_score (stored alongside, see news_log.py) is
kept as a fast numeric signal for trend-tracking -- Claude's label and
reasoning are the primary output for a person reading a single
headline's analysis.
"""

from __future__ import annotations

import os
import json
import logging

import anthropic
from dotenv import load_dotenv

from src.news.news_log import NewsLog

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"  # same model as generate_answer.py, for consistency
MAX_TOKENS = 300  # short, structured output -- one label + a few sentences of reasoning per headline

SYSTEM_PROMPT = """You are a financial news sentiment analyst covering Indian-listed companies.

For each headline given, respond with ONLY a JSON object (no markdown fences, no preamble) in this exact shape:
{"label": "positive" | "negative" | "neutral" | "mixed", "reasoning": "one or two sentences explaining why"}

Rules:
- "mixed" is a valid and often more honest label than forcing a headline into positive/negative \
when it genuinely contains both good and bad signals (e.g. "wins large deal but margins under pressure").
- Judge sentiment from the perspective of what the headline implies about the company's business \
and outlook, not market mood in general.
- Do not give investment advice or price predictions. Describe what the headline signals, not what \
a reader should do about it.
- Keep reasoning concise and specific to the headline's actual content -- do not invent facts not \
present in the headline.
"""


def analyze_headline(headline: str, client: anthropic.Anthropic) -> tuple[str, str]:
    """Returns (label, reasoning). Raises on API failure -- caller decides how to handle."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Headline: {headline}"}],
    )

    raw_text = "".join(block.text for block in response.content if block.type == "text")

    try:
        parsed = json.loads(raw_text)
        label = parsed.get("label", "unknown")
        reasoning = parsed.get("reasoning", "")
    except json.JSONDecodeError:
        # Claude occasionally wraps JSON in explanation despite instructions --
        # log the raw text rather than silently losing it, so a parsing
        # failure is visible and debuggable rather than a mysterious skip.
        logger.warning("Could not parse Claude's response as JSON, storing raw text: %s", raw_text[:200])
        label = "unparsed"
        reasoning = raw_text

    return label, reasoning


def analyze_pending(log: NewsLog, batch_size: int = 50) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it in your environment (.env) before "
            "calling analyze_pending()."
        )

    client = anthropic.Anthropic()
    pending = log.needs_claude_analysis(limit=batch_size)

    analyzed = 0
    for article in pending:
        try:
            label, reasoning = analyze_headline(article["headline"], client)
        except anthropic.APIError as exc:
            logger.error("Claude API call failed for %r: %s", article["headline"][:60], exc)
            continue  # one failed article should not stop the batch

        log.record_claude_analysis(article["article_url"], label, reasoning)
        analyzed += 1
        logger.info("Analyzed [%s]: %s", label, article["headline"][:80])

    return analyzed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    log = NewsLog()
    count = analyze_pending(log)
    log.close()
    print(f"Analyzed {count} article(s). Run news_report.py to view results.")