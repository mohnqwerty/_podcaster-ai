"""Nullcon conference source (India).

Nullcon is the flagship Indian offensive-security conference held
annually in Goa. Covers bug bounty methodology, exploit development,
hardware security, and India-specific threat landscape.

For the elite-hacker goal: Nullcon talks are where the Indian hacker
community's latest research is presented — many of the top bug bounty
hunters from India debut techniques here first.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "nullcon"
FEED_URL: Final[str] = "https://nullcon.net/feed"
MAX_ITEMS: Final[int] = 5
LOOKBACK_DAYS: Final[int] = 365  # Nullcon is annual; widen window so annual CFP/announcement items surface


def fetch() -> list[Item]:
    try:
        with http_client() as client:
            resp = client.get(FEED_URL)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("nullcon.fetch_failed", error=str(exc))
        return []

    items: list[Item] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

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
            log.debug("nullcon.entry_skipped", error=str(exc))
            continue

    log.info("nullcon.fetched", count=len(items))
    return items
