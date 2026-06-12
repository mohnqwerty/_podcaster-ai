"""Fetch PortSwigger research articles via Playwright for full content."""

from __future__ import annotations

import asyncio

import structlog

from .base import Item
from .scraper import fetch_items

log = structlog.get_logger(__name__)

SOURCE = "portswigger"
LISTING_URL = "https://portswigger.net/research"


def fetch() -> list[Item]:
    try:
        return asyncio.run(
            fetch_items(
                source=SOURCE,
                listing_url=LISTING_URL,
                link_selector="a[href*='/research/']",
                title_selector="h2, h3, .title",
                summary_selector="p, .summary",
                wait_selector="article, .research-card, main",
                max_items=12,
            )
        )
    except Exception as exc:
        log.warning("portswigger.fetch_failed", error=str(exc))
        return []
