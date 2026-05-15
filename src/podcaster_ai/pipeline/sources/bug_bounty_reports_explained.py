"""Bug Bounty Reports Explained (BBRE) YouTube channel source.

Fetches latest episode transcripts from Grzegorz Niedziela's YouTube channel.
Capped to 1 latest episode per run.

Configuration (env):
- BBRE_ENABLED      (bool, default false)
- BBRE_CHANNEL_ID   (str, default UCdWIQh9DGG6uhqHrqQv1jBQ)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "bbre"
DEFAULT_CHANNEL_ID: Final[str] = "UCdWIQh9DGG6uhqHrqQv1jBQ"
RSS_URL_FMT: Final[str] = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"


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
        log.debug("bbre.transcript_failed", video_id=video_id, error=str(exc))
        return ""


def fetch() -> list[Item]:
    """Return latest BBRE episode with transcript if available."""
    if not _env_bool("BBRE_ENABLED"):
        log.info("bbre.disabled", reason="BBRE_ENABLED not set")
        return []

    channel_id = _env_str("BBRE_CHANNEL_ID", DEFAULT_CHANNEL_ID) or DEFAULT_CHANNEL_ID
    rss_url = RSS_URL_FMT.format(cid=channel_id)

    items: list[Item] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    try:
        with http_client() as client:
            resp = client.get(rss_url)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("bbre.fetch_failed", error=str(exc))
        return []

    for entry in parsed.entries or []:
        try:
            published = parse_dt(entry.get("published") or entry.get("updated"))
            if published is None or published < cutoff:
                continue

            video_id = (entry.get("yt_videoid") or "").strip()
            if not video_id:
                link = (entry.get("link") or "").strip()
                if "v=" in link:
                    video_id = link.rsplit("v=", 1)[-1].split("&", 1)[0]
            if not video_id:
                continue

            transcript = _safe_transcript(video_id)
            summary = transcript or (entry.get("summary") or entry.get("description") or "").strip()
            if not summary.strip():
                continue

            title = (entry.get("title") or "Untitled video").strip()
            link = f"https://www.youtube.com/watch?v={video_id}"
            items.append(
                Item(
                    title=title,
                    url=link,
                    summary=summary.strip(),
                    source=SOURCE,
                    published_at=published,
                    extra={
                        "channel_id": channel_id,
                        "video_id": video_id,
                        "has_transcript": bool(transcript),
                    },
                )
            )
            # Cap to 1 latest episode per run
            if len(items) >= 1:
                break
        except Exception as exc:  # noqa: BLE001
            log.debug("bbre.entry_skipped", error=str(exc))
            continue

    log.info("bbre.fetched", count=len(items))
    return items
