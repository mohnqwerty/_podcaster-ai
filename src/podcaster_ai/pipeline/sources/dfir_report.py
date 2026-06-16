"""DFIR Report source.

Deep incident-response write-ups from The DFIR Report team. Each post
is a multi-thousand-word reconstruction of a real intrusion, with
timestamps, IOCs, MITRE ATT&CK technique IDs, and lessons learned.
Considered the gold standard for learning tradecraft on the blue side.

The DFIR Report was previously gated behind DFIR_REPORT_ENABLED in
the .env. It is now active by default — these reports are too valuable
to skip.
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
MAX_ITEMS: Final[int] = 4
LOOKBACK_DAYS: Final[int] = 60  # DFIR publishes a few posts per month


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def fetch() -> list[Item]:
    if not _env_bool("DFIR_REPORT_ENABLED", default=True):
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
            log.debug("dfir_report.entry_skipped", error=str(exc))
            continue

    log.info("dfir_report.fetched", count=len(items))
    return items
