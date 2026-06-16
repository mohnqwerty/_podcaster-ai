"""Concept-of-the-Day source.

Returns a single curated concept per day, rotated through a pool of ~35
topics across 5 buckets:
  - ai_security      (largest bucket — operator's focus)
  - web_security     (XSS/SSRF/JWT/OAuth/deserialization)
  - frameworks       (OWASP/NIST/CVSS/MITRE ATT&CK)
  - tradecraft       (AD attacks, LOLBins, credential dumping, phishing)
  - foundations      (zero trust, crypto, CIA, secure defaults)

Each concept has a title, summary, talking_points (3-4 bullets for
Arjun/Maya to expand on), and a build_it pointer (a hands-on exercise).

The source is deterministic — the concept for a given date is the
bucket that the date's day-of-year falls in, then the topic at the
position (day % len(topics_in_bucket)). This means every podcast
gets a fresh concept, the same date always gets the same concept,
and the cycle is ~5 weeks before any concept repeats.

Configuration (env):
- CONCEPT_TIMEZONE  (str, default "UTC")
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import structlog

from .base import Item

log = structlog.get_logger(__name__)

SOURCE: Final[str] = "concept_of_the_day"
_DATA_FILE: Final[Path] = Path(__file__).resolve().parents[2] / "data" / "concept_pool.json"


def _load_pool() -> dict[str, Any]:
    """Read the concept pool from the bundled JSON file."""
    try:
        with _DATA_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        log.warning("concept_pool.load_failed", error=str(exc))
        return {}


def _pick_concept(now: datetime) -> dict[str, Any] | None:
    """Deterministic concept-of-the-day picker.

    Rotation strategy:
      - Day of year % 5 picks the bucket (cycles through the 5 buckets)
      - Day of year // 5 % len(bucket) picks the topic within the bucket

    This guarantees that:
      - Every bucket gets covered at least once every 5 days
      - No topic repeats within ~5 weeks (5 topics/bucket × 5 days)
      - The same date always gets the same concept
    """
    pool = _load_pool()
    meta = pool.get("_meta", {})
    rotation = meta.get("rotation_order", list(pool.keys()))

    if not rotation:
        return None

    day_of_year = now.timetuple().tm_yday
    bucket_name = rotation[day_of_year % len(rotation)]
    bucket = pool.get(bucket_name) or []
    if not bucket:
        return None
    pos = (day_of_year // len(rotation)) % len(bucket)
    return bucket[pos]


def _concept_to_item(concept: dict[str, Any], bucket: str, now: datetime) -> Item:
    """Build an Item from a concept dict so the rest of the pipeline treats it
    like a sourced item. The summary carries the talking points and build-it
    pointer so the LLM and the shownotes stage have everything they need."""
    title = f"Concept of the Day: {concept.get('title', 'Untitled')}"
    summary_parts: list[str] = [concept.get("summary", "").strip()]
    talking_points = concept.get("talking_points") or []
    if talking_points:
        summary_parts.append("")
        summary_parts.append("Talking points:")
        for tp in talking_points:
            summary_parts.append(f"- {tp}")
    build_it = concept.get("build_it")
    if build_it:
        summary_parts.append("")
        summary_parts.append(f"Build it yourself: {build_it}")
    summary = "\n".join(p for p in summary_parts if p).strip()

    return Item(
        title=title,
        url="",  # No external link; this is a curated concept
        summary=summary,
        source=SOURCE,
        published_at=now,
        extra={
            "bucket": bucket,
            "is_concept": True,
        },
    )


def fetch() -> list[Item]:
    """Return exactly one Item: today's concept."""
    try:
        tz_name = os.environ.get("CONCEPT_TIMEZONE", "UTC")
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001
            tz = timezone.utc
        now = datetime.now(tz)
    except Exception:  # noqa: BLE001
        now = datetime.now(timezone.utc)

    concept = _pick_concept(now)
    if concept is None:
        log.warning("concept_pool.empty")
        return []

    pool = _load_pool()
    meta = pool.get("_meta", {})
    rotation = meta.get("rotation_order", [])
    day_of_year = now.timetuple().tm_yday
    bucket = rotation[day_of_year % len(rotation)] if rotation else "general"

    item = _concept_to_item(concept, bucket, now)
    log.info(
        "concept_pool.fetched",
        bucket=bucket,
        title=concept.get("title"),
    )
    return [item]
