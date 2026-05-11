"""Common types and helpers shared by all source fetchers."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

# Source weight defaults — used by the ranking stage.
SOURCE_WEIGHTS: dict[str, float] = {
    "portswigger": 1.0,
    "hackerone": 0.9,
    "projectdiscovery": 0.7,
    "nvd": 0.85,
    "cisa_kev": 1.0,
    "vendor_rss": 0.6,
    "youtube": 0.5,
}

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
DEFAULT_HEADERS = {
    "User-Agent": (
        "podcaster-ai/0.1 (+https://github.com/mohnqwerty/_podcaster-ai) "
        "research-fetcher"
    ),
    "Accept": "application/json, application/xml, text/xml, text/plain;q=0.9, */*;q=0.5",
}


@dataclass(slots=True)
class Item:
    """Normalised raw item produced by every source fetcher."""

    title: str
    url: str
    summary: str
    source: str
    published_at: Optional[datetime] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.published_at is not None:
            d["published_at"] = self.published_at.astimezone(timezone.utc).isoformat()
        return d


def http_client() -> httpx.Client:
    """Return a configured httpx.Client. Callers must close (use as context manager)."""
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
    )


def parse_dt(value: object) -> Optional[datetime]:
    """Best-effort datetime parsing. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    # Try a series of common formats.
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        # ISO 8601 (handle trailing "Z").
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        from dateutil import parser as du_parser  # type: ignore

        dt = du_parser.parse(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        log.debug("parse_dt.failed", value=s)
        return None
