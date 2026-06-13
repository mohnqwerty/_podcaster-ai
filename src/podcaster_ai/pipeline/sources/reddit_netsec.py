"""Reddit r/netsec and r/bugbounty source.

Fetches top RSS posts from the two highest-signal security subreddits.
No API key required (RSS is public). High-value for the elite-hacker
goal: fresh PoC drops, writeups, tool releases, community discussion.

Configuration (env):
- REDDIT_LOOKBACK_HOURS  (int, default 72)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "reddit"
FEEDS: Final[list[tuple[str, str]]] = [
    ("reddit_netsec", "https://www.reddit.com/r/netsec/.rss?limit=25"),
    ("reddit_bugbounty", "https://www.reddit.com/r/bugbounty/.rss?limit=25"),
]
MAX_ITEMS_PER_FEED: Final[int] = 5


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _fetch_feed(client, source: str, url: str, cutoff: datetime) -> list[Item]:
    try:
        resp = client.get(url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("reddit.feed_failed", url=url, error=str(exc))
        return []

    items: list[Item] = []
    for entry in parsed.entries or []:
        if len(items) >= MAX_ITEMS_PER_FEED:
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
                    source=source,
                    published_at=published,
                )
            )
        except Exception as exc:
            log.debug("reddit.entry_skipped", source=source, error=str(exc))
            continue

    return items


def fetch() -> list[Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_env_int("REDDIT_LOOKBACK_HOURS", 72))

    items: list[Item] = []
    try:
        with http_client() as client:
            for source, url in FEEDS:
                items.extend(_fetch_feed(client, source, url, cutoff))
    except Exception as exc:
        log.warning("reddit.fetch_failed", error=str(exc))
        return items

    log.info("reddit.fetched", count=len(items))
    return items
