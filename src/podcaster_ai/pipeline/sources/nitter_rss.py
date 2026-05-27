"""Fetch tweets from security researchers via public Nitter RSS feeds.

No API key needed — uses public Nitter instances to get RSS of user timelines.
Fail-soft: tries multiple instances per account; silently skips failures.
"""

from __future__ import annotations

from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "nitter"

NITTER_INSTANCES: Final[list[str]] = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.kavin.rocks",
    "https://nitter.lqdv.com",
]

TWITTER_ACCOUNTS: Final[list[str]] = [
    "hacker0x01",
    "Bugcrowd",
    "intigriti",
    "sehacure",
    "demonslay335",
    "malwrhunterteam",
    "buzz_smith",
    "Pancak3lullz",
]


def fetch() -> list[Item]:
    items: list[Item] = []
    for account in TWITTER_ACCOUNTS:
        for instance in NITTER_INSTANCES:
            url = f"{instance}/{account}/rss"
            try:
                with http_client() as client:
                    resp = client.get(url, follow_redirects=True)
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.content)
                    if not parsed.entries:
                        continue
                    for entry in parsed.entries:
                        items.append(
                            Item(
                                title=(entry.get("title") or "").strip(),
                                url=(entry.get("link") or "").strip(),
                                summary=(entry.get("summary") or entry.get("description") or "").strip(),
                                source=SOURCE,
                                published_at=parse_dt(entry.get("published") or entry.get("updated")),
                            )
                        )
                    break
            except Exception:
                continue

    log.info("nitter.fetched", count=len(items), accounts=len(TWITTER_ACCOUNTS))
    return items
