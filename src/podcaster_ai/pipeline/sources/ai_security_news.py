"""Fetch AI security news via Playwright for full article content."""

from __future__ import annotations

import asyncio

import structlog

from .base import Item
from .scraper import fetch_items

log = structlog.get_logger(__name__)

SOURCE = "ai_security"
LISTING_URL = "https://venturebeat.com/category/ai/"


def fetch() -> list[Item]:
    try:
        return asyncio.run(
            fetch_items(
                source=SOURCE,
                listing_url=LISTING_URL,
                link_selector="a[href*='venturebeat.com']",
                title_selector="h2, h3, .article-title, .entry-title",
                summary_selector="p, .excerpt, .summary",
                wait_selector="article, main, .river",
                max_items=7,
            )
        )
    except Exception as exc:
        log.warning("ai_security.fetch_failed", error=str(exc))
        return []
