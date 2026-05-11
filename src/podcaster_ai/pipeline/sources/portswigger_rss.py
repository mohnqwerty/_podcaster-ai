"""Fetch the PortSwigger Research RSS feed.

URL: https://portswigger.net/research/rss
"""

from __future__ import annotations

from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

FEED_URL: Final[str] = "https://portswigger.net/research/rss"
SOURCE: Final[str] = "portswigger"


def fetch() -> list[Item]:
    """Return recent PortSwigger Research posts as Items. Fail-soft on error."""
    try:
        with http_client() as client:
            resp = client.get(FEED_URL)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("portswigger.fetch_failed", error=str(exc))
        return []

    items: list[Item] = []
    for entry in parsed.entries or []:
        try:
            items.append(
                Item(
                    title=(entry.get("title") or "").strip(),
                    url=(entry.get("link") or "").strip(),
                    summary=(entry.get("summary") or entry.get("description") or "").strip(),
                    source=SOURCE,
                    published_at=parse_dt(
                        entry.get("published") or entry.get("updated")
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("portswigger.entry_skipped", error=str(exc))
            continue

    log.info("portswigger.fetched", count=len(items))
    return items
