"""
Phase 3b (News & sentiment layer) -- CLI report.

Usage:
    python -m src.news.news_report RELIANCE
    python -m src.news.news_report TCS --limit 10

Prerequisite pipeline order:
    1. fetch_news.py       -- pull recent articles from Marketaux
    2. analyze_sentiment.py -- run Claude sentiment analysis on new articles
    3. news_report.py       -- this script, view results for a symbol
"""

from __future__ import annotations

import argparse

from src.news.news_log import NewsLog


def main() -> None:
    parser = argparse.ArgumentParser(description="View recent news + sentiment for a watchlist stock.")
    parser.add_argument("symbol", type=str, help="Bare NSE symbol, e.g. RELIANCE (not RELIANCE.NS)")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    log = NewsLog()
    articles = log.get_recent_for_symbol(args.symbol, limit=args.limit)
    log.close()

    if not articles:
        print(f"No articles found for {args.symbol}. Run fetch_news.py first.")
        return

    print(f"\n{'='*70}\n{args.symbol} -- {len(articles)} recent article(s)\n{'='*70}\n")

    for a in articles:
        print(f"[{a['published_at']}] {a['source'] or 'unknown source'}")
        print(f"  {a['headline']}")

        mx_score = a.get("marketaux_sentiment_score")
        mx_str = f"{mx_score:.2f}" if mx_score is not None else "n/a"

        label = a.get("claude_sentiment_label") or "not yet analyzed"
        print(f"  Marketaux score: {mx_str}  |  Claude label: {label}")

        if a.get("claude_reasoning"):
            print(f"  Reasoning: {a['claude_reasoning']}")

        print(f"  {a['article_url']}")
        print()


if __name__ == "__main__":
    main()