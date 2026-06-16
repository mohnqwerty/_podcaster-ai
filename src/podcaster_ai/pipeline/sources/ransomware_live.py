"""ransomware.live API source.

The ransomware.live project (Julien Mousqueton) maintains a JSON
catalog of ransomware groups at api.ransomware.live/groups. The
recentVictims endpoint has been retired, but the groups catalog still
gives a current snapshot of who's active, what tools they use, and
what their leak sites look like.

The endpoint returns a list of groups, each with: name, description,
tools (list of tool/technique names), locations (list of leak site
URLs and onion addresses), and added_date.

For the elite-hacker goal: knowing which ransomware groups are
operationally active right now is the most actionable threat-intel
signal for any defender. The groups list is the canonical reference.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final

import structlog

from .base import Item, http_client

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "ransomware_live"
API_URL: Final[str] = "https://api.ransomware.live/groups"
MAX_ITEMS: Final[int] = 5


def _format_group(record: dict[str, Any]) -> Item | None:
    try:
        name = (record.get("name") or "Unknown group").strip()
        description = (record.get("description") or "").strip()
        tools = record.get("tools") or []
        locations = record.get("locations") or []
        # Find a non-onion URL (i.e. an actual reachable leak site mirror)
        url = "https://www.ransomware.live"
        for loc in locations:
            if isinstance(loc, dict):
                fqdn = (loc.get("fqdn") or "")
                if fqdn and ".onion" not in fqdn and fqdn.startswith("http"):
                    url = fqdn
                    break

        title = f"Active ransomware group: {name}"
        summary_parts: list[str] = []
        if description:
            summary_parts.append(description[:400])
        if tools:
            tools_str = ", ".join(str(t) for t in tools[:5])
            summary_parts.append(f"Tools: {tools_str}")
        summary = "\n".join(summary_parts) if summary_parts else name

        return Item(
            title=title,
            url=url,
            summary=summary,
            source=SOURCE,
            published_at=datetime.now(timezone.utc),
            extra={
                "group": name,
                "tools": tools,
                "location_count": len(locations),
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("ransomware_live.entry_skipped", error=str(exc))
        return None


def fetch() -> list[Item]:
    try:
        with http_client() as client:
            resp = client.get(API_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("ransomware_live.fetch_failed", error=str(exc))
        return []

    if not isinstance(data, list):
        log.warning("ransomware_live.unexpected_payload", type=type(data).__name__)
        return []

    # Pick the 5 most recently-added groups (highest operational tempo).
    sorted_data = sorted(
        data,
        key=lambda r: r.get("added_date") or "",
        reverse=True,
    )

    items: list[Item] = []
    for record in sorted_data:
        if len(items) >= MAX_ITEMS:
            break
        if not isinstance(record, dict):
            continue
        item = _format_group(record)
        if item is not None:
            items.append(item)

    log.info("ransomware_live.fetched", count=len(items), total=len(data))
    return items
