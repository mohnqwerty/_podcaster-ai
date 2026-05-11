"""Show notes generation: produce a clean markdown document."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from .config import get_settings
from .script import ScriptResult

log = structlog.get_logger(__name__)


def generate_shownotes(
    script: ScriptResult,
    brief: dict[str, Any],
    episode_date: datetime | None = None,
) -> str:
    """Generate markdown show notes from the script and research brief.

    Args:
        script: ScriptResult from the script stage.
        brief: Research brief dict (may contain "_items" key).
        episode_date: optional override for episode date (defaults to now).

    Returns:
        Markdown string ready for delivery.
    """
    settings = get_settings()
    if episode_date is None:
        episode_date = datetime.now(timezone.utc)

    date_str = episode_date.strftime("%Y-%m-%d")
    time_str = episode_date.strftime("%H:%M UTC")

    # Build the markdown document.
    lines: list[str] = []
    lines.append(f"# {script.title}")
    lines.append("")
    lines.append(f"**{script.tagline}**")
    lines.append("")
    lines.append(f"*Aired: {date_str} at {time_str}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary paragraph (from brief or fallback).
    summary = brief.get("tagline") or "Today's episode covers the latest in bug bounty and vulnerability research."
    lines.append(f"## Summary\n\n{summary}\n")

    # Segments from the brief.
    segments = brief.get("segments") or []
    if segments:
        lines.append("## Segments\n")
        for seg in segments:
            seg_name = seg.get("name") or "Untitled"
            seg_angle = seg.get("angle") or ""
            lines.append(f"### {seg_name}")
            if seg_angle:
                lines.append(f"\n{seg_angle}\n")
            items = seg.get("items") or []
            for item in items:
                headline = item.get("headline") or "Item"
                lines.append(f"- **{headline}**")
                facts = item.get("key_facts") or []
                for fact in facts:
                    lines.append(f"  - {fact}")
                urls = item.get("source_urls") or []
                if urls:
                    for url in urls:
                        lines.append(f"  - Source: {url}")
            lines.append("")

    # References / raw items (for transparency).
    raw_items = brief.get("_items") or []
    if raw_items:
        lines.append("## Full References\n")
        for item in raw_items:
            title = item.get("title") or "Untitled"
            url = item.get("url") or ""
            source = item.get("source") or "unknown"
            lines.append(f"- [{title}]({url}) — *{source}*")
        lines.append("")

    # Production notes.
    lines.append("## Production Notes\n")
    lines.append(f"- **Hosts**: {settings.host_maya_name} & {settings.host_arjun_name}")
    lines.append(f"- **Podcast**: {settings.podcast_title}")
    lines.append(f"- **Generated**: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- **Sources**: {len(raw_items)} items aggregated")
    lines.append("")

    # Disclaimer.
    lines.append("---\n")
    lines.append("*Disclaimer: This podcast aggregates publicly available security research. ")
    lines.append("Always verify information from original sources. The hosts and producers ")
    lines.append("are not liable for any use or misuse of information presented.*\n")

    result = "\n".join(lines)
    log.info(
        "shownotes.generated",
        title=script.title,
        segments=len(segments),
        items=len(raw_items),
    )
    return result
