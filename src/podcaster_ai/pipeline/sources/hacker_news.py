"""Hacker News frontpage RSS source.

Uses hnrss.org to pull recent front-page HN stories. Security-relevant
stories (new CVEs, tool releases, researcher writeups, deep technical
discussions) bubble up to the front page within hours of being posted.

For the elite-hacker goal: HN comments on security stories often contain
the smartest discussion in the industry — researchers debating the
real-world impact of a CVE, sharing POCs, or arguing about the best
mitigation. Many of the most important infosec career trajectories
started with HN.

Note: we don't filter to security-only here. The episode's research
stage will de-prioritise non-security items through the LLM prompt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "hacker_news"
FEED_URL: Final[str] = "https://hnrss.org/frontpage?count=30"
MAX_ITEMS: Final[int] = 8
LOOKBACK_HOURS: Final[int] = 36


def fetch() -> list[Item]:
    try:
        with http_client() as client:
            resp = client.get(FEED_URL)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("hacker_news.fetch_failed", error=str(exc))
        return []

    items: list[Item] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    for entry in parsed.entries or []:
        if len(items) >= MAX_ITEMS:
            break
        try:
            published = parse_dt(entry.get("published") or entry.get("updated"))
            if published is None or published < cutoff:
                continue

            link = (entry.get("link") or "").strip()
            if not link:
                continue

            title = (entry.get("title") or "Untitled").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()
            if not summary:
                continue

            items.append(
                Item(
                    title=title,
                    url=link,
                    summary=summary,
                    source=SOURCE,
                    published_at=published,
                )
            )
        except Exception as exc:
            log.debug("hacker_news.entry_skipped", error=str(exc))
            continue

    log.info("hacker_news.fetched", count=len(items))
    return items
