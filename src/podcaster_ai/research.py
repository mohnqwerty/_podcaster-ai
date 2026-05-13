"""Research stage: gather → dedupe → rank → build a structured research brief."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import structlog

from .config import get_settings
from .llm import chat
from .pipeline.sources import (
    Item,
)
from .pipeline.sources import (
    ai_security_news,
    cisa_kev,
    conferences,
    hackerone_hacktivity,
    hardware_hacking,
    mastodon,
    nvd_recent,
    portswigger_rss,
    projectdiscovery_releases,
    vendor_rss,
    youtube_transcripts,
)
from .pipeline.sources.base import SOURCE_WEIGHTS

log = structlog.get_logger(__name__)


SYSTEM_PROMPT = """You are the senior researcher for a daily two-host bug-bounty
podcast called "Daily Recon". Your job is to turn a list of raw items (titles,
URLs, summaries, metadata) into a tight research brief for the writers.

Hard rules:
- Use ONLY the provided items. Do NOT invent CVEs, vendors, exploits, quotes,
  numbers, or names. If a fact is not in the source material, omit it.
- Every claim in your brief must be traceable to one of the supplied source URLs.
- Prefer concrete, actionable detail (CVE IDs with CVSS, affected versions,
  vendor advisories, KEV due dates) over generic commentary.
- Group items into 3–5 thematic segments.
- MANDATORY SECTIONS (if data exists):
  1. AI Security (focus on LLM vulnerabilities, ATLAS, and model bypasses)
  2. Hardware Hacking (focus on firmware, side-channels, and physical bypasses)
  3. Conferences & Events (Black Hat, DEF CON, Hack.lu, etc.)
- Skip items that are duplicates or too thin to discuss.
- Keep the tone analytical, not breathless.
- Mastodon items are Tier 3 leads — must be cross-checked against authoritative
  sources (NVD, vendor advisories, PortSwigger, CISA KEV, etc.) before being
  asserted as fact. Always cite the original Mastodon URL in show notes.

Output STRICT JSON matching this shape:
{
  "title": "string — episode title",
  "tagline": "string — one-line tease",
  "segments": [
    {
      "name": "string — segment heading",
      "angle": "string — why this matters today",
      "items": [
        {
          "headline": "string",
          "key_facts": ["string", ...],
          "source_urls": ["https://..."]
        }
      ]
    }
  ],
  "closing_notes": "string — short sign-off / reminder"
}
"""


def _gather_all() -> list[Item]:
    """Run every fetcher and collect their items. Each fetcher is fail-soft."""
    fetchers = [
        ("portswigger", portswigger_rss.fetch),
        ("hackerone", hackerone_hacktivity.fetch),
        ("projectdiscovery", projectdiscovery_releases.fetch),
        ("nvd", nvd_recent.fetch),
        ("cisa_kev", cisa_kev.fetch),
        ("vendor_rss", vendor_rss.fetch),
        ("youtube", youtube_transcripts.fetch),
        ("ai_security", ai_security_news.fetch),
        ("hardware_hacking", hardware_hacking.fetch),
        ("conferences", conferences.fetch),
        ("mastodon", mastodon.fetch),
    ]
    out: list[Item] = []
    for name, fn in fetchers:
        try:
            items = fn() or []
        except Exception as exc:  # noqa: BLE001 — defensive belt-and-braces
            log.warning("research.source_failed", source=name, error=str(exc))
            items = []
        out.extend(items)
    return out


_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)


def _dedupe_key(item: Item) -> str:
    """Best-effort dedupe — prefer CVE id, fallback to URL, then title."""
    blob = f"{item.title}\n{item.summary}"
    cve = _CVE_RE.search(blob)
    if cve:
        return cve.group(0).upper()
    if item.url:
        return item.url.strip().lower()
    return item.title.strip().lower()


def _recency_score(item: Item, now: datetime) -> float:
    if item.published_at is None:
        return 0.2
    age_h = max(0.0, (now - item.published_at.astimezone(timezone.utc)).total_seconds() / 3600.0)
    # Simple decay: 1.0 at 0h, ~0.37 at 72h, ~0.05 at 168h.
    if age_h <= 0:
        return 1.0
    if age_h >= 168:
        return 0.05
    import math

    return max(0.05, math.exp(-age_h / 72.0))


def _cvss_score(item: Item) -> float:
    cvss = item.extra.get("cvss") if item.extra else None
    if cvss is None:
        return 0.0
    try:
        return min(10.0, float(cvss)) / 10.0
    except (TypeError, ValueError):
        return 0.0


def _rank(items: Iterable[Item]) -> list[Item]:
    now = datetime.now(timezone.utc)

    def score(it: Item) -> float:
        weight = SOURCE_WEIGHTS.get(it.source, 0.5)
        return (
            (1.5 * weight)
            + (1.0 * _recency_score(it, now))
            + (1.2 * _cvss_score(it))
        )

    return sorted(items, key=score, reverse=True)


def _dedupe_and_cap(items: list[Item]) -> list[Item]:
    settings = get_settings()
    seen: set[str] = set()
    by_source: dict[str, int] = {}
    out: list[Item] = []

    for item in _rank(items):
        key = _dedupe_key(item)
        if key in seen:
            continue
        cap = settings.max_items_per_source
        if by_source.get(item.source, 0) >= cap:
            continue
        seen.add(key)
        by_source[item.source] = by_source.get(item.source, 0) + 1
        out.append(item)
        if len(out) >= settings.max_total_items:
            break

    log.info(
        "research.deduped",
        kept=len(out),
        by_source={k: v for k, v in by_source.items()},
    )
    return out


def _items_to_prompt(items: list[Item]) -> str:
    serialised = [
        {
            "title": it.title,
            "url": it.url,
            "summary": it.summary[:1500],
            "source": it.source,
            "published_at": it.published_at.astimezone(timezone.utc).isoformat()
            if it.published_at
            else None,
            "extra": it.extra or {},
        }
        for it in items
    ]
    return json.dumps(serialised, ensure_ascii=False, indent=2)


def _safe_json_parse(text: str) -> dict[str, Any]:
    """Parse JSON, tolerating ```json fences and stray prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    # Find the outermost JSON object.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM did not return a JSON object")
    return json.loads(cleaned[start : end + 1])


def build_research_brief() -> dict[str, Any]:
    """Return the full structured research brief used by the script stage."""
    items = _gather_all()
    log.info("research.gathered", total=len(items))
    if not items:
        log.warning("research.no_items")
        return {
            "title": "Daily Recon — quiet day",
            "tagline": "No fresh sources reached us today.",
            "segments": [],
            "closing_notes": "Pipeline ran cleanly but every source returned empty.",
            "_items": [],
        }

    short_list = _dedupe_and_cap(items)
    user_payload = (
        "Build the research brief from these raw items.\n\n"
        f"```json\n{_items_to_prompt(short_list)}\n```"
    )

    try:
        raw = chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_payload},
            ],
            json_mode=True,
        )
        brief = _safe_json_parse(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("research.llm_failed", error=str(exc))
        # Last-resort fallback: a degenerate brief built directly from items
        # so downstream stages still produce *something* for inspection.
        brief = {
            "title": "Daily Recon — fallback brief",
            "tagline": "LLM brief unavailable; using raw items.",
            "segments": [
                {
                    "name": "Raw highlights",
                    "angle": "Direct from sources, no synthesis.",
                    "items": [
                        {
                            "headline": it.title,
                            "key_facts": [it.summary[:280]],
                            "source_urls": [it.url] if it.url else [],
                        }
                        for it in short_list[:10]
                    ],
                }
            ],
            "closing_notes": "Fallback brief — verify everything yourself.",
        }

    brief["_items"] = [it.to_dict() for it in short_list]
    log.info(
        "research.brief_ready",
        segments=len(brief.get("segments") or []),
        title=brief.get("title"),
    )
    return brief
