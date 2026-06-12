"""Fetch Krebs on Security articles via Playwright for full content."""

from __future__ import annotations

import asyncio

import structlog

from .base import Item
from .scraper import fetch_items

log = structlog.get_logger(__name__)

SOURCE = "krebs"
LISTING_URL = "https://krebsonsecurity.com"


def fetch() -> list[Item]:
    try:
        return asyncio.run(
            fetch_items(
                source=SOURCE,
                listing_url=LISTING_URL,
                link_selector="article a[href*='krebsonsecurity.com']",
                title_selector="h2.entry-title, h1",
                summary_selector=".entry-summary, .entry-content p",
                wait_selector="article, .hentry",
                max_items=3,
            )
        )
    except Exception as exc:
        log.warning("krebs.fetch_failed", error=str(exc))
        return []
