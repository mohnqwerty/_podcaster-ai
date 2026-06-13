"""OWASP project updates source.

Pulls the official OWASP blog feed and a few of the high-value project
feeds (Top 10, ASVS, Cheat Sheet Series, Web Security Testing Guide).
For the elite-hacker goal these are the de-facto curriculum.

All feeds are public RSS, no auth.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "owasp"
FEEDS: Final[list[tuple[str, str]]] = [
    ("owasp_blog", "https://owasp.org/feed.xml"),
    ("owasp_top10", "https://github.com/OWASP/Top10/releases.atom"),
    ("owasp_asvs", "https://github.com/OWASP/ASVS/releases.atom"),
    ("owasp_cheatsheets", "https://github.com/OWASP/CheatSheetSeries/releases.atom"),
    ("owasp_wstg", "https://github.com/OWASP/wstg/releases.atom"),
]
MAX_ITEMS_PER_FEED: Final[int] = 3
LOOKBACK_DAYS: Final[int] = 60


def _fetch_feed(client, source: str, url: str, cutoff: datetime) -> list[Item]:
    try:
        resp = client.get(url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("owasp.feed_failed", url=url, error=str(exc))
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
            log.debug("owasp.entry_skipped", source=source, error=str(exc))
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
        log.warning("owasp.fetch_failed", error=str(exc))
        return items

    log.info("owasp.fetched", count=len(items))
    return items
