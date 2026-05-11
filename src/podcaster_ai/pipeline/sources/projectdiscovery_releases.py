"""Fetch recent GitHub releases for ProjectDiscovery's nuclei + nuclei-templates."""

from __future__ import annotations

from typing import Any, Final

import structlog

from .base import Item, http_client, parse_dt

log = structlog.get_logger(__name__)

REPOS: Final[tuple[str, ...]] = (
    "projectdiscovery/nuclei",
    "projectdiscovery/nuclei-templates",
)
PER_REPO_LIMIT: Final[int] = 5
SOURCE: Final[str] = "projectdiscovery"


def _releases_for(client, repo: str) -> list[Item]:
    url = f"https://api.github.com/repos/{repo}/releases"
    try:
        resp = client.get(url, params={"per_page": PER_REPO_LIMIT})
        resp.raise_for_status()
        releases: list[dict[str, Any]] = resp.json() or []
    except Exception as exc:  # noqa: BLE001
        log.warning("projectdiscovery.repo_failed", repo=repo, error=str(exc))
        return []

    items: list[Item] = []
    for rel in releases:
        if rel.get("draft"):
            continue
        tag = rel.get("tag_name") or rel.get("name") or "release"
        body = (rel.get("body") or "").strip()
        # Trim release body to a manageable summary.
        if len(body) > 1200:
            body = body[:1200].rsplit("\n", 1)[0] + "\n…(truncated)"
        items.append(
            Item(
                title=f"{repo} {tag}",
                url=rel.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}",
                summary=body or f"New release {tag} for {repo}.",
                source=SOURCE,
                published_at=parse_dt(rel.get("published_at") or rel.get("created_at")),
                extra={
                    "repo": repo,
                    "tag": tag,
                    "prerelease": bool(rel.get("prerelease")),
                },
            )
        )
    return items


def fetch() -> list[Item]:
    """Return recent ProjectDiscovery releases. Fail-soft on error."""
    items: list[Item] = []
    try:
        with http_client() as client:
            # Light-touch GitHub auth-less use; keep a small footprint.
            for repo in REPOS:
                items.extend(_releases_for(client, repo))
    except Exception as exc:  # noqa: BLE001
        log.warning("projectdiscovery.fetch_failed", error=str(exc))
        return []

    log.info("projectdiscovery.fetched", count=len(items))
    return items
