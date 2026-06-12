"""Fetch security news via Playwright for full article content."""

from __future__ import annotations

import asyncio

import structlog

from .base import Item
from .scraper import fetch_items

log = structlog.get_logger(__name__)

SOURCE = "threat_intel_news"
SITES = [
    ("https://www.bleepingcomputer.com", "a[href*='/news/']"),
    ("https://thehackernews.com", "a[href*='thehackernews.com']"),
    ("https://www.securityweek.com", "a[href*='/securityweek.com/']"),
]


def fetch() -> list[Item]:
    items: list[Item] = []
    for url, selector in SITES:
        try:
            site_items = asyncio.run(
                fetch_items(
                    source=SOURCE,
                    listing_url=url,
                    link_selector=selector,
                    title_selector="h2, h3, .entry-title, .post-title",
                    summary_selector="p, .summary, .entry-summary",
                    wait_selector="article, main, .site-main",
                    max_items=5,
                )
            )
            items.extend(site_items)
        except Exception as exc:
            log.warning("threat_intel_news.site_failed", url=url, error=str(exc))
            continue
    return items
