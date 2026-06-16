"""The Record (Recorded Future News) — daily cybercrime journalism.

The Record is Recorded Future's news arm. It covers ransomware incidents,
cybercrime arrests, nation-state operations, and geopolitics with technical
depth and a daily cadence. The single best source for 'what just happened
in ransomware today'.

For the elite-hacker goal: The Record connects CVEs to live incidents
faster than any CVE database. When a new exploit chain is used in the
wild, you'll see it here within hours, often before NVD indexes it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "the_record"
FEED_URL: Final[str] = "https://therecord.media/feed/"
MAX_ITEMS: Final[int] = 5
LOOKBACK_DAYS: Final[int] = 7


def fetch() -> list[Item]:
    try:
        with http_client() as client:
            resp = client.get(FEED_URL)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("the_record.fetch_failed", error=str(exc))
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
            log.debug("the_record.entry_skipped", error=str(exc))
            continue

    log.info("the_record.fetched", count=len(items))
    return items
