"""Show notes generation: clean markdown + PDF output."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from .config import get_settings
from .script import ScriptResult

log = structlog.get_logger(__name__)


def _format_item_source(item: dict[str, Any]) -> str:
    """Best-effort source label from an item's url or title."""
    url = (item.get("url") or "").strip()
    source = (item.get("source") or "").strip()
    if source:
        return source.replace("_", " ").title()
    return ""


def _clean_text(text: str, max_len: int = 200) -> str:
    """Strip excessive whitespace and truncate."""
    import re
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rsplit(" ", 1)[0] + "…"
    return cleaned


def generate_shownotes(
    script: ScriptResult,
    brief: dict[str, Any],
    episode_date: datetime | None = None,
) -> str:
    """Generate clean markdown show notes."""
    settings = get_settings()
    if episode_date is None:
        episode_date = datetime.now(timezone.utc)

    date_str = episode_date.strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append(f"# {script.title}")
    lines.append(f"*{date_str}*\n")

    # Summary
    summary = brief.get("tagline") or "Today's episode covers the latest in bug bounty and vulnerability research."
    lines.append("## Summary\n")
    lines.append(f"{summary}\n")

    # Topics Covered — grouped by segment
    segments = brief.get("segments") or []
    if segments:
        lines.append("## Topics Covered\n")
        for seg in segments:
            seg_name = seg.get("name") or "Highlights"
            lines.append(f"### {seg_name}\n")
            items = seg.get("items") or []
            for item in items:
                headline = item.get("headline") or "Item"
                key_facts = item.get("key_facts") or []
                urls = item.get("source_urls") or []
                key_point = _clean_text(key_facts[0], 160) if key_facts else ""
                source_label = f" — {_clean_text(urls[0], 80)}" if urls and urls[0] else ""
                lines.append(f"- **{headline}**{source_label}")
                if key_point:
                    lines.append(f"  > {key_point}")
                lines.append("")
        lines.append("")

    # References & Rabbit Holes (from brief _items)
    raw_items = brief.get("_items") or []
    if raw_items:
        lines.append("## References & Rabbit Holes\n")
        for item in raw_items:
            title = item.get("title") or "Untitled"
            url = item.get("url") or ""
            if url:
                lines.append(f"- [{title}]({url})")
            else:
                src = _format_item_source(item)
                lines.append(f"- **{title}** ({src})" if src else f"- {title}")
        lines.append("")

    # Full Transcript
    if script.dialogue:
        lines.append("## Full Transcript\n")
        lines.append(script.dialogue)
        lines.append("")

    # Disclaimer
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


def generate_pdf(
    script: ScriptResult,
    brief: dict[str, Any],
    output_path: Path,
    episode_date: datetime | None = None,
) -> Path:
    """Generate PDF show notes from the brief + script data."""
    try:
        from fpdf import FPDF
    except ImportError:
        log.warning("shownotes.pdf_missing_dep", detail="fpdf2 not installed, skipping PDF")
        return output_path.with_suffix(".md")

    settings = get_settings()
    if episode_date is None:
        episode_date = datetime.now(timezone.utc)

    date_str = episode_date.strftime("%Y-%m-%d")
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", uni=True)
    pdf.add_font("DejaVu", "B", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", uni=True)
    pdf.set_auto_page_break(auto=True, margin=20)

    # Title
    pdf.set_font("DejaVu", "B", 18)
    pdf.multi_cell(0, 10, script.title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, date_str, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_text_color(0, 0, 0)

    # Summary
    summary = brief.get("tagline") or "Today's episode covers the latest in bug bounty and vulnerability research."
    pdf.set_font("DejaVu", "B", 13)
    pdf.cell(0, 10, "Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    pdf.multi_cell(0, 6, summary, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Topics Covered
    segments = brief.get("segments") or []
    if segments:
        pdf.set_font("DejaVu", "B", 13)
        pdf.cell(0, 10, "Topics Covered", new_x="LMARGIN", new_y="NEXT")
        for seg in segments:
            seg_name = seg.get("name") or "Highlights"
            pdf.set_font("DejaVu", "B", 11)
            pdf.cell(0, 8, seg_name, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("DejaVu", "", 10)
            for item in seg.get("items") or []:
                headline = item.get("headline") or "Item"
                key_facts = item.get("key_facts") or []
                urls = item.get("source_urls") or []
                key_point = _clean_text(key_facts[0], 160) if key_facts else ""
                source_label = f" -- {_clean_text(urls[0], 80)}" if urls and urls[0] else ""
                pdf.multi_cell(0, 6, f"\u2022 {headline}{source_label}", new_x="LMARGIN", new_y="NEXT")
                if key_point:
                    pdf.set_font("DejaVu", "", 8)
                    pdf.set_text_color(80, 80, 80)
                    pdf.multi_cell(0, 5, f"   {key_point}", new_x="LMARGIN", new_y="NEXT")
                    pdf.set_text_color(0, 0, 0)
                    pdf.set_font("DejaVu", "", 10)
            pdf.ln(2)

    # References
    raw_items = brief.get("_items") or []
    if raw_items:
        pdf.set_font("DejaVu", "B", 13)
        pdf.cell(0, 10, "References & Rabbit Holes", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 9)
        for item in raw_items:
            title = item.get("title") or "Untitled"
            url = item.get("url") or ""
            clean_title = _clean_text(title, 100)
            if url:
                link_id = pdf.add_link(url=url)
                pdf.set_text_color(0, 0, 200)
                pdf.write(5, f"\u2022 {clean_title}", link=link_id)
                pdf.ln(5)
                pdf.set_text_color(100, 100, 100)
                pdf.set_font("DejaVu", "", 7)
                pdf.write(5, f"   {url}")
                pdf.ln(5)
                pdf.set_text_color(0, 0, 0)
                pdf.set_font("DejaVu", "", 9)
            else:
                src = _format_item_source(item)
                label = f"\u2022 {clean_title} ({src})" if src else f"\u2022 {clean_title}"
                pdf.multi_cell(0, 5, label, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    # Transcript excerpt
    if script.dialogue:
        preview_turns = 6
        pdf.set_font("DejaVu", "B", 13)
        pdf.cell(0, 10, "Full Transcript (excerpt)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 9)
        turn_count = 0
        for line in script.dialogue.splitlines():
            line = line.strip()
            if not line:
                continue
            if turn_count >= preview_turns:
                pdf.set_text_color(120, 120, 120)
                pdf.multi_cell(0, 5, "[... full transcript in audio episode ...]", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
                break
            pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
            turn_count += 1
        pdf.ln(4)

    # Disclaimer
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4, "Disclaimer: This podcast aggregates publicly available security research. Always verify information from original sources. The hosts and producers are not liable for any use or misuse of information presented.", new_x="LMARGIN", new_y="NEXT")

    pdf.output(str(output_path))
    log.info("shownotes.pdf_generated", path=str(output_path))
    return output_path
