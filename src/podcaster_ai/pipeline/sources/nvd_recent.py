"""Fetch recent CVEs from the NVD 2.0 API filtered by CVSS threshold + lookback window.

Applies a vendor whitelist so the show only surfaces CVEs in widely-used
software (Microsoft, Apple, Linux kernel, OpenSSL, K8s, etc.). The
whitelist is a Fortune-500-typical bar; obscure CMS plugins and niche
appliances are dropped at fetch time. Each surviving CVE is tagged
`extra.is_notable = True` so the shownotes section can badge it with a
star. CVEs that fall outside the whitelist but have CVSS >= 9.5 are
kept anyway — sometimes a critical vuln in a less-common package still
matters (a 9.5 in any software warrants attention).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Final

import structlog

from ...config import get_settings
from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

NVD_ENDPOINT: Final[str] = "https://services.nvd.nist.gov/rest/json/cves/2.0"
SOURCE: Final[str] = "nvd"

# Vendor whitelist — Fortune-500-typical bar (~30 vendors).
# Matched as substrings against the CPE string in NVD's response, lowercased.
_NOTABLE_VENDOR_KEYWORDS: Final[tuple[str, ...]] = (
    # Microsoft ecosystem
    "microsoft", "windows", "exchange", "office", "azure", "msrc", "msft",
    # Apple
    "apple", "ios", "macos", "ipados", "safari", "tvos", "webkit",
    # Google
    "google", "android", "chrome", "chromium",
    # Linux distributions + kernel
    "linux", "kernel", "redhat", "ubuntu", "debian", "suse", "canonical",
    "alpine", "amazon_linux", "amzn", "rhel", "centos", "fedora",
    # Networking / infra
    "cisco", "juniper", "fortinet", "forti", "palo_alto", "paloalto",
    "f5", "citrix", "vmware", "vmware_by_broadcom", "checkpoint", "arista",
    "broadcom", "aruba", "dell", "hp_", "hewlett_packard", "lenovo",
    # Open-source libraries / runtimes
    "openssl", "apache", "httpd", "nginx", "openssh", "ffmpeg", "libxml2",
    "curl", "libcurl", "node", "nodejs", "python", "django", "rails",
    "spring", "log4j", "tomcat", "kafka", "redis", "postgres", "postgresql",
    "mysql", "mariadb", "mongodb", "memcached", "rabbitmq", "elasticsearch",
    # Cloud / DevOps
    "atlassian", "jira", "confluence", "bitbucket", "github", "gitlab",
    "hashicorp", "terraform", "ansible", "jenkins", "circleci", "kubernetes",
    "k8s", "docker", "moby", "istio", "envoy", "etcd", "prometheus",
    # Cloud providers
    "aws", "amazon", "azure", "gcp", "google_cloud",
    # Popular SaaS / identity
    "okta", "auth0", "salesforce", "slack", "zoom", "workday", "servicenow",
    "zendesk", "dropbox", "box", "slack_technologies",
    # Security tools / platforms (these are widely-deployed)
    "palo_alto_networks", "crowdstrike", "sentinelone", "sentinel_one",
    "snyk", "rapid7", "qualys", "tenable", "nessus",
    # Browsers
    "firefox", "mozilla", "brave", "tor_browser", "torproject",
)

# CVEs above this score are kept even if no vendor match.
_HIGH_CVSS_FLOOR: Final[float] = 9.5


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


def _extract_cwe(weaknesses: list[dict[str, Any]]) -> str | None:
    """Return the primary CWE id (e.g. "CWE-78") from the NVD weaknesses block.

    NVD lists weaknesses as:
        {"source": "...", "type": "Primary"|"Secondary", "description": [{"lang":"en","value":"CWE-78"}]}
    Prefer Primary; fall back to the first English description.
    """
    primary: str | None = None
    fallback: str | None = None
    for w in weaknesses or []:
        for d in w.get("description") or []:
            if d.get("lang") != "en":
                continue
            value = (d.get("value") or "").strip()
            if not value.startswith("CWE-"):
                continue
            if w.get("type") == "Primary":
                primary = value
                break
            if fallback is None:
                fallback = value
        if primary:
            return primary
    return fallback


def _english_description(descriptions: list[dict[str, Any]]) -> str:
    for d in descriptions or []:
        if d.get("lang") == "en":
            return (d.get("value") or "").strip()
    if descriptions:
        return (descriptions[0].get("value") or "").strip()
    return ""


def _cpe_strings(cve: dict[str, Any]) -> list[str]:
    """Flatten every CPE criterion string from an NVD configurations block."""
    out: list[str] = []
    for config in cve.get("configurations") or []:
        for node in config.get("nodes") or []:
            for cpe in node.get("cpeMatch") or []:
                crit = cpe.get("criteria")
                if isinstance(crit, str):
                    out.append(crit.lower())
    return out


def _is_notable_vendor(cpe_strings: list[str], score: float) -> bool:
    """Return True if the CVE matches the vendor whitelist or has a very
    high CVSS (>= _HIGH_CVSS_FLOOR). The CVSS floor is a safety net for
    critical bugs in less-common software that the operator runs."""
    if score >= _HIGH_CVSS_FLOOR:
        return True
    for cpe in cpe_strings:
        for kw in _NOTABLE_VENDOR_KEYWORDS:
            if kw in cpe:
                return True
    return False


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
    vendor_filtered = 0
    for vuln in data.get("vulnerabilities") or []:
        cve = vuln.get("cve") or {}
        cve_id = cve.get("id")
        if not cve_id:
            continue

        score, vector = _extract_cvss(cve.get("metrics") or {})
        if score is None or score < min_cvss:
            continue

        cwe = _extract_cwe(cve.get("weaknesses") or [])
        cpe_strings = _cpe_strings(cve)
        is_notable = _is_notable_vendor(cpe_strings, score)
        if not is_notable:
            vendor_filtered += 1
            continue

        description = _english_description(cve.get("descriptions") or [])
        if len(description) > 500:
            description = description[:500].rsplit(" ", 1)[0] + "…"

        extra: dict[str, Any] = {
            "cve_id": cve_id,
            "cvss": score,
            "vector": vector,
            "is_notable": True,
        }
        if cwe:
            extra["cwe"] = cwe

        items.append(
            Item(
                title=f"{cve_id} (CVSS {score:.1f})",
                url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                summary=description or f"{cve_id} — see NVD for details.",
                source=SOURCE,
                published_at=parse_dt(cve.get("published")),
                extra=extra,
            )
        )

    log.info(
        "nvd.fetched",
        count=len(items),
        min_cvss=min_cvss,
        hours=hours,
        vendor_filtered=vendor_filtered,
    )
    return items
