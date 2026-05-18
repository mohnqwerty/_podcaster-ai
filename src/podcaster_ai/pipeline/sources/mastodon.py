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
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Optional

import httpx
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "mastodon"

DEFAULT_BASE_URL: Final[str] = "https://infosec.exchange"
DEFAULT_HASHTAGS: Final[str] = "infosec,cve,bugbounty,0day,threatintel"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _env_str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


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


def fetch() -> list[Item]:
    """Return recent Mastodon items. Always fail-soft; never raises."""
    token = _env_str("MASTODON_ACCESS_TOKEN", "")
    if not token:
        log.info("mastodon.disabled", reason="MASTODON_ACCESS_TOKEN not set")
        return []

    base_url = _env_str("MASTODON_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    hashtags_csv = _env_str("MASTODON_HASHTAGS", DEFAULT_HASHTAGS) or DEFAULT_HASHTAGS
    hashtags = [h.strip().lstrip("#") for h in hashtags_csv.split(",") if h.strip()]
    include_home = _env_bool("MASTODON_INCLUDE_HOME", True)
    include_bookmarks = _env_bool("MASTODON_INCLUDE_BOOKMARKS", True)
    hours = max(1, _env_int("MASTODON_HOURS", 48))

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
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
                # The Mastodon API expects the bare tag (no leading #) in the path.
                # Mastodon now requires ?limit=40 plus likely a different param shape.
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
        return []

    items: list[Item] = []
    seen_urls: set[str] = set()
    for status in statuses:
        # Mastodon "reblog" wraps another status; prefer the original payload
        # so we surface the source author, not the reblogger.
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

    log.info(
        "mastodon.fetched",
        count=len(items),
        base_url=base_url,
        hashtags=hashtags,
        hours=hours,
    )
    return items
