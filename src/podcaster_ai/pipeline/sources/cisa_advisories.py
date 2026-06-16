"""BleepingComputer ransomware coverage source.

Pulls BleepingComputer's main RSS feed, filtering for ransomware-tagged
posts. CISA advisories (the planned source) are blocked at the network
level for our IP range, so BleepingComputer serves as the best open
replacement — they publish ransomware news daily and are the de-facto
journalistic source for new variants and techniques.

For the elite-hacker goal: ransomware tradecraft evolves weekly. Reading
BleepingComputer's ransomware coverage daily is the cheapest way to
stay current on new extortion tactics, leaked builder source, and
threat-actor TTP changes.

The source key remains 'cisa_advisories' for backwards compatibility
with the source attribution map and reference priority list, but
logically the source is BleepingComputer's ransomware feed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "cisa_advisories"  # kept for backwards compatibility
FEED_URL: Final[str] = "https://www.bleepingcomputer.com/feed/"
MAX_ITEMS: Final[int] = 5
LOOKBACK_DAYS: Final[int] = 14
_RANSOMWARE_KEYWORDS: Final[tuple[str, ...]] = (
    "ransomware", "ransom", "extortion", "leak site", "double extortion",
    "lockbit", "blackcat", "alphv", "akira", "play", "cl0p", "clop",
    "8base", "rhysida", "medusa", "inc ransom",
)


def _is_ransomware_post(entry) -> bool:
    """Return True if a BleepingComputer entry is ransomware-tagged."""
    title = (entry.get("title") or "").lower()
    summary = (entry.get("summary") or entry.get("description") or "").lower()
    for cat in entry.get("tags") or []:
        term = (cat.get("term") or "").lower() if hasattr(cat, "get") else ""
        if "ransom" in term:
            return True
    blob = f"{title} {summary}"
    return any(kw in blob for kw in _RANSOMWARE_KEYWORDS)


def fetch() -> list[Item]:
    try:
        with http_client() as client:
            resp = client.get(FEED_URL)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("cisa_advisories.fetch_failed", error=str(exc))
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

            if not _is_ransomware_post(entry):
                continue

            items.append(
                Item(
                    title=title,
                    url=link,
                    summary=summary,
                    source=SOURCE,
                    published_at=published,
                    extra={"is_ransomware": True},
                )
            )
        except Exception as exc:
            log.debug("cisa_advisories.entry_skipped", error=str(exc))
            continue

    log.info("cisa_advisories.fetched", count=len(items))
    return items
