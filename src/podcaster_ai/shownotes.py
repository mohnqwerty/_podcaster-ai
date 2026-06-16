"""Show notes generation: markdown + PDF in daily.md format."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

# Top-25 MITRE CWE names used to render CWE tags inline with CVEs.
# If a CVE's CWE isn't here, we just render the id (e.g. "CWE-1395") with
# no parenthetical name, rather than fabricating one.
_CWE_NAMES: dict[str, str] = {
    "CWE-22": "Path Traversal",
    "CWE-78": "OS Command Injection",
    "CWE-79": "Cross-site Scripting (XSS)",
    "CWE-89": "SQL Injection",
    "CWE-94": "Code Injection",
    "CWE-119": "Buffer Overflow",
    "CWE-125": "Out-of-bounds Read",
    "CWE-190": "Integer Overflow",
    "CWE-200": "Information Exposure",
    "CWE-269": "Improper Privilege Management",
    "CWE-287": "Improper Authentication",
    "CWE-295": "Improper Certificate Validation",
    "CWE-319": "Cleartext Transmission",
    "CWE-352": "CSRF",
    "CWE-400": "Uncontrolled Resource Consumption",
    "CWE-416": "Use After Free",
    "CWE-434": "Unrestricted File Upload",
    "CWE-476": "NULL Pointer Dereference",
    "CWE-502": "Deserialization",
    "CWE-611": "XXE",
    "CWE-787": "Out-of-bounds Write",
    "CWE-918": "SSRF",
    "CWE-1021": "Clickjacking",
    "CWE-1333": "Regex DoS",
    "CWE-1395": "Dependency on Vulnerable Component",
}


def _format_cwe(cwe: str | None) -> str:
    """Render a CWE id as 'CWE-78 (OS Command Injection)' or just 'CWE-1395'."""
    if not cwe:
        return ""
    name = _CWE_NAMES.get(cwe.upper())
    return f"{cwe} ({name})" if name else cwe

from .script import ScriptResult

log = structlog.get_logger(__name__)


def _clean_text(text: str, max_len: int = 600) -> str:
    """Strip markdown noise (### Summary etc), excessive whitespace, and truncate."""
    cleaned = re.sub(r"#{1,6}\s*Summary\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"#{1,6}\s*Description\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"#{1,6}\s*Details\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<br\s*/?>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
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
        "the_hacker_news": "The Hacker News",
        "dark_reading": "Dark Reading",
        "infosecurity_magazine": "Infosecurity Magazine",
        "cisco_talos": "Cisco Talos",
        "microsoft_security": "Microsoft Security",
        "project_zero": "Google Project Zero",
        "trail_of_bits": "Trail of Bits",
        "cert_in": "CERT-In",
        "cyberwire_daily": "CyberWire Daily",
        "reddit_netsec": "Reddit r/netsec",
        "reddit_bugbounty": "Reddit r/bugbounty",
        "owasp": "OWASP",
        "owasp_blog": "OWASP Blog",
        "owasp_top10": "OWASP Top 10",
        "owasp_asvs": "OWASP ASVS",
        "owasp_cheatsheets": "OWASP Cheat Sheets",
        "owasp_wstg": "OWASP WSTG",
        "exploit_db": "Exploit-DB",
        "github_advisories": "GitHub Security Advisories",
        "darknet_diaries": "Darknet Diaries",
        "risky_business": "Risky Business",
        "critical_thinking": "Critical Thinking (Bug Bounty Podcast)",
        "bbre": "Bug Bounty Reports Explained",
        "dfir_report": "The DFIR Report",
        "hak5": "Hak5",
        "defcon": "DEF CON",
        "blackhat": "Black Hat",
        "conferences_youtube": "DEF CON / Black Hat",
        "ai_newsletters": "AI Newsletters (Latent Space / Interconnects / Ben's Bites / swyx)",
        "latent_space": "Latent Space",
        "interconnects": "Interconnects (Nathan Lambert)",
        "bens_bites": "Ben's Bites",
        "swyx": "swyx.io (AI Engineer)",
        "nullcon": "Nullcon (India)",
        "hacker_news": "Hacker News",
        "concept_of_the_day": "Concept of the Day (curated)",
        "the_record": "The Record (Recorded Future News)",
        "cisa_advisories": "BleepingComputer (ransomware coverage)",
        "ransomware_live": "ransomware.live (active group catalog)",
        "abusech_ransomware": "abuse.ch Cybercrime Feed",
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


def _render_segment(
    lines: list[str],
    *,
    level: int,
    name: str,
    angle: str,
    items: list[dict[str, Any]],
    raw_items: list[dict[str, Any]],
    cit_map: dict[str, str],
    is_concept: bool = False,
) -> None:
    """Render one segment (top-level or sub-segment) to the markdown lines.

    level=2 produces `## ` (top-level section), level=3 produces `### `
    (sub-section of News and Analysis). is_concept=True adds the
    "Today's concept" callout above the header.
    """
    if is_concept:
        lines.append("> **Today's concept** — sit back, this is the educational anchor of the episode.\n")

    header = "##" if level == 2 else "###"
    lines.append(f"{header} {name}\n")

    para_parts: list[str] = []
    if angle:
        para_parts.append(angle)

    for item in items:
        headline = item.get("headline") or ""
        key_facts = item.get("key_facts") or []
        key_point_raw = key_facts[0] if key_facts else ""
        key_point = _clean_text(key_point_raw, 280) if key_point_raw else ""

        # Find source citation
        item_citations: set[int] = set()
        for src_item in raw_items:
            if src_item.get("title", "").strip() == headline.strip():
                src = src_item.get("source", "")
                if src in cit_map:
                    item_citations.add(int(cit_map[src]))
        cit_str = " ".join(f"[{n}]" for n in sorted(item_citations))

        # Resolve a clickable URL: prefer source_urls from the segment
        # item, fall back to raw_items by title match.
        item_url: str = ""
        for url in item.get("source_urls") or []:
            if url:
                item_url = url
                break
        if not item_url:
            for src_item in raw_items:
                if src_item.get("title", "").strip() == headline.strip():
                    cand = src_item.get("url") or ""
                    if cand:
                        item_url = cand
                        break

        # Notable badge: items flagged is_notable=True by the
        # NVD source (vendor whitelist match or CVSS >= 9.5) get a
        # leading star so listeners can scan for the widely-used-software
        # CVEs at a glance.
        is_notable = bool((item.get("extra") or {}).get("is_notable"))

        if item_url:
            linked_headline = f"[{headline}]({item_url})"
        else:
            linked_headline = headline

        # CWE inline tag.
        cwe_str = _format_cwe(item.get("cwe"))
        if cwe_str:
            linked_headline = f"{linked_headline} · {cwe_str}"

        # Prepend the star for notable items.
        if is_notable:
            linked_headline = f"★ {linked_headline}"

        if key_point:
            if item_url:
                item_text = f"{linked_headline} — {key_point}"
            else:
                item_text = f"{linked_headline}, {key_point}"
        else:
            item_text = linked_headline
        if cit_str:
            item_text += f" {cit_str}"
        para_parts.append(item_text)

    if para_parts:
        para = " ".join(p.rstrip(".") for p in para_parts).rstrip(".") + ".\n"
        lines.append(para)


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
            if first:
                trimmed = first if len(first) <= 120 else first[:117] + "…"
                summary_parts.append(f"The hosts also cover {trimmed}")
    summary = " ".join(p for p in summary_parts if p)
    if not summary:
        summary = "Today's episode covers the latest in bug bounty and vulnerability research."
    lines.append("## Episode Summary\n")
    lines.append(f"{summary}\n")

    # News and Analysis — segments as paragraphs with citation markers
    raw_items = brief.get("_items") or []
    cit_map = _source_citation_map(raw_items)

    # Partition segments by name. The LLM must use verbatim names from
    # the research.py system prompt, but a defensive partition ensures
    # the shownotes layout is stable even if the LLM deviates.
    concept_seg = next(
        (s for s in segments
         if (s.get("name") or "").lower().startswith("concept of the day")
         or (s.get("name") or "").lower() in {"builder's corner", "concept explainer"}),
        None,
    )
    family_segs = [
        s for s in segments
        if s is not concept_seg
        and not (s.get("name") or "").lower().startswith("def con & black hat")
    ]
    conf_talks_seg = next(
        (s for s in segments if (s.get("name") or "").lower().startswith("def con & black hat")),
        None,
    )
    # Also pull DEF CON / Black Hat items out of the family segments
    # (the LLM may have bundled them into "Tools, Conferences, and
    # Community" instead of giving them their own segment).
    if conf_talks_seg is None:
        talks_items = [
            it
            for s in family_segs
            for it in s.get("items") or []
            if (it.get("source") or it.get("_source") or "").lower() in {"defcon", "blackhat", "conferences_youtube"}
        ]
        if talks_items:
            conf_talks_seg = {
                "name": "DEF CON & Black Hat Talks",
                "angle": "Conference talks and announcements from DEF CON and Black Hat. Watch the deep-dive research presentations.",
                "items": talks_items,
            }

    # === Top-level: Concept of the Day ===
    if concept_seg:
        _render_segment(
            lines,
            level=2,
            name="Concept of the Day",
            angle="",
            items=concept_seg.get("items") or [],
            raw_items=raw_items,
            cit_map=cit_map,
            is_concept=True,
        )

    # === Top-level: News and Analysis (5 family sub-segments) ===
    if family_segs:
        lines.append("## News and Analysis\n")
        for seg in family_segs:
            _render_segment(
                lines,
                level=3,
                name=seg.get("name") or "Highlights",
                angle=seg.get("angle") or "",
                items=seg.get("items") or [],
                raw_items=raw_items,
                cit_map=cit_map,
            )

    # === Top-level: DEF CON & Black Hat Talks (separate from Conferences) ===
    if conf_talks_seg and (conf_talks_seg.get("items") or []):
        _render_segment(
            lines,
            level=2,
            name="DEF CON & Black Hat Talks",
            angle=conf_talks_seg.get("angle") or "",
            items=conf_talks_seg.get("items") or [],
            raw_items=raw_items,
            cit_map=cit_map,
        )

    # References and Rabbit Holes — diverse picks from across all sources,
    # each with a learning hook, NOT just the top-of-rank CVEs.
    if raw_items:
        lines.append("## References and Rabbit Holes\n")
        lines.append(
            "Curated picks from the day's sources, with what to look for. "
            "Tuned to your taste: Darknet Diaries / CTBB / BBRE for the "
            "story-and-method angle, Hak5 for hands-on tradecraft, DEF CON "
            "and Black Hat for deep conference talks, and the AI Engineer "
            "ecosystem (Latent Space / Interconnects / swyx / Ben's Bites) "
            "for the LLM-and-agent side. NVD/GHSA show up here only when "
            "the day's events demand it.\n"
        )

        # Priority order for picking references — learning-rich sources first,
        # so the section actually teaches instead of just listing CVE IDs.
        # Personalised for the operator: they watch Darknet Diaries, CTBB,
        # BBRE, Hak5; they want DEF CON / Black Hat talks; they read
        # AI Engineer / Latent Space / Interconnects newsletters; and they
        # track Nullcon (India) + HITB for conferences.
        reference_priority = [
            "portswigger",
            "trail_of_bits",
            "project_zero",
            "owasp",
            "owasp_blog",
            "owasp_top10",
            "owasp_cheatsheets",
            "owasp_asvs",
            "owasp_wstg",
            "exploit_db",
            "reddit_netsec",
            "reddit_bugbounty",
            "github_advisories",
            "hackerone",
            "darknet_diaries",        # operator watches this
            "critical_thinking",      # operator watches this
            "bbre",                    # operator watches this
            "hak5",                    # operator watches this; degraded mode w/ multiple fallbacks
            "defcon",                  # operator wants conference talks
            "blackhat",                # operator wants conference talks
            "conferences_youtube",     # alias for the defcon+blackhat pair
            "ai_newsletters",          # operator reads AI Engineer ecosystem
            "latent_space",            # AI Engineer Summit content
            "interconnects",           # Nathan Lambert on LLMs
            "bens_bites",              # Ben Tossell on AI tools
            "swyx",                    # swyx on AI engineering
            "nullcon",                 # India conference
            "hacker_news",             # HN front page
            "the_record",              # Recorded Future News — daily journalism
            "cisa_advisories",         # Official CISA advisory
            "ransomware_live",         # Real-time victim tracking
            "abusech_ransomware",      # abuse.ch BTC/C2 tracker
            "projectdiscovery",
            "risky_business",
            "krebs",
            "the_hacker_news",
            "dark_reading",
            "infosecurity_magazine",
            "threat_intel_news",
            "cyberwire_daily",
            "microsoft_security",
            "cisco_talos",
            "cert_in",
            "dfir_report",
            "ai_security",
            "hardware_hacking",
            "nvd",
            "cisa_kev",
            "vendor_rss",
            "mastodon",
            "nitter",
            "conferences",
            "youtube",
        ]

        seen_keys: set[str] = set()
        seen_titles: set[str] = set()
        picked: list[dict[str, Any]] = []

        # Bucket items by source for fair picking
        by_source: dict[str, list[dict[str, Any]]] = {}
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            src = (it.get("source") or "").strip()
            if not src:
                continue
            by_source.setdefault(src, []).append(it)

        for src in reference_priority:
            if len(picked) >= 12:
                break
            for it in by_source.get(src, []):
                if len(picked) >= 12:
                    break
                title = (it.get("title") or "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                picked.append(it)
                break  # one item per source

        # If we still have room, top up with remaining items (any source)
        if len(picked) < 12:
            for it in raw_items:
                if len(picked) >= 12:
                    break
                title = (it.get("title") or "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                picked.append(it)

        # Per-source learning hooks
        learning_hooks = {
            "portswigger": "Read the technique walkthrough — these writeups usually include a working PoC.",
            "trail_of_bits": "Deep, method-rich blog posts — read for the reasoning, not just the bug.",
            "project_zero": "Top-tier vulnerability research with full technical timelines and root-cause analysis.",
            "owasp": "Curriculum content — bookmark and reference while learning each topic.",
            "owasp_blog": "Project updates from the OWASP community — often a new lab or tool.",
            "owasp_top10": "The Top 10 — make sure you can find and exploit every category here.",
            "owasp_cheatsheets": "Cheat Sheet Series — go-to reference for each vulnerability class.",
            "owasp_asvs": "Application Security Verification Standard — the checklist for secure apps.",
            "owasp_wstg": "Web Security Testing Guide — read the section for any bug class you're hunting.",
            "exploit_db": "Public exploit code — read it to learn how the technique is actually weaponized.",
            "reddit_netsec": "Community PoC drops and writeups — freshest exploitation signal.",
            "reddit_bugbounty": "Top-hunter methodology and recent finds from r/bugbounty.",
            "github_advisories": "Supply-chain vulnerability with affected package and version range.",
            "hackerone": "Disclosed bug report — real-world findings with full attack narrative.",
            "projectdiscovery": "New tool or nuclei template — add to your recon/fuzzing pipeline.",
            "darknet_diaries": "Story-driven episode — context and tradecraft behind the headlines.",
            "critical_thinking": "Bug bounty methodology podcast — top hunters' workflows.",
            "bbre": "Reads disclosed bug reports — listen for the attacker's process.",
            "risky_business": "Industry commentary and geopolitical context.",
            "krebs": "Investigative reporting on cybercrime and breaches.",
            "the_hacker_news": "Breaking news coverage with technical detail.",
            "dark_reading": "Enterprise and vulnerability coverage with industry context.",
            "infosecurity_magazine": "Long-form analysis and feature articles.",
            "threat_intel_news": "BleedingComputer / The Hacker News / SecurityWeek daily threat intel.",
            "cyberwire_daily": "Daily 15-min briefing for the cybersecurity industry.",
            "microsoft_security": "Microsoft vulnerability and threat research.",
            "cisco_talos": "Threat intel from one of the largest research teams in the industry.",
            "cert_in": "India CERT advisory — vendor + affected versions + mitigations.",
            "dfir_report": "Deep incident analysis — read for the kill-chain reconstruction.",
            "ai_security": "AI/LLM security news from VentureBeat.",
            "hardware_hacking": "Hackaday security hacks — firmware, side-channels, physical attacks.",
            "hak5": "Hands-on hardware + tradecraft — Threat Wire daily 5-min news plus deep-dive episodes on tools and techniques. Source uses multi-URL fallback (Invidious + YouTube direct) since the primary RSS endpoint is intermittent.",
            "defcon": "DEF CON conference talk — these are full-length, deep-dive sessions on cutting-edge offensive security research.",
            "blackhat": "Black Hat conference talk — research-grade presentations from the industry's most technical researchers.",
            "conferences_youtube": "DEF CON / Black Hat conference talk — deep, full-length research presentations.",
            "ai_newsletters": "AI Engineer ecosystem — prompt injection, agent exploits, new LLM-bypass techniques are first shared here.",
            "latent_space": "Latent Space — the AI Engineer community newsletter by swyx, covers agentic systems, evals, and the LLM dev cycle.",
            "interconnects": "Interconnects (Nathan Lambert) — deep takes on LLM training, RLHF, and frontier model behavior.",
            "bens_bites": "Ben's Bites — daily AI tools and product roundup, often surfaces new agents + security implications.",
            "swyx": "swyx.io — the personal blog behind the AI Engineer Summit, covers the LLM engineering discipline.",
            "nullcon": "Nullcon (India) — flagship Indian offensive-security conference; many top bug bounty hunters debut techniques here.",
            "hacker_news": "Hacker News front page — security community discussion, often where new CVEs and PoCs are first linked.",
            "the_record": "Recorded Future News — daily cybercrime journalism. Connects CVEs to live incidents within hours.",
            "cisa_advisories": "BleepingComputer's ransomware coverage — daily de-facto source for new variants, leaked builders, and TTP changes.",
            "ransomware_live": "ransomware.live active group catalog — who's operational right now, what tools they use, where their leak sites are.",
            "abusech_ransomware": "abuse.ch cybercrime feed — fresh IoCs for active malware campaigns, the canonical source for IoC quality data.",
            "nvd": "CVE entry — check the affected versions and references for the full picture.",
            "cisa_kev": "Known-exploited vulnerability — patch this if it affects you.",
            "vendor_rss": "Vendor advisory — check affected versions and patch timeline.",
            "mastodon": "Community post from the fediverse — cross-check before citing as fact.",
            "nitter": "X / Twitter post via Nitter — cross-check before citing as fact.",
            "conferences": "Upcoming security conference — submit a talk or attend.",
            "youtube": "Video content — transcripts available for summarization.",
        }

        picks = ["Arjun", "Maya"]
        for i, item in enumerate(picked):
            title = item.get("title") or "Untitled"
            url = item.get("url") or ""
            src = item.get("source") or ""
            extra = item.get("extra") or {}
            clean_title = _clean_text(title, 110)
            cwe_suffix = _format_cwe(extra.get("cwe"))
            if cwe_suffix:
                clean_title = f"{clean_title} · {cwe_suffix}"
            is_notable = bool(extra.get("is_notable"))
            if is_notable:
                clean_title = f"★ {clean_title}"
            attribution = _get_source_attribution(src)
            hook = learning_hooks.get(src, "Read for context and follow-up research.")
            pick = picks[i % len(picks)]
            if url:
                lines.append(
                    f"*   [{clean_title}]({url}) — {pick}'s pick via {attribution}. "
                    f"**Why:** {hook}"
                )
            else:
                lines.append(
                    f"*   **{clean_title}** — {pick}'s pick via {attribution}. "
                    f"**Why:** {hook}"
                )
        lines.append("")

    # Learn It — practice resources, lab links, and a free reading list
    # for anyone on the elite-hacker path.
    learn_items: list[str] = []

    # Surface lab/practice pointers whenever the episode touches relevant
    # technique classes. The list is curated, not exhaustive.
    technique_keywords = {
        "xss": "PortSwigger Academy — Cross-site scripting labs (portswigger.net/web-security/cross-site-scripting)",
        "cross-site scripting": "PortSwigger Academy — Cross-site scripting labs (portswigger.net/web-security/cross-site-scripting)",
        "sql injection": "PortSwigger Academy — SQL injection labs (portswigger.net/web-security/sql-injection)",
        "sqli": "PortSwigger Academy — SQL injection labs (portswigger.net/web-security/sql-injection)",
        "ssrf": "PortSwigger Academy — Server-side request forgery labs (portswigger.net/web-security/ssrf)",
        "csrf": "PortSwigger Academy — CSRF labs (portswigger.net/web-security/csrf)",
        "xxe": "PortSwigger Academy — XML external entity labs (portswigger.net/web-security/xxe)",
        "deserialization": "PortSwigger Academy — Insecure deserialization labs (portswigger.net/web-security/deserialization)",
        "path traversal": "PortSwigger Academy — Path traversal labs (portswigger.net/web-security/path-traversal)",
        "authentication": "PortSwigger Academy — Authentication labs (portswigger.net/web-security/authentication)",
        "oauth": "PortSwigger Academy — OAuth labs (portswigger.net/web-security/oauth)",
        "jwt": "PortSwigger Academy — JWT labs (portswigger.net/web-security/jwt)",
        "file upload": "PortSwigger Academy — File upload labs (portswigger.net/web-security/file-upload)",
        "command injection": "PortSwigger Academy — OS command injection labs (portswigger.net/web-security/os-command-injection)",
        "os command": "PortSwigger Academy — OS command injection labs (portswigger.net/web-security/os-command-injection)",
        "rce": "PortSwigger Academy — OS command injection labs (portswigger.net/web-security/os-command-injection)",
        "ssti": "PortSwigger Research — Server-side template injection (portswigger.net/research/server-side-template-injection)",
        "prototype pollution": "PortSwigger Academy — Prototype pollution labs",
        "race condition": "PortSwigger Research — Smashing the state machine (portswigger.net/research/smashing-the-state-machine)",
        "http request smuggling": "PortSwigger Research — HTTP request smuggling (portswigger.net/research/http-desync-attacks)",
        "kernel": "Linux Kernel CVE archives + pwnable wargames",
        "container escape": "DeepSurface / HackTricks container-escape checklist",
        "kubernetes": "Kubernetes Goat (madhuakula/kubernetes-goat) + kube-hunter",
    }

    seen_links: set[str] = set()
    blob = " ".join(
        (it.get("title") or "") + " " + (it.get("summary") or "")
        for it in raw_items
    ).lower()
    for kw, link in technique_keywords.items():
        if link in seen_links:
            continue
        if kw in blob:
            learn_items.append(f"*   {link}")
            seen_links.add(link)

    if any(it.get("source") in ("reddit_netsec", "reddit_bugbounty") for it in raw_items):
        learn_items.append(
            "*   Reddit r/netsec + r/bugbounty — fresh PoC drops, writeups, "
            "and top-hunter methodology (reddit.com/r/netsec, reddit.com/r/bugbounty)"
        )
    if any(it.get("source", "").startswith("owasp") for it in raw_items):
        learn_items.append(
            "*   OWASP Top 10, ASVS, Cheat Sheet Series, WSTG — the de-facto curriculum "
            "(owasp.org/www-project-top-ten, /www-project-application-security-verification-standard, "
            "/www-project-cheat-sheets, /www-project-web-security-testing-guide)"
        )
    if any(it.get("source") == "exploit_db" for it in raw_items):
        learn_items.append(
            "*   Exploit-DB — public exploits, read the code to learn the technique "
            "(exploit-db.com)"
        )
    if any(it.get("source") == "github_advisories" for it in raw_items):
        learn_items.append(
            "*   GitHub Security Advisories — supply-chain vulnerabilities across npm, "
            "PyPI, Maven, Go, Rust (github.com/advisories)"
        )
    if any(it.get("source") in ("portswigger", "trail_of_bits", "project_zero") for it in raw_items):
        learn_items.append(
            "*   Researcher blogs to subscribe to: PortSwigger Research, "
            "Trail of Bits blog, Google Project Zero — deep, technique-rich writeups"
        )

    # Surface the Concept of the Day's "Build it yourself" pointer as the
    # top item in Learn It, so the action shows up on its own line.
    for it in raw_items:
        if it.get("source") == "concept_of_the_day":
            summary = (it.get("summary") or "")
            for line in summary.splitlines():
                if line.lower().startswith("build it yourself"):
                    build_line = line.split(":", 1)[1].strip() if ":" in line else line
                    learn_items.insert(
                        0, f"*   **Concept of the Day — build it:** {build_line}"
                    )
                    break
            break

    if learn_items:
        lines.append("## Learn It\n")
        lines.append(
            "Free practice environments and reading to turn today's news into "
            "tomorrow's tradecraft.\n"
        )
        lines.extend(learn_items)
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


def _render_pdf_segment(
    pdf,
    *,
    level: int,
    name: str,
    angle: str,
    items: list[dict[str, Any]],
    raw_items: list[dict[str, Any]],
    cit_map: dict[str, str],
) -> None:
    """Render one segment into the PDF, with per-item clickable links.

    level=2 produces a bold 11pt heading (top-level), level=3 produces
    a bold 10pt heading (sub-section of News and Analysis).
    """
    header_size = 11 if level == 2 else 10
    pdf.set_font("DejaVu", "B", header_size)
    pdf.cell(0, 6 if level == 3 else 7, name, new_x="LMARGIN", new_y="NEXT")

    if angle:
        pdf.set_font("DejaVu", "", 8)
        pdf.set_text_color(80, 80, 80)
        pdf.multi_cell(0, 4, angle, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)

    for item in items:
        headline = item.get("headline") or ""
        key_facts = item.get("key_facts") or []
        key_point = _clean_text(key_facts[0], 200) if key_facts else ""

        item_citations: set[int] = set()
        for src_item in raw_items:
            if src_item.get("title", "").strip() == headline.strip():
                src = src_item.get("source", "")
                if src in cit_map:
                    item_citations.add(int(cit_map[src]))
        cit_str = " ".join(f"[{n}]" for n in sorted(item_citations))

        # Build the clickable link text from source_urls / raw_items.
        item_url: str = ""
        for url in item.get("source_urls") or []:
            if url:
                item_url = url
                break
        if not item_url:
            for src_item in raw_items:
                if src_item.get("title", "").strip() == headline.strip():
                    cand = src_item.get("url") or ""
                    if cand:
                        item_url = cand
                        break

        # CWE suffix (e.g. " · CWE-78 (OS Command Injection)")
        cwe_str = _format_cwe(item.get("cwe"))
        is_notable = bool((item.get("extra") or {}).get("is_notable"))

        # Build the headline label that will become the link.
        link_label = headline
        if cwe_str:
            link_label = f"{headline} \u00b7 {cwe_str}"
        if len(link_label) > 220:
            link_label = link_label[:217] + "\u2026"
        if is_notable:
            link_label = f"★ {link_label}"

        # Render: bullet + linked headline in blue, then plain tail.
        pdf.set_font("DejaVu", "", 8)
        pdf.write(4.5, "\u2022  ")
        if item_url:
            pdf.set_text_color(0, 0, 200)
            pdf.set_font("DejaVu", "B", 8)
            pdf.write(4.5, link_label, link=item_url)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("DejaVu", "", 8)
        else:
            pdf.set_font("DejaVu", "B", 8)
            pdf.write(4.5, link_label)
        if key_point:
            pdf.write(4.5, f" \u2014 {key_point}")
        if cit_str:
            pdf.write(4.5, f"  {cit_str}")
        pdf.ln(5)
    pdf.ln(2)


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
    pdf.add_font("DejaVu", "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    pdf.add_font("DejaVu", "B", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
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

    # News and Analysis — partition segments by name, same logic as
    # generate_shownotes so the PDF and the .md have identical structure.
    concept_seg = next(
        (s for s in segments if (s.get("items") or [{}])[0].get("source") == "concept_of_the_day"),
        None,
    )
    family_segs = [
        s for s in segments
        if s is not concept_seg
        and not (s.get("name") or "").lower().startswith("def con & black hat")
    ]
    conf_talks_seg = next(
        (s for s in segments if (s.get("name") or "").lower().startswith("def con & black hat")),
        None,
    )
    if conf_talks_seg is None:
        talks_items = [
            it
            for s in family_segs
            for it in s.get("items") or []
            if (it.get("source") or it.get("_source")) in {"defcon", "blackhat", "conferences_youtube"}
        ]
        if talks_items:
            conf_talks_seg = {
                "name": "DEF CON & Black Hat Talks",
                "angle": "",
                "items": talks_items,
            }

    # Top-level: Concept of the Day
    if concept_seg:
        _render_pdf_segment(
            pdf,
            level=2,
            name="Concept of the Day",
            angle="",
            items=concept_seg.get("items") or [],
            raw_items=raw_items,
            cit_map=cit_map,
        )

    # News and Analysis
    if family_segs:
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 7, "News and Analysis", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        for seg in family_segs:
            _render_pdf_segment(
                pdf,
                level=3,
                name=seg.get("name") or "Highlights",
                angle=seg.get("angle") or "",
                items=seg.get("items") or [],
                raw_items=raw_items,
                cit_map=cit_map,
            )

    # Top-level: DEF CON & Black Hat Talks
    if conf_talks_seg and (conf_talks_seg.get("items") or []):
        _render_pdf_segment(
            pdf,
            level=2,
            name="DEF CON & Black Hat Talks",
            angle=conf_talks_seg.get("angle") or "",
            items=conf_talks_seg.get("items") or [],
            raw_items=raw_items,
            cit_map=cit_map,
        )

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
                pdf.set_text_color(0, 0, 200)
                pdf.set_font("DejaVu", "", 8)
                pdf.write(4.5, label, link=url)
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
