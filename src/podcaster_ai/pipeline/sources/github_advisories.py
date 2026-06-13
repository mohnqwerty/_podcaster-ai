"""GitHub Security Advisories (GHSA) source.

Pulls recent reviewed security advisories from GitHub's public API
(no auth required for public data). The GHSA database is the
authoritative source for supply-chain vulnerabilities across npm,
PyPI, Maven, Go, Rust, etc. — a major modern attack surface that
NVD often lags on.

For the elite-hacker goal: supply-chain bugs (dependency confusion,
typosquatting, malicious packages) are a huge and growing category.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Final

import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "github_advisories"
API_URL: Final[str] = "https://api.github.com/advisories"
MAX_ITEMS: Final[int] = 8
LOOKBACK_DAYS: Final[int] = 30


def _format_advisory(adv: dict[str, Any]) -> Item | None:
    try:
        ghsa_id = adv.get("ghsa_id") or ""
        cve_id = (adv.get("cve_id") or "").strip() or None
        summary = (adv.get("summary") or "").strip()
        description = (adv.get("description") or "").strip()
        html_url = (adv.get("html_url") or "").strip()
        if not html_url or not summary:
            return None

        title_parts = [f"GHSA-{ghsa_id}"] if ghsa_id else []
        if cve_id:
            title_parts.append(cve_id)
        title_parts.append(summary[:120])
        title = " — ".join(title_parts) if title_parts else summary[:120]

        body = description or summary
        if len(body) > 800:
            body = body[:800].rstrip() + "..."

        published = parse_dt(adv.get("published_at"))

        severity = (adv.get("severity") or "").strip().lower() or None
        cvss = adv.get("cvss") or {}
        cvss_score: float | None = None
        try:
            s = cvss.get("score")
            if s is not None:
                cvss_score = float(s)
        except (TypeError, ValueError):
            cvss_score = None

        extra: dict[str, Any] = {}
        if cve_id:
            extra["cve_id"] = cve_id
        if severity:
            extra["severity"] = severity
        if cvss_score is not None:
            extra["cvss"] = cvss_score
        for ref in adv.get("references") or []:
            url = ref.get("url") if isinstance(ref, dict) else None
            if url and "github.com/" not in (url or ""):
                extra.setdefault("poc_url", url)
                break
        for pkg in adv.get("vulnerabilities") or []:
            pname = pkg.get("package") or {}
            if isinstance(pname, dict) and pname.get("name"):
                extra.setdefault("package", pname.get("name"))
                break

        return Item(
            title=title,
            url=html_url,
            summary=body,
            source=SOURCE,
            published_at=published,
            extra=extra,
        )
    except Exception as exc:
        log.debug("github_advisories.entry_skipped", error=str(exc))
        return None


def fetch() -> list[Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    try:
        with http_client() as client:
            resp = client.get(
                API_URL,
                params={
                    "type": "reviewed",
                    "per_page": str(MAX_ITEMS * 2),
                    "sort": "published",
                    "direction": "desc",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("github_advisories.fetch_failed", error=str(exc))
        return []

    if not isinstance(data, list):
        log.warning("github_advisories.unexpected_payload", type=type(data).__name__)
        return []

    items: list[Item] = []
    for adv in data:
        if len(items) >= MAX_ITEMS:
            break
        if not isinstance(adv, dict):
            continue
        published = parse_dt(adv.get("published_at"))
        if published is not None and published < cutoff:
            continue
        item = _format_advisory(adv)
        if item is not None:
            items.append(item)

    log.info("github_advisories.fetched", count=len(items))
    return items
