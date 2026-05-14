"""The DFIR Report source.

Fetches latest incident analysis from RSS feed.
Capped to 1 latest report per run.

Configuration (env):
- DFIR_REPORT_ENABLED   (bool, default false)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "dfir_report"
FEED_URL: Final[str] = "https://thedfirreport.com/feed/"


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean from environment variable."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def fetch() -> list[Item]:
    """Return latest DFIR Report incident analysis."""
    if not _env_bool("DFIR_REPORT_ENABLED"):
        log.info("dfir_report.disabled", reason="DFIR_REPORT_ENABLED not set")
        return []

    try:
        with http_client() as client:
            resp = client.get(FEED_URL)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("dfir_report.fetch_failed", error=str(exc))
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

            title = (entry.get("title") or "Untitled report").strip()
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
            # Cap to 1 latest report per run
            if len(items) >= 1:
                break
        except Exception as exc:  # noqa: BLE001
            log.debug("dfir_report.entry_skipped", error=str(exc))
            continue

    log.info("dfir_report.fetched", count=len(items))
    return items
