"""Fetch AI-specific security news and advisories."""

from __future__ import annotations

from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

# Primary AI security sources.
FEEDS: Final[list[str]] = [
    "https://blog.trailofbits.com/feed/",  # Trail of Bits security research
    "https://embracethered.com/blog/index.xml",     # Embrace The Red security blog
    "https://protectai.com/blog/rss.xml", # Protect AI security advisories
]
SOURCE: Final[str] = "ai_security"


def fetch() -> list[Item]:
    """Return recent AI security items. Fail-soft on error."""
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
                    log.warning("ai_security.feed_failed", url=url, error=str(exc))
                    continue
    except Exception as exc:  # noqa: BLE001
        log.warning("ai_security.fetch_failed", error=str(exc))
        return []

    log.info("ai_security.fetched", count=len(items))
    return items
