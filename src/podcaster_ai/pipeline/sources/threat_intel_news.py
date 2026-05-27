"""Fetch general security news from threat intel and infosec news outlets."""

from __future__ import annotations

from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

FEEDS: Final[list[str]] = [
    "https://www.bleepingcomputer.com/feed/",
    "https://feeds.feedburner.com/TheHackersNews",
    "https://feeds.feedburner.com/SecurityWeek",
]
SOURCE: Final[str] = "threat_intel_news"


def fetch() -> list[Item]:
    """Return recent security news items. Fail-soft on error."""
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
                    log.warning("threat_intel_news.feed_failed", url=url, error=str(exc))
                    continue
    except Exception as exc:  # noqa: BLE001
        log.warning("threat_intel_news.fetch_failed", error=str(exc))
        return []

    log.info("threat_intel_news.fetched", count=len(items))
    return items
