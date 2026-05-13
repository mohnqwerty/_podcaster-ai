"""End-to-end-ish tests for the FastAPI dashboard.

These run with an in-process sqlite DB under a tmp data dir, so no real
network or shell pipeline is invoked.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date as date_cls
from pathlib import Path

# Add src to path for testing.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Each test gets a fresh data dir, fresh settings cache, fresh DB."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USER", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "correct horse battery staple!!")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("DASHBOARD_LOGIN_RATE_LIMIT", "10000/minute")

    # Clear all cached singletons and module state so settings re-read env.
    from podcaster_ai import config as _cfg

    _cfg.get_settings.cache_clear()

    # Force the web.db engine to be rebuilt against this DATA_DIR.
    import podcaster_ai.web.db as _db

    _db._engine = None  # type: ignore[attr-defined]
    _db._session_factory = None  # type: ignore[attr-defined]

    yield

    _db._engine = None  # type: ignore[attr-defined]
    _db._session_factory = None  # type: ignore[attr-defined]
    _cfg.get_settings.cache_clear()


def _client():
    """Build a TestClient that runs lifespan (so bootstrap seeds the admin)."""
    from fastapi.testclient import TestClient
    from podcaster_ai.web.main import create_app

    app = create_app()
    return TestClient(app)


def _login(client, username="admin", password="correct horse battery staple!!"):
    # First GET to obtain the CSRF cookie.
    r = client.get("/login")
    assert r.status_code == 200
    csrf = client.cookies.get("podcaster_csrf")
    assert csrf
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf},
        follow_redirects=False,
    )


def test_healthz_open():
    with _client() as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.text == "ok"


def test_unauth_root_redirects_to_login():
    with _client() as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"


def test_episodes_requires_login():
    with _client() as c:
        # Browser-style request: HTML accept header => redirect to /login.
        r = c.get("/episodes", follow_redirects=False, headers={"Accept": "text/html"})
        assert r.status_code in (302, 401)
        if r.status_code == 302:
            assert r.headers["location"] == "/login"


def test_login_csrf_required():
    with _client() as c:
        # Skip the GET so we never get the CSRF cookie.
        r = c.post(
            "/login",
            data={"username": "admin", "password": "x", "csrf_token": "wrong"},
            follow_redirects=False,
        )
        assert r.status_code == 400


def test_login_wrong_password():
    with _client() as c:
        c.get("/login")
        csrf = c.cookies.get("podcaster_csrf")
        r = c.post(
            "/login",
            data={"username": "admin", "password": "nope", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 401


def test_login_success_and_session_cookie():
    with _client() as c:
        r = _login(c)
        assert r.status_code == 303
        assert r.headers["location"] == "/episodes"
        assert "podcaster_session" in c.cookies

        # Now fetch /episodes with the cookie.
        r2 = c.get("/episodes")
        assert r2.status_code == 200
        assert "Episodes" in r2.text


def test_episode_detail_404_for_unknown_date():
    with _client() as c:
        _login(c)
        r = c.get("/episodes/2099-01-01", follow_redirects=False)
        assert r.status_code == 404


def test_episode_detail_path_traversal_blocked():
    with _client() as c:
        _login(c)
        # The route only matches a valid YYYY-MM-DD; anything else 404s.
        r = c.get("/episodes/..%2Fetc%2Fpasswd", follow_redirects=False)
        assert r.status_code == 404


def test_admin_pages_require_admin():
    """A viewer-role user must NOT see /admin/users or /sources."""
    import asyncio

    from podcaster_ai.web import auth as auth_helpers
    from podcaster_ai.web.db import get_session_factory, init_models
    from podcaster_ai.web.models import User

    async def _seed_viewer():
        await init_models()
        factory = get_session_factory()
        async with factory() as session:
            session.add(
                User(
                    username="bob",
                    password_hash=auth_helpers.hash_password("a-long-enough-pw-12345"),
                    role="viewer",
                )
            )
            await session.commit()

    asyncio.run(_seed_viewer())

    with _client() as c:
        r = _login(c, username="bob", password="a-long-enough-pw-12345")
        assert r.status_code == 303
        r2 = c.get("/admin/users", follow_redirects=False, headers={"Accept": "text/html"})
        assert r2.status_code == 403
        r3 = c.get("/sources", follow_redirects=False, headers={"Accept": "text/html"})
        assert r3.status_code == 403


def test_register_episode_roundtrip():
    """The pipeline-facing helper writes a row that the dashboard can render."""
    import asyncio

    from podcaster_ai.web.db import register_episode, get_session_factory, init_models
    from sqlalchemy import select
    from podcaster_ai.web.models import Episode

    async def _ensure_tables():
        await init_models()

    asyncio.run(_ensure_tables())

    # Create dummy artifact files under the configured data dir.
    from podcaster_ai.config import get_settings

    settings = get_settings()
    ep_dir = settings.episodes_dir()
    ep_dir.mkdir(parents=True, exist_ok=True)
    d = date_cls(2026, 5, 13)
    mp3 = ep_dir / f"{d.isoformat()}.mp3"
    md = ep_dir / f"{d.isoformat()}.md"
    mp3.write_bytes(b"ID3\x00\x00fake-mp3-bytes")
    md.write_text("# Demo episode\n\nA test episode.\n", encoding="utf-8")

    register_episode(d, mp3, md, title="Demo episode")

    # Verify via SQLAlchemy that the row exists.
    async def _check():
        factory = get_session_factory()
        async with factory() as session:
            res = await session.execute(select(Episode).where(Episode.episode_date == d))
            ep = res.scalar_one_or_none()
            assert ep is not None
            assert ep.title == "Demo episode"
            return ep

    ep = asyncio.run(_check())
    assert Path(ep.mp3_path).exists()

    with _client() as c:
        _login(c)
        r = c.get(f"/episodes/{d.isoformat()}")
        assert r.status_code == 200
        assert "Demo episode" in r.text
        # Range-supported audio.
        r_aud = c.get(f"/episodes/{d.isoformat()}/audio", headers={"Range": "bytes=0-3"})
        assert r_aud.status_code == 206
        assert r_aud.headers["content-range"].startswith("bytes 0-3/")
        # Notes download.
        r_notes = c.get(f"/episodes/{d.isoformat()}/notes")
        assert r_notes.status_code == 200
        assert "Demo episode" in r_notes.text


def test_secret_redaction():
    from podcaster_ai.web.auth import redact, safe_json

    assert redact({"password": "abc", "ok": 1}) == {"password": "***", "ok": 1}
    assert "***" in safe_json({"api_key": "sk-x", "x": "y"})


def test_security_headers_present():
    with _client() as c:
        r = c.get("/login")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"
        assert r.headers.get("referrer-policy") == "same-origin"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
