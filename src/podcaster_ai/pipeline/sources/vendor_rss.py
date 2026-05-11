"""Fetch arbitrary vendor advisory RSS feeds configured via env var."""

from __future__ import annotations

from typing import Final
from urllib.parse import urlparse

import feedparser
import structlog

from ...config import get_settings
from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "vendor_rss"


def _label_for(url: str) -> str:
    try:
        host = urlparse(url).hostname or "vendor"
    except Exception:  # noqa: BLE001
        host = "vendor"
    return host.replace("www.", "")


def _fetch_one(client, url: str) -> list[Item]:
    label = _label_for(url)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("vendor_rss.feed_failed", url=url, error=str(exc))
        return []

    items: list[Item] = []
    for entry in parsed.entries or []:
        try:
            items.append(
                Item(
                    title=(entry.get("title") or "").strip(),
                    url=(entry.get("link") or "").strip(),
                    summary=(
                        entry.get("summary") or entry.get("description") or ""
                    ).strip(),
                    source=SOURCE,
                    published_at=parse_dt(
                        entry.get("published") or entry.get("updated")
                    ),
                    extra={"vendor": label},
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("vendor_rss.entry_skipped", url=url, error=str(exc))
            continue
    return items


def fetch() -> list[Item]:
    """Return aggregated items from all vendor RSS feeds in env. Fail-soft per-feed."""
    settings = get_settings()
    feeds = settings.vendor_feed_list()
    if not feeds:
        log.info("vendor_rss.disabled", reason="no feeds configured")
        return []

    items: list[Item] = []
    try:
        with http_client() as client:
            for url in feeds:
                items.extend(_fetch_one(client, url))
    except Exception as exc:  # noqa: BLE001
        log.warning("vendor_rss.fetch_failed", error=str(exc))
        return items

    log.info("vendor_rss.fetched", count=len(items), feeds=len(feeds))
    return items
