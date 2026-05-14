"""Ransomwatch source — public ransomware leak tracker.

Fetches recent ransomware victims from the ransomwatch API.
Produces a single summary item per run with top groups and victim count.

Configuration (env):
- RANSOMWATCH_ENABLED   (bool, default false)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import httpx
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "ransomwatch"
PRIMARY_URL: Final[str] = "https://ransomwatch.telemetry.ltd/posts.json"
FALLBACK_URL: Final[str] = "https://api.ransomware.live/recentvictims"


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean from environment variable."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _fetch_primary() -> list[dict[str, Any]]:
    """Fetch from primary ransomwatch API endpoint."""
    try:
        with http_client() as client:
            resp = client.get(PRIMARY_URL)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return []
    except Exception as exc:  # noqa: BLE001
        log.debug("ransomwatch.primary_failed", error=str(exc))
        return []


def _fetch_fallback() -> list[dict[str, Any]]:
    """Fetch from fallback ransomware.live API endpoint."""
    try:
        with http_client() as client:
            resp = client.get(FALLBACK_URL)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return []
    except Exception as exc:  # noqa: BLE001
        log.debug("ransomwatch.fallback_failed", error=str(exc))
        return []


def fetch() -> list[Item]:
    """Return a summary of recent ransomware victims."""
    if not _env_bool("RANSOMWATCH_ENABLED"):
        log.info("ransomwatch.disabled", reason="RANSOMWATCH_ENABLED not set")
        return []

    # Try primary endpoint first, fallback to alternative
    victims = _fetch_primary()
    if not victims:
        victims = _fetch_fallback()

    if not victims:
        log.warning("ransomwatch.no_data")
        return []

    # Filter to last 24-48 hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    recent_victims: list[dict[str, Any]] = []

    for victim in victims:
        try:
            # Try to parse published_at from various possible fields
            pub_str = victim.get("published") or victim.get("date") or victim.get("discovered") or ""
            if not pub_str:
                continue
            pub_dt = parse_dt(pub_str)
            if pub_dt is None or pub_dt < cutoff:
                continue
            recent_victims.append(victim)
        except Exception:  # noqa: BLE001
            continue

    if not recent_victims:
        log.info("ransomwatch.no_recent_victims")
        return []

    # Extract group names and count
    groups: dict[str, int] = {}
    for victim in recent_victims:
        group = (victim.get("group") or victim.get("actor") or "Unknown").strip()
        if group:
            groups[group] = groups.get(group, 0) + 1

    # Sort by count and take top 5
    top_groups = sorted(groups.items(), key=lambda x: x[1], reverse=True)[:5]
    top_group_names = [g[0] for g in top_groups]

    title = f"Ransomware Update: {len(recent_victims)} new victims claimed"
    group_list = ", ".join(top_group_names) if top_group_names else "various groups"
    summary = (
        f"In the last 48 hours, {len(recent_victims)} new victims have been claimed "
        f"by ransomware groups. Top actors: {group_list}. "
        f"Details available at ransomwatch.telemetry.ltd."
    )

    # Use the most recent victim's timestamp as the item's published_at
    latest_pub = None
    for victim in recent_victims:
        pub_str = victim.get("published") or victim.get("date") or victim.get("discovered") or ""
        if pub_str:
            pub_dt = parse_dt(pub_str)
            if pub_dt and (latest_pub is None or pub_dt > latest_pub):
                latest_pub = pub_dt

    items = [
        Item(
            title=title,
            url="https://ransomwatch.telemetry.ltd/",
            summary=summary,
            source=SOURCE,
            published_at=latest_pub or datetime.now(timezone.utc),
            extra={
                "victim_count": len(recent_victims),
                "top_groups": top_group_names,
            },
        )
    ]

    log.info("ransomwatch.fetched", count=1, victim_count=len(recent_victims), groups=len(groups))
    return items
