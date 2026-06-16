"""Common types and helpers shared by all source fetchers."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

# Source weight defaults — used by the ranking stage.
SOURCE_WEIGHTS: dict[str, float] = {
    "portswigger": 1.0,
    "hackerone": 0.9,
    "projectdiscovery": 0.7,
    "nvd": 0.85,
    "cisa_kev": 1.0,
    "vendor_rss": 0.6,
    "youtube": 1.2,        # Prioritized primary resource
    "conferences": 1.1,    # High importance for events
    "ai_security": 1.0,
    "hardware_hacking": 1.0,
    "krebs": 1.0,
    "threat_intel_news": 1.0,
    "ransomwatch": 0.8,    # (no longer maintained — disabled)
    "dfir_report": 0.9,    # In-depth DFIR incident analysis
    "nitter": 0.6,         # Twitter/X via Nitter RSS — Tier 3 leads, cross-check
    "mastodon": 0.5,       # Tier 3 lead — cross-check before asserting as fact
    "the_hacker_news": 0.8,
    "dark_reading": 0.8,
    "infosecurity_magazine": 0.8,
    "cisco_talos": 0.9,
    "microsoft_security": 0.8,
    "project_zero": 1.0,
    "trail_of_bits": 0.9,
    "cert_in": 0.9,
    "cyberwire_daily": 0.7,
    # Elite-hacker learning tier (newly enabled/added)
    "reddit_netsec": 0.85,         # Community PoCs, writeups
    "reddit_bugbounty": 0.85,      # Methodology, top hunter tips
    "owasp": 1.0,                  # The curriculum
    "owasp_blog": 0.9,
    "owasp_top10": 1.0,
    "owasp_asvs": 0.9,
    "owasp_cheatsheets": 0.95,
    "owasp_wstg": 0.95,
    "exploit_db": 0.9,             # Public exploits — read to learn
    "github_advisories": 0.9,      # Supply-chain vulns
    "darknet_diaries": 0.8,        # Story-driven — motivation + context
    "risky_business": 0.6,         # Industry commentary
    "critical_thinking": 0.85,     # Bug bounty methodology
    "bbre": 0.85,                  # Reads bug reports
    # Personalised for the operator — Hak5, DEF CON / Black Hat, AI Engineer
    # ecosystem, Nullcon + HITB conferences, Hacker News front page.
    # hak5 disabled 2026-06-16: YouTube channel RSS feed 404s across all
    # proxies (YouTube direct, Invidious, Piped). Re-enable by uncommenting
    # the line below and re-creating src/podcaster_ai/pipeline/sources/hak5.py.
    # "hak5": 1.0,                # Threat Wire + hands-on tradecraft
    "defcon": 1.0,                 # Conference talks
    "blackhat": 1.0,               # Conference talks
    "conferences_youtube": 1.0,    # alias for the defcon+blackhat pair
    "ai_newsletters": 0.85,        # LLM/agent/dev-tools news cycle
    "latent_space": 0.85,
    "interconnects": 0.85,
    "bens_bites": 0.7,
    "swyx": 0.7,
    "nullcon": 0.9,                # India offensive-security conference
    "hacker_news": 0.6,            # HN frontpage (filtered downstream)
    "concept_of_the_day": 0.95,    # Curated daily concept — always included
    # Ransomware / threat intel tier (replaces RansomWatch)
    "the_record": 0.85,            # Recorded Future News — daily journalism
    "cisa_advisories": 0.95,       # Official CISA advisories — high-signal
    "ransomware_live": 0.8,        # Real-time victim tracking
    "abusech_ransomware": 0.8,     # abuse.ch tracker — BTC addresses, C2s
}

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
DEFAULT_HEADERS = {
    "User-Agent": (
        "podcaster-ai/0.1 (+https://github.com/mohnqwerty/_podcaster-ai) "
        "research-fetcher"
    ),
    "Accept": "application/json, application/xml, text/xml, text/plain;q=0.9, */*;q=0.5",
}


@dataclass(slots=True)
class Item:
    """Normalised raw item produced by every source fetcher."""

    title: str
    url: str
    summary: str
    source: str
    published_at: Optional[datetime] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.published_at is not None:
            d["published_at"] = self.published_at.astimezone(timezone.utc).isoformat()
        return d


def http_client() -> httpx.Client:
    """Return a configured httpx.Client. Callers must close (use as context manager)."""
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers=DEFAULT_HEADERS,
        follow_redirects=True,
    )


def parse_dt(value: object) -> Optional[datetime]:
    """Best-effort datetime parsing. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    # Try a series of common formats.
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        # ISO 8601 (handle trailing "Z").
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        from dateutil import parser as du_parser  # type: ignore

        dt = du_parser.parse(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        log.debug("parse_dt.failed", value=s)
        return None
