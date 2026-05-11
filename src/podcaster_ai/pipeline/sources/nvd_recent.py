"""Fetch recent CVEs from the NVD 2.0 API filtered by CVSS threshold + lookback window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Final

import structlog

from ...config import get_settings
from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

NVD_ENDPOINT: Final[str] = "https://services.nvd.nist.gov/rest/json/cves/2.0"
SOURCE: Final[str] = "nvd"


def _extract_cvss(metrics: dict[str, Any]) -> tuple[float | None, str | None]:
    """Return (best_score, vector_string) from an NVD metrics block."""
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key) or []
        for entry in entries:
            data = entry.get("cvssData") or {}
            score = data.get("baseScore")
            if score is not None:
                return float(score), data.get("vectorString")
    return None, None


def _english_description(descriptions: list[dict[str, Any]]) -> str:
    for d in descriptions or []:
        if d.get("lang") == "en":
            return (d.get("value") or "").strip()
    if descriptions:
        return (descriptions[0].get("value") or "").strip()
    return ""


def fetch() -> list[Item]:
    """Return recent high-severity CVEs published in the last N hours. Fail-soft."""
    settings = get_settings()
    hours = max(1, settings.nvd_lookback_hours)
    min_cvss = settings.nvd_min_cvss

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    params = {
        # NVD requires "yyyy-MM-ddTHH:mm:ss.SSS" — millisecond precision.
        "pubStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "pubEndDate": end.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "resultsPerPage": 200,
    }

    try:
        with http_client() as client:
            resp = client.get(NVD_ENDPOINT, params=params)
            resp.raise_for_status()
            data = resp.json() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("nvd.fetch_failed", error=str(exc))
        return []

    items: list[Item] = []
    for vuln in data.get("vulnerabilities") or []:
        cve = vuln.get("cve") or {}
        cve_id = cve.get("id")
        if not cve_id:
            continue

        score, vector = _extract_cvss(cve.get("metrics") or {})
        if score is None or score < min_cvss:
            continue

        description = _english_description(cve.get("descriptions") or [])
        if len(description) > 1000:
            description = description[:1000].rsplit(" ", 1)[0] + "…"

        items.append(
            Item(
                title=f"{cve_id} (CVSS {score:.1f})",
                url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                summary=description or f"{cve_id} — see NVD for details.",
                source=SOURCE,
                published_at=parse_dt(cve.get("published")),
                extra={
                    "cve_id": cve_id,
                    "cvss": score,
                    "vector": vector,
                },
            )
        )

    log.info("nvd.fetched", count=len(items), min_cvss=min_cvss, hours=hours)
    return items
