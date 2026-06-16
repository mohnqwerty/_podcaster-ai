"""Hak5 YouTube source.

Hak5 produces hardware-hacking and infosec content including Threat Wire
(daily 5-min news), Hak5 show, Packet Hacking, and Bash Bunny / LAN Turtle
walkthroughs. The operator explicitly listed Threat Wire as a daily
watch — this source is restored in degraded mode with multiple
fallbacks because the YouTube RSS feed has been intermittently 404ing
across all proxies (YouTube direct, every Invidious instance, Piped).
We cycle through the fallbacks so the operator gets at least some
Hak5 content on days when any one feed works.

Channel ID verified: UC3s0BtrBJpwNDaflRSoiieQ (YouTube search 2026-06-16).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "hak5"
# We try Invidious first (most stable), then alternate instances, then
# fall back to YouTube direct. Each URL is tried in order; first one
# that returns a non-empty feed wins for the day.
FEED_URLS: Final[list[str]] = [
    # Invidious public instances (rotated so we don't hammer one)
    "https://invidious.materialio.us/feed/channel/UC3s0BtrBJpwNDaflRSoiieQ",
    "https://yewtu.be/feed/channel/UC3s0BtrBJpwNDaflRSoiieQ",
    "https://invidious.snopyta.org/feed/channel/UC3s0BtrBJpwNDaflRSoiieQ",
    "https://invidious.fdn.fr/feed/channel/UC3s0BtrBJpwNDaflRSoiieQ",
    "https://invidious.kavin.rocks/feed/channel/UC3s0BtrBJpwNDaflRSoiieQ",
    # YouTube direct as last resort
    "https://www.youtube.com/feeds/videos.xml?channel_id=UC3s0BtrBJpwNDaflRSoiieQ",
]
MAX_ITEMS: Final[int] = 6
LOOKBACK_DAYS: Final[int] = 30


def _try_fetch(url: str) -> Any | None:
    """Return feedparser result if successful, None on failure."""
    try:
        with http_client() as client:
            resp = client.get(url)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        if parsed.entries:
            return parsed
        log.info("hak5.empty_feed", url=url)
        return None
    except Exception as exc:  # noqa: BLE001
        log.info("hak5.feed_failed", url=url, error=str(exc)[:120])
        return None


def fetch() -> list[Item]:
    parsed = None
    used_url = None
    for url in FEED_URLS:
        result = _try_fetch(url)
        if result is not None:
            parsed = result
            used_url = url
            break

    if parsed is None:
        log.warning("hak5.all_feeds_failed", urls=len(FEED_URLS))
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

    log.info("hak5.fetched", count=len(items), via=used_url)
    return items
