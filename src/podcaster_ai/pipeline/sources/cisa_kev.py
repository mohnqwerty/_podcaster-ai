"""Fetch the CISA Known Exploited Vulnerabilities catalog and surface recent additions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Final

import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

KEV_URL: Final[str] = (
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
)
SOURCE: Final[str] = "cisa_kev"
DEFAULT_LOOKBACK_DAYS: Final[int] = 14


def fetch() -> list[Item]:
    """Return KEV entries added in the last DEFAULT_LOOKBACK_DAYS days. Fail-soft."""
    try:
        with http_client() as client:
            # Add User-Agent to avoid 403 Forbidden
            headers = {"User-Agent": "podcaster-ai/1.0 (+https://github.com/mohnqwerty/_podcaster-ai)"}
            resp = client.get(KEV_URL, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("cisa_kev.fetch_failed", error=str(exc))
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    items: list[Item] = []
    for vuln in data.get("vulnerabilities") or []:
        added = parse_dt(vuln.get("dateAdded"))
        if added is None or added < cutoff:
            continue

        cve_id = vuln.get("cveID") or "CVE-UNKNOWN"
        vendor = vuln.get("vendorProject") or "Unknown vendor"
        product = vuln.get("product") or "Unknown product"
        name = vuln.get("vulnerabilityName") or cve_id
        short = (vuln.get("shortDescription") or "").strip()
        action = (vuln.get("requiredAction") or "").strip()

        summary_parts = [
            f"Vendor: {vendor} | Product: {product}",
        ]
        if short:
            summary_parts.append(short)
        if action:
            summary_parts.append(f"Required action: {action}")

        items.append(
            Item(
                title=f"KEV: {name} ({cve_id})",
                url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                summary=" | ".join(summary_parts),
                source=SOURCE,
                published_at=added,
                extra={
                    "cve_id": cve_id,
                    "vendor": vendor,
                    "product": product,
                    "due_date": vuln.get("dueDate"),
                    "known_ransomware_use": vuln.get("knownRansomwareCampaignUse"),
                },
            )
        )

    log.info("cisa_kev.fetched", count=len(items))
    return items
