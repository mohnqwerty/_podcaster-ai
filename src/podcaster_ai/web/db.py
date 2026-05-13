"""Async SQLAlchemy engine + session helpers + tiny pipeline-facing API.

The pipeline calls `register_episode(...)` after a successful run to record an
episode row. That helper is fail-soft: if SQLAlchemy or the DB file is
missing, it logs a warning and returns without raising so the standalone
pipeline keeps working when the dashboard isn't installed.
"""

from __future__ import annotations

import json
from datetime import date as date_cls, datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import structlog

from ..config import get_settings
from .models import Base, Episode

log = structlog.get_logger(__name__)

# Engine is created lazily so importing this module is cheap and side-effect-free.
_engine = None
_session_factory = None


def _build_engine_and_factory():
    """Create the async engine and session factory on first use."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    settings = get_settings()
    url = settings.effective_database_url()
    # Ensure the parent directory exists for sqlite URLs.
    if url.startswith("sqlite"):
        # sqlite+aiosqlite:////path/to/file.db  -> extract path after ":///"
        try:
            path_part = url.split(":///", 1)[1]
            Path(path_part).parent.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
    engine = create_async_engine(url, echo=False, pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        _engine, _session_factory = _build_engine_and_factory()
    return _engine


def get_session_factory():
    global _engine, _session_factory
    if _session_factory is None:
        _engine, _session_factory = _build_engine_and_factory()
    return _session_factory


async def get_session() -> AsyncIterator[Any]:
    """FastAPI dependency: yield an AsyncSession bound to the engine."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_models() -> None:
    """Create all tables. Idempotent — safe to call on every startup."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def shutdown() -> None:
    """Dispose the engine on application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


# ---------------------------------------------------------------------------
# Pipeline-facing helper
# ---------------------------------------------------------------------------

def register_episode(
    episode_date: date_cls,
    mp3_path: Path,
    md_path: Path,
    *,
    title: str = "",
    duration_seconds: Optional[int] = None,
    items: Optional[list[dict[str, Any]]] = None,
) -> None:
    """Record/upsert an episode row. Synchronous, fail-soft.

    Called from the pipeline after a successful run. Uses a short-lived
    sync sqlite connection so the pipeline does not have to depend on an
    async runtime — and it never raises: any DB issue degrades to a warning.
    """
    try:
        import sqlite3

        settings = get_settings()
        url = settings.effective_database_url()
        # Convert sqlite+aiosqlite:///PATH to plain PATH for sqlite3.
        if "sqlite" not in url:
            log.warning("register_episode.unsupported_db", url=url)
            return
        try:
            db_path = url.split(":///", 1)[1]
        except IndexError:
            log.warning("register_episode.bad_url", url=url)
            return
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        items_json = None
        if items is not None:
            try:
                items_json = json.dumps(items, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                items_json = None

        conn = sqlite3.connect(db_path, timeout=10.0)
        try:
            # Create the table schema inline so the pipeline can run before
            # the dashboard ever boots and creates tables via SQLAlchemy.
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_date DATE UNIQUE NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    mp3_path TEXT NOT NULL DEFAULT '',
                    notes_md_path TEXT NOT NULL DEFAULT '',
                    duration_seconds INTEGER,
                    items_json TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_episodes_episode_date
                    ON episodes(episode_date);
                """
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO episodes (episode_date, title, mp3_path, notes_md_path,
                                      duration_seconds, items_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_date) DO UPDATE SET
                    title=excluded.title,
                    mp3_path=excluded.mp3_path,
                    notes_md_path=excluded.notes_md_path,
                    duration_seconds=excluded.duration_seconds,
                    items_json=excluded.items_json
                """,
                (
                    episode_date.isoformat(),
                    title,
                    str(mp3_path),
                    str(md_path),
                    duration_seconds,
                    items_json,
                    now_iso,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        log.info(
            "register_episode.ok",
            date=episode_date.isoformat(),
            mp3=str(mp3_path),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("register_episode.failed", error=str(exc))


# Re-export Episode so callers can `from podcaster_ai.web.db import Episode`.
__all__ = [
    "get_engine",
    "get_session",
    "get_session_factory",
    "init_models",
    "shutdown",
    "register_episode",
    "Episode",
]
