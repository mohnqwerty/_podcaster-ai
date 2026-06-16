"""abuse.ch URLhaus feed source.

The original abuse.ch Ransomware Tracker (ransomwaretracker.abuse.ch)
has been intermittent for years. The replacement is abuse.ch's
URLhaus service — a community-driven feed of active malware-hosting
URLs, updated daily. The CSV is freely available without API key and
carries the most actionable IoCs in the abuse.ch ecosystem.

URLhaus also operates MalwareBazaar (malware samples), ThreatFox
(IoC database), and FeodoTracker (C2 tracker). All complement the
URLhaus feed as primary threat-intel sources.

For the elite-hacker goal: URLhaus is the canonical source for
"what URLs are hosting malware right now". The CSV is 3MB+ daily
and updated continuously. Filter for `threat = Malware download`
or `threat = C2` to find the most relevant entries.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Final

import structlog

from .base import Item, http_client

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "abusech_ransomware"
CSV_URL: Final[str] = "https://urlhaus.abuse.ch/downloads/csv_recent/"
MAX_ITEMS: Final[int] = 5
# Only pull these threat types — most useful for the show.
_KEEP_THREATS: Final[frozenset[str]] = frozenset({"malware_download", "c2"})


def _parse_csv(text: str) -> list[Item]:
    """URLhaus CSV format: header lines start with '#', columns are
    id, dateadded, url, url_status, last_online, threat, tags, urlhaus_link, reporter.

    Header lines are skipped by Python's csv module if we just iterate.
    """
    items: list[Item] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row or len(row) < 7:
            continue
        if row[0].startswith("#"):
            continue
        # Column indices (verified against actual URLhaus CSV format):
        # 0: id, 1: dateadded, 2: url, 3: url_status, 4: last_online,
        # 5: threat, 6: tags, 7: urlhaus_link, 8: reporter
        if len(row) > 3 and row[3].strip().lower() != "online":
            continue
        threat = row[5].strip().lower() if len(row) > 5 else ""
        if _KEEP_THREATS and threat not in _KEEP_THREATS:
            continue
        try:
            url = row[2].strip()
            threat_label = row[5].strip() if len(row) > 5 else "malware"
            tags = row[6].strip() if len(row) > 6 else ""
            urlhaus_link = row[7].strip() if len(row) > 7 else ""
            summary = f"Threat: {threat_label}"
            if tags:
                summary += f" — tags: {tags}"
            items.append(
                Item(
                    title=f"Active malware URL: {url[:120]}",
                    url=urlhaus_link or url,
                    summary=summary,
                    source=SOURCE,
                    published_at=datetime.now(timezone.utc),
                    extra={
                        "threat": threat_label,
                        "tags": tags,
                        "observed_url": url,
                    },
                )
            )
            if len(items) >= MAX_ITEMS:
                break
        except Exception as exc:  # noqa: BLE001
            log.debug("abusech_ransomware.row_skipped", error=str(exc))
            continue
    return items


def fetch() -> list[Item]:
    try:
        with http_client() as client:
            resp = client.get(CSV_URL)
            resp.raise_for_status()
            items = _parse_csv(resp.text)
    except Exception as exc:  # noqa: BLE001
        log.warning("abusech_ransomware.fetch_failed", error=str(exc))
        return []

    log.info("abusech_ransomware.fetched", count=len(items))
    return items
