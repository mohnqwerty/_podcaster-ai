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
    ai_newsletters,
    ai_security_news,
    cert_in,
    cisco_talos,
    cisa_kev,
    concept_pool,
    conferences,
    cyberwire_daily,
    dark_reading,
    defcon_blackhat,
    dfir_report,
    exploit_db,
    github_advisories,
    hacker_news,
    hackerone_hacktivity,
    hak5,
    hardware_hacking,
    infosecurity_magazine,
    krebs_on_security,
    mastodon,
    microsoft_security,
    nitter_rss,
    nullcon,
    nvd_recent,
    owasp,
    portswigger_rss,
    project_zero,
    projectdiscovery_releases,
    reddit_netsec,
    the_hacker_news,
    threat_intel_news,
    trail_of_bits,
    vendor_rss,
    youtube_transcripts,
)
from .pipeline.sources.base import SOURCE_WEIGHTS

log = structlog.get_logger(__name__)

_URL_RE = re.compile(r"https?://\S+")
_CVSS_VECTOR_RE = re.compile(r"CVSS:\d+\.\d+/\S+")
_CWE_RE = re.compile(r"CWE-\d+", re.IGNORECASE)


_SOURCE_FAMILIES: list[tuple[str, set[str], str]] = [
    (
        "Critical CVEs and Advisories",
        {
            "nvd", "cisa_kev", "vendor_rss", "github_advisories",
            "microsoft_security", "cisco_talos", "cert_in",
        },
        "Newly disclosed or actively-exploited vulnerabilities from authoritative databases and vendor advisories.",
    ),
    (
        "Web and AI Security Research",
        {
            "portswigger", "owasp", "owasp_blog", "owasp_top10", "owasp_asvs",
            "owasp_cheatsheets", "owasp_wstg", "ai_security", "project_zero",
        },
        "Deep technical research on web vulnerabilities, AI/LLM security, and exploitation techniques.",
    ),
    (
        "Exploits, PoCs, and Writeups",
        {
            "exploit_db", "reddit_netsec", "reddit_bugbounty",
            "trail_of_bits",
        },
        "Public exploits, proof-of-concept code, and researcher writeups to read and learn from.",
    ),
    (
        "Threat Intelligence and Incidents",
        {
            "krebs", "the_hacker_news", "dark_reading", "infosecurity_magazine",
            "threat_intel_news", "cyberwire_daily", "dfir_report",
            "mastodon", "nitter", "hackerone",
        },
        "Active threat actor activity, breach analysis, and industry context.",
    ),
    (
        "Tools, Conferences, and Community",
        {
            "projectdiscovery", "conferences", "darknet_diaries",
            "risky_business", "critical_thinking", "bbre", "youtube",
            "hardware_hacking",
        },
        "New offensive-security tools, upcoming conferences, podcast episodes, and community knowledge.",
    ),
]


def _family_for(source: str) -> tuple[str, str] | None:
    """Return (family_name, family_angle) for a source key, or None."""
    for name, members, angle in _SOURCE_FAMILIES:
        if source in members:
            return (name, angle)
    return None


def _categorize_fallback_brief(short_list: list[Item]) -> list[dict[str, Any]]:
    """Group items into categorized segments when the LLM brief is unavailable.

    Each segment groups items by source family, with the family description as
    the angle. Items within a segment keep their source URL so the shownotes
    stage can hyperlink each headline.
    """
    buckets: dict[str, dict[str, Any]] = {}
    orphan_items: list[Item] = []
    for it in short_list:
        fam = _family_for(it.source)
        if fam is None:
            orphan_items.append(it)
            continue
        name, angle = fam
        bucket = buckets.setdefault(name, {"name": name, "angle": angle, "items": []})
        key_facts: list[str] = []
        if it.summary:
            cleaned = re.sub(r"###\s*Summary\s*", "", it.summary, flags=re.IGNORECASE)
            cleaned = re.sub(r"##\s*Summary\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                key_facts.append(cleaned[:400])
        cwe = (it.extra or {}).get("cwe") if it.extra else None
        item_dict: dict[str, Any] = {
            "headline": it.title,
            "key_facts": key_facts,
            "source_urls": [it.url] if it.url else [],
            "_source": it.source,
        }
        if cwe:
            item_dict["cwe"] = cwe
        bucket["items"].append(item_dict)

    segments: list[dict[str, Any]] = []
    for name, _, _ in _SOURCE_FAMILIES:
        bucket = buckets.get(name)
        if not bucket or not bucket["items"]:
            continue
        bucket["items"].sort(key=lambda d: d.get("headline", ""))
        segments.append(bucket)

    if orphan_items:
        segments.append(
            {
                "name": "Other Coverage",
                "angle": "Additional items from the day's sources.",
                "items": [
                    {
                        "headline": it.title,
                        "key_facts": [
                            re.sub(r"\s+", " ", it.summary).strip()[:400]
                        ] if it.summary else [],
                        "source_urls": [it.url] if it.url else [],
                        "_source": it.source,
                        **(
                            {"cwe": (it.extra or {}).get("cwe")}
                            if (it.extra or {}).get("cwe")
                            else {}
                        ),
                    }
                    for it in orphan_items
                ],
            }
        )

    return segments


def _strip_urls(text: str) -> str:
    return re.sub(r"\s+", " ", _URL_RE.sub("", text)).strip()


def _strip_cvss_vector(text: str) -> str:
    return re.sub(r"\s+", " ", _CVSS_VECTOR_RE.sub("", text)).strip()


SYSTEM_PROMPT = """You are the senior researcher for a daily two-host bug-bounty
podcast called "Daily Recon". Your job is to turn a list of raw items (titles,
summaries, metadata) into a tight research brief for the writers.

PODCAST GOAL: Help listeners become elite security researchers. The
listener is not just hearing news — they are building a tradecraft
foundation. Every brief item should answer: "what is the technique or
weakness class here, and how would I practice finding it?"

Hard rules:
- Use ONLY the provided items. Do NOT invent CVEs, vendors, exploits, quotes,
  numbers, or names. If a fact is not in the source material, omit it.
- Every claim in your brief must be traceable to one of the supplied source URLs.
- CROSS-REFERENCE: When an item appears in multiple sources (e.g., a CVE on
  NVD AND a PortSwigger article AND a Mastodon post), flag it as corroborated.
  Prefer the authoritative source (NVD, vendor advisory, CISA KEV) over
  social-media or news-aggregator takes for factual claims.
- Include the source URL in each item's source_urls field so the script writers
  can reference them in show notes. URLs are critical for the shownotes references section.
- Prefer concrete, actionable detail (CVE IDs with CVSS score, affected versions,
  vendor advisories, KEV due dates) over generic commentary. Keep CVE descriptions
  short — just the key facts, not the full advisory text.
- CVSS scores: only mention the numeric score briefly (e.g. "CVSS 9.8").
  Do NOT read CVSS vector strings or verbose scoring details.
- Group items into 5–8 thematic segments — more segments means richer content.
  Aim for depth, not breadth: include key facts, exploitability context, and
  real-world impact for each item.
- MANDATORY SECTIONS (if data exists):
  1. AI Security (focus on LLM vulnerabilities, ATLAS, and model bypasses)
  2. Hardware Hacking (focus on firmware, side-channels, and physical bypasses)
   3. Conferences & Events (prioritise India & Asia — BSides, Nullcon, VULNCON, CIACON, etc.)
  4. Podcasts & Research (Darknet Diaries, Critical Thinking Bug Bounty, etc.)

SOURCE-FAMILY COVERAGE: A brief MUST contain at least one segment per
source family that returned 3+ items. The five families are:
- "Critical CVEs and Advisories" (NVD, CISA KEV, GitHub Advisories,
  Microsoft Security, Cisco Talos, CERT-In, vendor RSS)
- "Web and AI Security Research" (PortSwigger, OWASP, AI security,
  Project Zero)
- "Exploits, PoCs, and Writeups" (Exploit-DB, Reddit r/netsec,
  Reddit r/bugbounty, Trail of Bits)
- "Threat Intelligence and Incidents" (Krebs, The Hacker News,
  Dark Reading, Infosecurity Magazine, BleepingComputer, CyberWire
  Daily, DFIR Report, RansomWatch, Mastodon, Nitter, HackerOne)
- "Tools, Conferences, and Community" (ProjectDiscovery, conferences,
  Darknet Diaries, Risky Business, Critical Thinking, BBRE, YouTube,
  Hardware Hacking)
If a family returned <3 items, OMIT it (do not invent a placeholder).
If a family returned 3+ items, you MUST include at least one segment
that covers them — even if you have to write 8 segments instead of 5.

CONCEPT OF THE DAY: The brief contains exactly one item with
source="concept_of_the_day". This MUST become its own segment named
"Concept of the Day" (or very similar — "Builder's Corner", "Concept
Explainer"). Do NOT fold it into AI Security, Web Security, or any
other segment. The listener expects a dedicated slot for the concept
explainer, distinct from the news.

The concept's talking_points (in its summary) and build_it pointer
are the two ingredients the script uses. Preserve both verbatim in
the segment's items.key_facts so the script writer doesn't have to
re-derive them.

CWE CLASS EXTRACTION: For every CVE discussed, include the CWE class
in the headline when the source material makes it identifiable. The
preferred format is:

    "CVE-2026-10520 — OS Command Injection in Ivanti Sentry (CWE-78, CVSS 10.0)"

If the CWE is not explicit in the source (and the source item's
extra.cwe field is empty), OMIT it rather than guess. NEVER make up
a CWE number. Set the "cwe" field on the item to the CWE id string
(e.g. "CWE-78") only when you are confident.

ELITE-HACKER FRAMING (for every CVE / exploit / technique discussed):
- Identify the CWE class (e.g., CWE-78 OS Command Injection, CWE-89 SQL Injection,
  CWE-79 XSS, CWE-22 Path Traversal, CWE-502 Deserialization, CWE-918 SSRF,
  CWE-287 Auth Bypass). If the CWE is not explicit in the source material, OMIT
  it rather than guess.
- State the technique / weakness class in PLAIN ENGLISH in 1 sentence.
- For every CVE, note the affected product + version range + exploitation
  prerequisites (auth required? user interaction? network reachable?).
- If the source item is a public exploit / PoC (Exploit-DB, GHSA PoC url, or
  Reddit writeup), call this out — readers can study the code.
- If the item is a researcher writeup (Reddit r/netsec, PortSwigger research,
  Trail of Bits blog, Project Zero), surface the technique the researcher
  used — that's the learning signal.
- If the item is from OWASP, surface which project (Top 10, ASVS, Cheat
  Sheet, WSTG) so listeners can deep-link to the relevant section.

LEARNING RESOURCES — when the brief covers a technique, mention the right
free practice environment:
- Web vulnerabilities (XSS, SQLi, SSRF, IDOR, deserialization, auth bypass):
  PortSwigger Web Security Academy (portswigger.net/web-security) — has free
  labs covering every CWE class.
- General bug bounty methodology: HackTheBox, TryHackMe, OWASP WebGoat,
  DVWA, bWAPP.
- Privilege escalation / post-exploitation: HackTheBox, PicoCTF, OverTheWire
  wargames.
- Reverse engineering / binary exploitation: CrackMes, pwnable, ROP Emporium.
- Forensics / DFIR: CyberDefenders, Blue Team Labs Online.
- Capture the flag in general: CTFtime.org for upcoming events + writeups.
Only mention a resource when it's actually relevant to the technique in the
brief — never stuff a generic reading list into every segment.

- Skip items that are duplicates or too thin to discuss.
- Keep the tone analytical, not breathless.
- Mastodon items are Tier 3 leads — must be cross-checked against authoritative
  sources (NVD, vendor advisories, PortSwigger, CISA KEV, etc.) before being
  asserted as fact. Always cite the original Mastodon URL in show notes so
  listeners can verify independently.

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
          "source_urls": ["https://..."],
          "cwe": "CWE-NN"   // optional, only when source material confirms it
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
        ("krebs", krebs_on_security.fetch),
        ("threat_intel_news", threat_intel_news.fetch),
        ("nitter", nitter_rss.fetch),
        ("dfir_report", dfir_report.fetch),
        ("mastodon", mastodon.fetch),
        ("the_hacker_news", the_hacker_news.fetch),
        ("dark_reading", dark_reading.fetch),
        ("infosecurity_magazine", infosecurity_magazine.fetch),
        ("cisco_talos", cisco_talos.fetch),
        ("microsoft_security", microsoft_security.fetch),
        ("project_zero", project_zero.fetch),
        ("trail_of_bits", trail_of_bits.fetch),
        ("cert_in", cert_in.fetch),
        ("cyberwire_daily", cyberwire_daily.fetch),
        ("reddit_netsec", reddit_netsec.fetch),
        ("owasp", owasp.fetch),
        ("exploit_db", exploit_db.fetch),
        ("github_advisories", github_advisories.fetch),
        ("hak5", hak5.fetch),
        ("defcon_blackhat", defcon_blackhat.fetch),
        ("ai_newsletters", ai_newsletters.fetch),
        ("nullcon", nullcon.fetch),
        ("hacker_news", hacker_news.fetch),
        ("concept_of_the_day", concept_pool.fetch),
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

    # Concept-of-the-day is always included first so it's never lost
    # to the cap, regardless of how full the brief is.
    for item in _rank(items):
        if (item.extra or {}).get("is_concept"):
            seen.add(_dedupe_key(item))
            by_source[item.source] = 1
            out.append(item)
            break

    for item in _rank(items):
        if (item.extra or {}).get("is_concept"):
            continue  # already added above
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
    def _sanitise_extra(extra: dict[str, object]) -> dict[str, object]:
        if extra is None:
            return {}
        sanitised = {}
        for k, v in extra.items():
            if k in ("cvss", "cve_id"):
                sanitised[k] = v
        return sanitised

    def _serialise_extra(extra: dict[str, object]) -> dict[str, object]:
        if extra is None:
            return {}
        out: dict[str, object] = {}
        for k, v in extra.items():
            if k in ("cvss", "cve_id", "cwe", "is_concept", "bucket"):
                out[k] = v
        return out

    def _summary_cap(it: Item) -> int:
        # Concept items carry talking points + a build-it pointer; let
        # them through at full length so the LLM has the teaching
        # skeleton available. News items stay at 200 to keep TPM low.
        if (it.extra or {}).get("is_concept"):
            return 2500
        return 200

    serialised = [
        {
            "title": _strip_cvss_vector(it.title[:150]),
            "url": it.url or "",
            "summary": _strip_cvss_vector(it.summary[: _summary_cap(it)]),
            "source": it.source,
            "published_at": it.published_at.astimezone(timezone.utc).isoformat()
            if it.published_at
            else None,
            "extra": _serialise_extra(it.extra or {}),
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

    settings = get_settings()
    chat_kwargs: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ],
        "json_mode": True,
    }
    if settings.research_llm_model:
        chat_kwargs["model"] = settings.research_llm_model
    try:
        raw = chat(**chat_kwargs)
        brief = _safe_json_parse(raw)
    except Exception as exc:  # noqa: BLE001
        # Self-correct: if the LLM rejected the prompt for being too large
        # (Groq returns 413, OpenAI returns 400 with 'context_length_exceeded'),
        # halve the item list and retry once before giving up.
        if "413" in str(exc) or "context_length" in str(exc).lower() or "too large" in str(exc).lower():
            log.warning("research.llm_prompt_too_large", items=len(short_list))
            trimmed = short_list[: max(10, len(short_list) // 2)]
            chat_kwargs["messages"][1]["content"] = (
                "Build the research brief from these raw items.\n\n"
                f"```json\n{_items_to_prompt(trimmed)}\n```"
            )
            try:
                raw = chat(**chat_kwargs)
                brief = _safe_json_parse(raw)
                log.info("research.llm_retry_succeeded", items=len(trimmed))
                short_list = trimmed
            except Exception as exc2:  # noqa: BLE001
                log.warning("research.llm_retry_failed", error=str(exc2))
                brief = None
        else:
            brief = None
        if brief is None:
            log.warning("research.llm_failed", error=str(exc))
            # Last-resort fallback: a categorised brief built directly from items
            # so downstream stages still produce a structured, linkable episode.
            brief = {
                "title": "Daily Recon — fallback brief",
                "tagline": "LLM brief unavailable; here's the day's coverage, grouped by source family.",
                "segments": _categorize_fallback_brief(short_list),
                "closing_notes": "Fallback brief — verify everything yourself.",
            }

    brief["_items"] = [it.to_dict() for it in short_list]
    log.info(
        "research.brief_ready",
        segments=len(brief.get("segments") or []),
        title=brief.get("title"),
    )
    return brief
