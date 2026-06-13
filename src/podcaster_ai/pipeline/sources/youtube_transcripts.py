"""Fetch recent YouTube episode transcripts for configured channel IDs.

This source NEVER embeds raw audio. It only pulls the user-provided transcript
(via youtube-transcript-api) so the LLM stage can summarise discussion points
with attribution and a link back to the source video.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

import feedparser
import structlog
from feedparser import FeedParserDict

from ...config import get_settings
from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "youtube"
RSS_URL_FMT: Final[str] = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
MAX_TRANSCRIPT_CHARS: Final[int] = 6000


def _channel_videos(client, channel_id: str) -> list[FeedParserDict]:
    """Return parsed entries for a channel's public RSS feed."""
    try:
        resp = client.get(RSS_URL_FMT.format(cid=channel_id))
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        return list(parsed.entries or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("youtube.channel_failed", channel_id=channel_id, error=str(exc))
        return []


def _safe_transcript(video_id: str) -> str:
    """Try to fetch the transcript for a video. Return '' on any failure."""
    try:
        # Imported lazily so absence of the package never blocks other sources.
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore

        # The API surface differs across versions; handle both.
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id)  # type: ignore[attr-defined]
        except AttributeError:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id)  # type: ignore[attr-defined]

        # Each segment is either a dict {"text": ...} or an object with a `.text` attr.
        parts: list[str] = []
        for seg in transcript:
            text = (
                seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
            )
            if text:
                parts.append(str(text))
        joined = " ".join(p.strip() for p in parts if p.strip())
        if len(joined) > MAX_TRANSCRIPT_CHARS:
            joined = joined[:MAX_TRANSCRIPT_CHARS].rsplit(" ", 1)[0] + "…"
        return joined
    except Exception as exc:  # noqa: BLE001
        log.debug("youtube.transcript_failed", video_id=video_id, error=str(exc))
        return ""


def fetch() -> list[Item]:
    """Return recent YouTube items with transcripts. Fail-soft on any error."""
    settings = get_settings()
    channels = settings.youtube_channels()
    if not channels:
        log.info("youtube.disabled", reason="no channel ids configured")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=max(1, settings.youtube_lookback_days)
    )

    items: list[Item] = []
    try:
        with http_client() as client:
            for cid in channels:
                for entry in _channel_videos(client, cid):
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
                    summary = transcript or (
                        entry.get("summary") or entry.get("description") or ""
                    )
                    if not summary.strip():
                        continue

                    title = (entry.get("title") or "Untitled video").strip()
                    author = (entry.get("author") or "").strip()
                    items.append(
                        Item(
                            title=title,
                            url=f"https://www.youtube.com/watch?v={video_id}",
                            summary=summary.strip(),
                            source=SOURCE,
                            published_at=published,
                            extra={
                                "channel_id": cid,
                                "author": author,
                                "video_id": video_id,
                                "has_transcript": bool(transcript),
                            },
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        log.warning("youtube.fetch_failed", error=str(exc))
        return items

    log.info("youtube.fetched", count=len(items), channels=len(channels))
    return items
