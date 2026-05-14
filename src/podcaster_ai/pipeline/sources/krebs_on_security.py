"""Krebs on Security source.

Fetches latest articles from RSS feed.
Capped to 1 latest article per run.

Configuration (env):
- KREBS_ENABLED   (bool, default false)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "krebs"
FEED_URL: Final[str] = "https://krebsonsecurity.com/feed/"


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean from environment variable."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def fetch() -> list[Item]:
    """Return latest Krebs on Security article."""
    if not _env_bool("KREBS_ENABLED"):
        log.info("krebs.disabled", reason="KREBS_ENABLED not set")
        return []

    try:
        with http_client() as client:
            resp = client.get(FEED_URL)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("krebs.fetch_failed", error=str(exc))
        return []

    items: list[Item] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    for entry in parsed.entries or []:
        try:
            published = parse_dt(entry.get("published") or entry.get("updated"))
            if published is None or published < cutoff:
                continue

            link = (entry.get("link") or "").strip()
            if not link:
                continue

            title = (entry.get("title") or "Untitled article").strip()
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
            # Cap to 1 latest article per run
            if len(items) >= 1:
                break
        except Exception as exc:  # noqa: BLE001
            log.debug("krebs.entry_skipped", error=str(exc))
            continue

    log.info("krebs.fetched", count=len(items))
    return items
