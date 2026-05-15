"""Critical Thinking Bug Bounty podcast source.

Fetches latest episode metadata from RSS feed and YouTube transcript when available.
Capped to 1 latest episode per run.

Configuration (env):
- CT_BB_ENABLED      (bool, default false)
- CT_BB_RSS_URL      (str, default https://www.criticalthinkingpodcast.io/feed)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Final, Optional

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "critical_thinking"
DEFAULT_RSS_URL: Final[str] = "https://www.criticalthinkingpodcast.io/feed"


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean from environment variable."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _env_str(name: str, default: str = "") -> str:
    """Get string from environment variable."""
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip()


def _safe_transcript(video_id: str) -> str:
    """Try to fetch the transcript for a YouTube video. Return '' on any failure."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore

        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)  # type: ignore[attr-defined]
        except AttributeError:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id)  # type: ignore[attr-defined]

        parts: list[str] = []
        for seg in transcript:
            text = (
                seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
            )
            if text:
                parts.append(str(text))
        joined = " ".join(p.strip() for p in parts if p.strip())
        # Cap transcript length
        if len(joined) > 6000:
            joined = joined[:6000].rsplit(" ", 1)[0] + "…"
        return joined
    except Exception as exc:  # noqa: BLE001
        log.debug("critical_thinking.transcript_failed", video_id=video_id, error=str(exc))
        return ""


def fetch() -> list[Item]:
    """Return latest Critical Thinking podcast episode with transcript if available."""
    if not _env_bool("CT_BB_ENABLED"):
        log.info("critical_thinking.disabled", reason="CT_BB_ENABLED not set")
        return []

    rss_url = _env_str("CT_BB_RSS_URL", DEFAULT_RSS_URL) or DEFAULT_RSS_URL

    try:
        with http_client() as client:
            resp = client.get(rss_url)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("critical_thinking.fetch_failed", error=str(exc))
        return []

    items: list[Item] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    for entry in parsed.entries or []:
        try:
            published = parse_dt(entry.get("published") or entry.get("updated"))
            if published is None or published < cutoff:
                continue

            link = (entry.get("link") or "").strip()
            if not link:
                continue

            # Try to extract YouTube video ID from link
            video_id = ""
            if "youtube.com" in link or "youtu.be" in link:
                if "v=" in link:
                    video_id = link.rsplit("v=", 1)[-1].split("&", 1)[0]
                elif "youtu.be" in link:
                    video_id = link.rsplit("/", 1)[-1].split("?", 1)[0]

            transcript = ""
            if video_id:
                transcript = _safe_transcript(video_id)

            summary = transcript or (entry.get("summary") or entry.get("description") or "").strip()
            if not summary.strip():
                continue

            title = (entry.get("title") or "Untitled episode").strip()
            items.append(
                Item(
                    title=title,
                    url=link,
                    summary=summary.strip(),
                    source=SOURCE,
                    published_at=published,
                    extra={
                        "has_transcript": bool(transcript),
                        "video_id": video_id,
                    },
                )
            )
            # Cap to 1 latest episode per run
            if len(items) >= 1:
                break
        except Exception as exc:  # noqa: BLE001
            log.debug("critical_thinking.entry_skipped", error=str(exc))
            continue

    log.info("critical_thinking.fetched", count=len(items))
    return items
