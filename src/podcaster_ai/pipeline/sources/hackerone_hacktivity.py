"""Fetch recent disclosed reports from HackerOne's public hacktivity feed.

We use the public hacktivity GraphQL endpoint and filter to disclosure-only
results so we never surface anything still under embargo.
"""

from __future__ import annotations

from typing import Any, Final

import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

ENDPOINT: Final[str] = "https://hackerone.com/graphql"
SOURCE: Final[str] = "hackerone"
PAGE_SIZE: Final[int] = 25

# Minimal GraphQL query — only public, disclosed reports.
QUERY: Final[str] = """
query Hacktivity($first: Int!) {
  hacktivity_items(
    first: $first
    queryString: "disclosed:true"
    orderBy: { field: latest_disclosable_activity_at, direction: DESC }
  ) {
    edges {
      node {
        ... on Disclosed {
          id
          databaseId: _id
          reporter { username }
          team { handle name }
          report {
            id
            databaseId: _id
            title
            substate
            url
            disclosed_at
            severity_rating
          }
          latest_disclosable_action
          latest_disclosable_activity_at
          total_awarded_amount
        }
      }
    }
  }
}
""".strip()


def _node_to_item(node: dict[str, Any]) -> Item | None:
    report = node.get("report") or {}
    title = (report.get("title") or "").strip()
    url = (report.get("url") or "").strip()
    if not title or not url:
        return None

    team = node.get("team") or {}
    severity = report.get("severity_rating")
    bounty = node.get("total_awarded_amount")
    summary_parts = [
        f"Program: {team.get('name') or team.get('handle') or 'unknown'}",
    ]
    if severity:
        summary_parts.append(f"Severity: {severity}")
    if bounty:
        summary_parts.append(f"Bounty: ${bounty}")
    summary_parts.append(f"State: {report.get('substate') or 'disclosed'}")

    return Item(
        title=title,
        url=url,
        summary=" | ".join(summary_parts),
        source=SOURCE,
        published_at=parse_dt(
            report.get("disclosed_at") or node.get("latest_disclosable_activity_at")
        ),
        extra={
            "team_handle": team.get("handle"),
            "severity": severity,
            "bounty_amount": bounty,
        },
    )


def fetch() -> list[Item]:
    """Return recent disclosed HackerOne reports. Fail-soft on error."""
    payload = {"query": QUERY, "variables": {"first": PAGE_SIZE}}
    try:
        with http_client() as client:
            resp = client.post(ENDPOINT, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("hackerone.fetch_failed", error=str(exc))
        return []

    edges = (
        ((data.get("data") or {}).get("hacktivity_items") or {}).get("edges") or []
    )
    items: list[Item] = []
    for edge in edges:
        node = edge.get("node") or {}
        if not node:
            continue
        item = _node_to_item(node)
        if item is not None:
            items.append(item)

    log.info("hackerone.fetched", count=len(items))
    return items
