"""Playwright-based scraper for full article extraction."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import structlog
from playwright.async_api import async_playwright

from .base import Item, parse_dt

log = structlog.get_logger(__name__)

_CLEAN_RE = re.compile(r"\s+")
_SELECTORS = [
    "article",
    "main",
    ".post-content",
    ".entry-content",
    ".article-content",
    ".story-body",
    ".article-body",
    "[itemprop=articleBody]",
    "#article-content",
    ".content",
]


async def fetch_article(url: str, browser_context: Any, timeout: int = 30000) -> str | None:
    """Load a single article URL and extract the main text content.

    Returns the full article text or None on failure.
    """
    page = await browser_context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        await page.wait_for_timeout(3000)

        text: str | None = None
        for sel in _SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el:
                    raw = await el.inner_text()
                    cleaned = _CLEAN_RE.sub(" ", raw).strip()
                    if len(cleaned) > 200:
                        text = cleaned
                        break
            except Exception:
                continue

        if not text:
            raw = await page.inner_text("body")
            text = _CLEAN_RE.sub(" ", raw).strip()[:5000]

        return text
    except Exception as exc:
        log.debug("scraper.article_failed", url=url, error=str(exc))
        return None
    finally:
        await page.close()


async def fetch_listing(
    url: str,
    browser_context: Any,
    link_selector: str = "a[href]",
    title_selector: str = "",
    summary_selector: str = "",
    date_selector: str = "",
    max_items: int = 12,
    wait_selector: str | None = None,
    timeout: int = 30000,
) -> list[dict[str, str]]:
    """Load a listing page and extract article metadata."""
    page = await browser_context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if wait_selector:
            await page.wait_for_selector(wait_selector, timeout=10000)
        else:
            await page.wait_for_timeout(3000)

        entries: list[dict[str, str]] = []

        links = await page.query_selector_all(link_selector)
        for link in links[:max_items]:
            href = await link.get_attribute("href")
            if not href:
                continue
            full_url = href if href.startswith("http") else f"https://{_extract_domain(url)}{href}"

            title = ""
            if title_selector:
                try:
                    title_el = await link.query_selector(title_selector)
                    if title_el:
                        title = await title_el.inner_text()
                except Exception:
                    pass
            if not title:
                title = await link.inner_text()

            summary = ""
            if summary_selector:
                try:
                    summary_el = await link.query_selector(summary_selector)
                    if summary_el:
                        summary = await summary_el.inner_text()
                except Exception:
                    pass

            date_text = ""
            if date_selector:
                try:
                    date_el = await link.query_selector(date_selector)
                    if date_el:
                        date_text = await date_el.inner_text()
                except Exception:
                    pass

            entries.append({
                "title": _CLEAN_RE.sub(" ", title).strip(),
                "url": full_url,
                "summary": _CLEAN_RE.sub(" ", summary).strip(),
                "date": date_text.strip(),
            })

        return entries
    except Exception as exc:
        log.warning("scraper.listing_failed", url=url, error=str(exc))
        return []
    finally:
        await page.close()


def _extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc


async def fetch_items(
    source: str,
    listing_url: str,
    link_selector: str,
    title_selector: str = "",
    summary_selector: str = "",
    date_selector: str = "",
    content_selector: str | None = None,
    max_items: int = 12,
    wait_selector: str | None = None,
    max_article_length: int = 5000,
) -> list[Item]:
    """Full pipeline: fetch listing, then each article via Playwright.

    Returns Items with full article text in summary.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        entries = await fetch_listing(
            url=listing_url,
            browser_context=context,
            link_selector=link_selector,
            title_selector=title_selector,
            summary_selector=summary_selector,
            date_selector=date_selector,
            max_items=max_items,
            wait_selector=wait_selector,
        )

        items: list[Item] = []
        for entry in entries:
            full_text = await fetch_article(entry["url"], context)
            summary = full_text[:max_article_length] if full_text else entry["summary"]
            published = None
            if entry.get("date"):
                published = parse_dt(entry["date"])

            items.append(Item(
                title=entry["title"] or "Untitled",
                url=entry["url"],
                summary=summary,
                source=source,
                published_at=published,
            ))

        await browser.close()

    log.info("scraper.fetch_items", source=source, count=len(items))
    return items
