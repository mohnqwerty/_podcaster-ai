"""DEF CON and Black Hat conference YouTube sources.

Both conferences post their recorded talks to YouTube. This module pulls
recent uploads from both channels so listeners can find deep technical
talks (often 30-60 min) on cutting-edge offensive security research.

For the elite-hacker goal: conference talks are the highest-density
learning resource per minute. A single DEF CON talk can teach more
about a technique than a year of news headlines.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "conferences_youtube"
FEEDS: Final[list[tuple[str, str]]] = [
    ("defcon", "https://www.youtube.com/feeds/videos.xml?channel_id=UC6Om9kAkl32dWlDSNlDS9Iw"),
    ("blackhat", "https://www.youtube.com/feeds/videos.xml?channel_id=UCJ6q9Ie29ajGqKApbLqfBOg"),
]
MAX_ITEMS_PER_FEED: Final[int] = 5
LOOKBACK_DAYS: Final[int] = 90


def _fetch_feed(client, source: str, url: str, cutoff: datetime) -> list[Item]:
    try:
        resp = client.get(url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("conferences_youtube.feed_failed", url=url, error=str(exc))
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
            log.debug("conferences_youtube.entry_skipped", source=source, error=str(exc))
            continue

    return items


def fetch() -> list[Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    items: list[Item] = []
    try:
        with http_client() as client:
            for source, url in FEEDS:
                items.extend(_fetch_feed(client, source, url, cutoff))
    except Exception as exc:
        log.warning("conferences_youtube.fetch_failed", error=str(exc))
        return items

    log.info("conferences_youtube.fetched", count=len(items))
    return items
