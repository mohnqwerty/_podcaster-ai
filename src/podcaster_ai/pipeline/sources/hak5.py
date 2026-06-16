"""Hak5 YouTube channel source.

Hak5 produces hardware-hacking and infosec content including Threat Wire
(daily 5-min news), Hak5 show, Packet Hacking, and Bash Bunny / LAN Turtle
walkthroughs. All episodes are about practical offensive security tradecraft.

For the elite-hacker goal: Hak5 content is hands-on and reproducible —
the kind of content that turns passive listeners into active pentesters.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "hak5"
FEED_URL: Final[str] = "https://www.youtube.com/feeds/videos.xml?channel_id=UC3s0BtrBJpwNDaflRSoiieQ"
MAX_ITEMS: Final[int] = 6
LOOKBACK_DAYS: Final[int] = 30


def fetch() -> list[Item]:
    try:
        with http_client() as client:
            resp = client.get(FEED_URL)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("hak5.fetch_failed", error=str(exc))
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
            log.debug("hak5.entry_skipped", error=str(exc))
            continue

    log.info("hak5.fetched", count=len(items))
    return items
