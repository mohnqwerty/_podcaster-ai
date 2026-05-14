"""Fetch security conference news and upcoming event details."""

from __future__ import annotations

from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

# Primary conference news/aggregator sources.
FEEDS: Final[list[str]] = [
    "https://infosec-conferences.com/feed/",  # Infosec Conferences
    "https://media.ccc.de/podcast-hd.xml",    # CCC (Chaos Computer Club) conferences
]
SOURCE: Final[str] = "conferences"


def fetch() -> list[Item]:
    """Return recent conference news and event details. Fail-soft on error."""
    items: list[Item] = []
    try:
        with http_client() as client:
            for url in FEEDS:
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.content)
                    for entry in parsed.entries or []:
                        items.append(
                            Item(
                                title=(entry.get("title") or "").strip(),
                                url=(entry.get("link") or "").strip(),
                                summary=(entry.get("summary") or entry.get("description") or "").strip(),
                                source=SOURCE,
                                published_at=parse_dt(entry.get("published") or entry.get("updated")),
                            )
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("conferences.feed_failed", url=url, error=str(exc))
                    continue
    except Exception as exc:  # noqa: BLE001
        log.warning("conferences.fetch_failed", error=str(exc))
        return []

    log.info("conferences.fetched", count=len(items))
    return items
