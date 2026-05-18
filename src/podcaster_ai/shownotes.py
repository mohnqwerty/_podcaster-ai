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

    # Build the markdown document.
    lines: list[str] = []
    lines.append(f"# {script.title} — {date_str}")
    lines.append("")
    
    # Episode summary (2-3 sentences)
    summary = brief.get("tagline") or "Today's episode covers the latest in bug bounty and vulnerability research."
    lines.append(f"## Summary\n\n{summary}\n")

    # Topics covered (bullet list with source links)
    segments = brief.get("segments") or []
    if segments:
        lines.append("## Topics Covered\n")
        for seg in segments:
            items = seg.get("items") or []
            for item in items:
                headline = item.get("headline") or "Item"
                urls = item.get("source_urls") or []
                if urls:
                    lines.append(f"- **{headline}**: {', '.join(urls)}")
                else:
                    lines.append(f"- **{headline}**")
        lines.append("")

    # References & Rabbit Holes (from brief _items if available)
    raw_items = brief.get("_items") or []
    if raw_items:
        lines.append("## References & Rabbit Holes\n")
        for item in raw_items:
            title = item.get("title") or "Untitled"
            url = item.get("url") or ""
            if url:
                lines.append(f"- [{title}]({url})")
            else:
                lines.append(f"- {title}")
        lines.append("")

    # Full Transcript
    if script.dialogue:
        lines.append("## Full Transcript\n")
        lines.append(script.dialogue)
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
