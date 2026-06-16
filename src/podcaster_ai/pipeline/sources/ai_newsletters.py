"""AI / builder-focused newsletter sources.

Pulls the AI Engineer ecosystem (swyx, Latent Space, Interconnects,
Ben's Bites) — these are the Substacks the user reads and that drive
the LLM / agent / dev-tools news cycle.

For the elite-hacker goal: AI agents are a new attack surface (prompt
injection, MCP abuse, indirect prompt injection from web content).
These newsletters are where new LLM-bypass techniques are first shared.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "ai_newsletters"
FEEDS: Final[list[tuple[str, str]]] = [
    ("latent_space", "https://www.latent.space/feed"),
    ("interconnects", "https://www.interconnects.ai/feed"),
    ("bens_bites", "https://www.bensbites.com/feed"),
    ("swyx", "https://www.swyx.io/feed"),
]
MAX_ITEMS_PER_FEED: Final[int] = 3
LOOKBACK_DAYS: Final[int] = 14


def _fetch_feed(client, source: str, url: str, cutoff: datetime) -> list[Item]:
    try:
        resp = client.get(url)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("ai_newsletters.feed_failed", url=url, error=str(exc))
        return []

    items: list[Item] = []
    for entry in parsed.entries or []:
        if len(items) >= MAX_ITEMS_PER_FEED:
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
                    source=source,
                    published_at=published,
                )
            )
        except Exception as exc:
            log.debug("ai_newsletters.entry_skipped", source=source, error=str(exc))
            continue

    return items


def fetch() -> list[Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    items: list[Item] = []
    try:
        with http_client() as client:
            for source, url in FEEDS:
                items.extend(_fetch_feed(client, source, url, cutoff))
    except Exception as exc:
        log.warning("ai_newsletters.fetch_failed", error=str(exc))
        return items

    log.info("ai_newsletters.fetched", count=len(items))
    return items
