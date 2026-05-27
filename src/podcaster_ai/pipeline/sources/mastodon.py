"""Mastodon source — pulls infosec-relevant statuses from a Mastodon instance.

Reads the user's home timeline, configured hashtag timelines, and bookmarks
from a Mastodon-compatible instance (default: https://infosec.exchange).

This is a Tier-3 lead source: items must always be cross-checked against
authoritative sources (NVD, vendor advisories, PortSwigger, CISA KEV, etc.)
before being asserted as fact in show notes.

Configuration (env, all optional except ACCESS_TOKEN to enable the source):

- MASTODON_BASE_URL          (default https://infosec.exchange)
- MASTODON_ACCESS_TOKEN      (REQUIRED to enable; missing -> fetch returns [])
- MASTODON_HASHTAGS          (csv, default "infosec,cve,bugbounty,0day,threatintel")
- MASTODON_INCLUDE_HOME      (bool, default true)
- MASTODON_INCLUDE_BOOKMARKS (bool, default true)
- MASTODON_HOURS             (int hours window, default 48)
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Optional

import httpx

import feedparser
import structlog

from ...config import get_settings
from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "mastodon"

DEFAULT_BASE_URL: Final[str] = "https://infosec.exchange"
DEFAULT_HASHTAGS: Final[str] = "infosec,cve,bugbounty,0day,threatintel"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(content: str) -> str:
    """Best-effort HTML -> plain text. Mastodon content is sanitized HTML."""
    if not content:
        return ""
    # Replace <br> and </p> with newlines for readability before tag stripping.
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", content, flags=re.IGNORECASE)
    text = re.sub(r"</\s*p\s*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _status_to_item(status: dict[str, Any]) -> Optional[Item]:
    """Convert a Mastodon status dict to an Item, or None if unusable."""
    if not isinstance(status, dict):
        return None
    url = (status.get("url") or "").strip()
    if not url:
        return None

    content_html = status.get("content") or ""
    body = _strip_html(content_html)
    if not body:
        # Some statuses are media-only; skip rather than emit empty leads.
        return None

    title = body[:120].strip()
    account = status.get("account") or {}
    acct = (account.get("acct") or account.get("username") or "").strip()

    reblogs = _safe_int(status.get("reblogs_count"))
    favs = _safe_int(status.get("favourites_count"))
    replies = _safe_int(status.get("replies_count"))

    suffix_parts: list[str] = []
    if acct:
        suffix_parts.append(f"(via @{acct})")
    suffix_parts.append(f"[reblogs={reblogs} favs={favs} replies={replies}]")
    summary = (body + " " + " ".join(suffix_parts)).strip()

    extra: dict[str, Any] = {
        "acct": acct,
        "reblogs_count": reblogs,
        "favourites_count": favs,
        "replies_count": replies,
        # Lightweight engagement signal usable by the ranking stage.
        "engagement": reblogs + favs + (replies // 2),
    }
    tags = status.get("tags") or []
    if isinstance(tags, list):
        tag_names = [t.get("name") for t in tags if isinstance(t, dict) and t.get("name")]
        if tag_names:
            extra["tags"] = tag_names

    return Item(
        title=title,
        url=url,
        summary=summary,
        source=SOURCE,
        published_at=parse_dt(status.get("created_at")),
        extra=extra,
    )


def _get_json(
    client: httpx.Client,
    base_url: str,
    path: str,
    token: str,
    label: str,
) -> list[dict[str, Any]]:
    """GET a Mastodon endpoint and return the JSON list. Fail-soft."""
    full = base_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = client.get(full, headers=headers)
        
        # Fallback: if /timelines/home returns 401, fall back to public timeline
        if resp.status_code == 401 and "timelines/home" in path:
            log.warning("mastodon.home_401_fallback", endpoint=label)
            fallback_path = "/api/v1/timelines/public?local=false&limit=40"
            resp = client.get(base_url.rstrip("/") + fallback_path, headers=headers)
            
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        # Some Mastodon endpoints return {"error": "..."} on 4xx; treat as empty.
        log.warning("mastodon.unexpected_payload", endpoint=label, type=type(data).__name__)
        return []
    except Exception as exc:  # noqa: BLE001 — fail-soft, never raise
        log.warning("mastodon.endpoint_failed", endpoint=label, error=str(exc))
        return []


# Public Mastodon instances for RSS-based fallback (no token needed).
# Index 0 is tried first for all hashtags; if it returns empty, subsequent
# instances are tried as fallback per-hashtag.
RSS_INSTANCES: Final[list[str]] = [
    "https://mastodon.social",
    "https://infosec.exchange",
]


def _fetch_rss_hashtag(tag: str, cutoff: datetime) -> list[Item]:
    """Fetch a hashtag feed via RSS (no auth required)."""
    items: list[Item] = []
    for instance in RSS_INSTANCES:
        try:
            url = f"{instance}/tags/{tag}.rss"
            with http_client() as client:
                resp = client.get(url, follow_redirects=True)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
                if not parsed.entries:
                    continue
                for entry in parsed.entries:
                    link = (entry.get("link") or "").strip()
                    if not link:
                        continue
                    raw_title = (entry.get("title") or "").strip()
                    raw_summary = (entry.get("summary") or entry.get("description") or "").strip()
                    # Mastodon RSS often has null title; fall back to parsed summary.
                    if not raw_title and raw_summary:
                        raw_title = _strip_html(raw_summary)
                    title = raw_title[:120].strip()
                    if not title:
                        continue
                    published = parse_dt(entry.get("published") or entry.get("updated"))
                    if published and published < cutoff:
                        continue
                    items.append(
                        Item(
                            title=title[:120],
                            url=link,
                            summary=_strip_html(raw_summary)[:500],
                            source=SOURCE,
                            published_at=published,
                            extra={"tags": [tag], "source": f"RSS #{tag} on {instance}"},
                        )
                    )
                if items:
                    break
        except Exception:
            continue
    return items


def _fetch_rss(hashtags: list[str], cutoff: datetime) -> list[Item]:
    """Fetch Mastodon via RSS fallback (no auth required)."""
    items: list[Item] = []
    seen: set[str] = set()
    for tag in hashtags:
        for item in _fetch_rss_hashtag(tag, cutoff):
            key = item.url.lower()
            if key not in seen:
                seen.add(key)
                items.append(item)
    return items


def _mastodon_settings() -> dict[str, Any]:
    """Read Mastodon config from the central Settings (reads .env)."""
    s = get_settings()
    return {
        "token": s.mastodon_access_token or "",
        "base_url": s.mastodon_base_url or DEFAULT_BASE_URL,
        "hashtags": [h.strip().lstrip("#") for h in (s.mastodon_hashtags or DEFAULT_HASHTAGS).split(",") if h.strip()],
        "hours": max(1, s.mastodon_hours),
        "include_home": bool(s.mastodon_include_home),
        "include_bookmarks": bool(s.mastodon_include_bookmarks),
    }


def fetch() -> list[Item]:
    """Return recent Mastodon items. Always fail-soft; never raises."""
    cfg = _mastodon_settings()
    token = cfg["token"]
    base_url = cfg["base_url"]
    hashtags = cfg["hashtags"]
    hours = cfg["hours"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    if not token:
        log.info("mastodon.rss_fallback", hashtags=hashtags)
        items = _fetch_rss(hashtags, cutoff)
        log.info("mastodon.fetched", count=len(items), mode="rss", hashtags=hashtags)
        return items

    # Token is present — try API first, fall back to RSS if API returns nothing.
    include_home = cfg["include_home"]
    include_bookmarks = cfg["include_bookmarks"]
    statuses: list[dict[str, Any]] = []

    try:
        with http_client() as client:
            if include_home:
                statuses.extend(
                    _get_json(
                        client,
                        base_url,
                        "/api/v1/timelines/home?limit=40",
                        token,
                        "home",
                    )
                )
            for tag in hashtags:
                statuses.extend(
                    _get_json(
                        client,
                        base_url,
                        f"/api/v1/timelines/tag/{tag}?limit=40",
                        token,
                        f"tag:{tag}",
                    )
                )
            if include_bookmarks:
                statuses.extend(
                    _get_json(
                        client,
                        base_url,
                        "/api/v1/bookmarks?limit=40",
                        token,
                        "bookmarks",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("mastodon.fetch_failed", error=str(exc))
        statuses = []

    items: list[Item] = []
    seen_urls: set[str] = set()
    for status in statuses:
        if isinstance(status, dict) and status.get("reblog"):
            inner = status.get("reblog")
            if isinstance(inner, dict):
                status = inner
        item = _status_to_item(status)
        if item is None:
            continue
        if item.published_at is not None and item.published_at < cutoff:
            continue
        key = item.url.lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)
        items.append(item)

    # If API returned nothing, fall back to RSS (token may be stale/invalid).
    if not items:
        log.info("mastodon.api_empty_rss_fallback", hashtags=hashtags)
        items = _fetch_rss(hashtags, cutoff)

    log.info(
        "mastodon.fetched",
        count=len(items),
        mode="api" if statuses else "rss_fallback",
        base_url=base_url,
        hashtags=hashtags,
        hours=hours,
    )
    return items
