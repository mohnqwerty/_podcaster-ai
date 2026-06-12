"""Fetch hardware hacking news via Playwright for full article content."""

from __future__ import annotations

import asyncio

import structlog

from .base import Item
from .scraper import fetch_items

log = structlog.get_logger(__name__)

SOURCE = "hardware_hacking"
LISTING_URL = "https://hackaday.com/category/security-hacks/"


def fetch() -> list[Item]:
    try:
        return asyncio.run(
            fetch_items(
                source=SOURCE,
                listing_url=LISTING_URL,
                link_selector="article a[href*='hackaday.com']",
                title_selector="h2.entry-title, h1",
                summary_selector=".entry-summary, p",
                wait_selector="article, .post",
                max_items=7,
            )
        )
    except Exception as exc:
        log.warning("hardware_hacking.fetch_failed", error=str(exc))
        return []
