"""Fetch security conference news via Playwright for full article content."""

from __future__ import annotations

import asyncio

import structlog

from .base import Item
from .scraper import fetch_items

log = structlog.get_logger(__name__)

SOURCE = "conferences"
LISTING_URL = "https://infosec-conferences.com"


def fetch() -> list[Item]:
    try:
        return asyncio.run(
            fetch_items(
                source=SOURCE,
                listing_url=LISTING_URL,
                link_selector="a[href*='infosec-conferences.com']",
                title_selector="h2, h3, .entry-title, .post-title",
                summary_selector="p, .excerpt",
                wait_selector="article, main, #primary",
                max_items=10,
            )
        )
    except Exception as exc:
        log.warning("conferences.fetch_failed", error=str(exc))
        return []
