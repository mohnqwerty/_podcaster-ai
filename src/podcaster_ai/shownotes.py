"""Show notes generation: markdown + PDF in daily.md format."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from .script import ScriptResult

log = structlog.get_logger(__name__)


def _clean_text(text: str, max_len: int = 600) -> str:
    """Strip excessive whitespace and truncate."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rsplit(" ", 1)[0] + "…"
    return cleaned


def _get_source_attribution(source: str) -> str:
    """Human-readable source name."""
    mapping = {
        "nvd": "NVD",
        "portswigger": "PortSwigger",
        "hackerone": "HackerOne",
        "projectdiscovery": "ProjectDiscovery",
        "cisa_kev": "CISA KEV",
        "krebs": "Krebs on Security",
        "threat_intel_news": "BleepingComputer / The Hacker News / SecurityWeek",
        "ai_security": "VentureBeat",
        "hardware_hacking": "Hackaday",
        "conferences": "InfoSec Conferences",
        "nitter": "X / Twitter (via Nitter)",
        "mastodon": "Mastodon",
        "vendor_rss": "Vendor RSS",
        "youtube": "YouTube",
    }
    return mapping.get(source, source.replace("_", " ").title())


def _citations_from_items(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Extract unique source attributions with citations."""
    seen: dict[str, str] = {}
    idx = 1
    citations: list[tuple[str, str]] = []
    for item in items:
        src = item.get("source") or ""
        if not src or src in seen:
            continue
        seen[src] = str(idx)
        label = _get_source_attribution(src)
        citations.append((str(idx), label))
        idx += 1
    return citations


def _source_citation_map(items: list[dict[str, Any]]) -> dict[str, str]:
    """Build source -> citation number map."""
    seen: dict[str, str] = {}
    idx = 1
    for item in items:
        src = item.get("source") or ""
        if src and src not in seen:
            seen[src] = str(idx)
            idx += 1
    return seen


def generate_shownotes(
    script: ScriptResult,
    brief: dict[str, Any],
    episode_date: datetime | None = None,
) -> str:
    """Generate markdown show notes in daily.md format."""
    if episode_date is None:
        episode_date = datetime.now(timezone.utc)

    date_str = episode_date.strftime("%B %d, %Y")
    lines: list[str] = []
    lines.append(f"# Daily Recon - {date_str}\n")

    # Episode Summary — tagline + one-line per segment
    tagline = brief.get("tagline") or ""
    segments = brief.get("segments") or []
    summary_parts = [tagline] if tagline else []
    for seg in segments:
        seg_items = seg.get("items") or []
        if seg_items:
            first = seg_items[0].get("headline") or ""
            summary_parts.append(f"The hosts also cover {first.lower()}" if first else "")
    summary = " ".join(p for p in summary_parts if p)
    if not summary:
        summary = "Today's episode covers the latest in bug bounty and vulnerability research."
    lines.append("## Episode Summary\n")
    lines.append(f"{summary}\n")

    # News and Analysis — segments as paragraphs with citation markers
    if segments:
        lines.append("## News and Analysis\n")
        # Build citation map from raw items
        raw_items = brief.get("_items") or []
        cit_map = _source_citation_map(raw_items)

        for seg in segments:
            seg_name = seg.get("name") or "Highlights"
            angle = seg.get("angle") or ""
            lines.append(f"### {seg_name}\n")

            # Opening sentence with angle
            para_parts: list[str] = []
            if angle:
                para_parts.append(angle)

            # Build item descriptions with citations
            for item in seg.get("items") or []:
                headline = item.get("headline") or ""
                key_facts = item.get("key_facts") or []
                key_point = _clean_text(key_facts[0], 250) if key_facts else ""

                # Find source citation
                item_citations: set[str] = set()
                for src_item in raw_items:
                    if src_item.get("title", "").strip() == headline.strip():
                        src = src_item.get("source", "")
                        if src in cit_map:
                            item_citations.add(f"[{cit_map[src]}]")
                cit_str = " ".join(sorted(item_citations, key=int))

                item_text = headline
                if key_point:
                    item_text += f", {key_point}"
                if cit_str:
                    item_text += f" {cit_str}"
                para_parts.append(item_text)

            if para_parts:
                lines.append(". ".join(para_parts) + ".\n")

    # References and Rabbit Holes
    if raw_items:
        lines.append("## References and Rabbit Holes\n")
        picks = ["Arjun", "Maya", "Arjun", "Maya"]
        for i, item in enumerate(raw_items[:8]):
            title = item.get("title") or "Untitled"
            url = item.get("url") or ""
            src = item.get("source") or ""
            clean_title = _clean_text(title, 100)
            pick = picks[i % len(picks)]
            if url:
                lines.append(
                    f"*   [{clean_title}]({url}) — {pick}'s pick: "
                    f"dig into the details from {_get_source_attribution(src)}."
                )
            else:
                lines.append(
                    f"*   **{clean_title}** — {pick}'s pick: "
                    f"follow up on this from {_get_source_attribution(src)}."
                )
        lines.append("")

    # Citations
    citations = _citations_from_items(raw_items)
    if citations:
        lines.append("## Citations\n")
        for num, label in citations:
            lines.append(f"[{num}] {label}")
        lines.append("")

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
    """Generate PDF shownotes in daily.md format."""
    try:
        from fpdf import FPDF
    except ImportError:
        log.warning("shownotes.pdf_missing_dep", detail="fpdf2 not installed, skipping PDF")
        return output_path.with_suffix(".md")

    if episode_date is None:
        episode_date = datetime.now(timezone.utc)

    date_str = episode_date.strftime("%B %d, %Y")
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", uni=True)
    pdf.add_font("DejaVu", "B", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", uni=True)
    pdf.set_auto_page_break(auto=True, margin=20)

    # Title
    pdf.set_font("DejaVu", "B", 16)
    pdf.multi_cell(0, 8, f"Daily Recon - {date_str}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Episode Summary
    segments = brief.get("segments") or []
    raw_items = brief.get("_items") or []
    cit_map = _source_citation_map(raw_items)

    tagline = brief.get("tagline") or ""
    summary_parts = [tagline] if tagline else []
    for seg in segments:
        seg_items = seg.get("items") or []
        if seg_items:
            first = seg_items[0].get("headline") or ""
            if first:
                summary_parts.append(f"The hosts also cover {first.lower()}")
    summary = ". ".join(p for p in summary_parts if p)
    if not summary:
        summary = "Today's episode covers the latest in bug bounty and vulnerability research."

    pdf.set_font("DejaVu", "B", 11)
    pdf.cell(0, 7, "Episode Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)
    pdf.multi_cell(0, 4.5, summary, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # News and Analysis
    if segments:
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 7, "News and Analysis", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        for seg in segments:
            seg_name = seg.get("name") or "Highlights"
            pdf.set_font("DejaVu", "B", 10)
            pdf.cell(0, 6, seg_name, new_x="LMARGIN", new_y="NEXT")

            para_parts: list[str] = []
            angle = seg.get("angle") or ""
            if angle:
                para_parts.append(angle)
            for item in seg.get("items") or []:
                headline = item.get("headline") or ""
                key_facts = item.get("key_facts") or []
                key_point = _clean_text(key_facts[0], 200) if key_facts else ""

                item_citations: set[str] = set()
                for src_item in raw_items:
                    if src_item.get("title", "").strip() == headline.strip():
                        src = src_item.get("source", "")
                        if src in cit_map:
                            item_citations.add(f"[{cit_map[src]}]")
                cit_str = " ".join(sorted(item_citations, key=int))

                item_text = headline
                if key_point:
                    item_text += f", {key_point}"
                if cit_str:
                    item_text += f" {cit_str}"
                para_parts.append(item_text)

            if para_parts:
                para = ". ".join(para_parts) + "."
                pdf.set_font("DejaVu", "", 8)
                pdf.multi_cell(0, 4, para, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

    # References and Rabbit Holes
    if raw_items:
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 7, "References and Rabbit Holes", new_x="LMARGIN", new_y="NEXT")
        picks = ["Arjun", "Maya", "Arjun", "Maya"]
        for i, item in enumerate(raw_items[:8]):
            title = item.get("title") or "Untitled"
            url = item.get("url") or ""
            src = item.get("source") or ""
            clean_title = _clean_text(title, 90)
            pick = picks[i % len(picks)]
            label = f"\u2022 {clean_title} — {pick}'s pick: dig into the details from {_get_source_attribution(src)}."
            if url:
                link_id = pdf.add_link(url=url)
                pdf.set_text_color(0, 0, 200)
                pdf.set_font("DejaVu", "", 8)
                pdf.write(4.5, label, link=link_id)
                pdf.ln(5)
                pdf.set_text_color(0, 0, 0)
            else:
                pdf.set_font("DejaVu", "", 8)
                pdf.multi_cell(0, 4.5, label, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Citations
    citations = _citations_from_items(raw_items)
    if citations:
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 7, "Citations", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 8)
        for num, label in citations:
            pdf.cell(0, 4, f"[{num}] {label}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    pdf.output(str(output_path))
    log.info("shownotes.pdf_generated", path=str(output_path))
    return output_path
