"""FastAPI application factory and routes for the dashboard."""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, Optional

import structlog
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Login rate limit. Overridable via env so tests can crank it up.
_LOGIN_RATE_LIMIT = os.environ.get("DASHBOARD_LOGIN_RATE_LIMIT", "5/minute")
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import configure_logging, get_settings
from . import auth as auth_helpers
from . import jobs as jobs_mod
from .bootstrap import bootstrap
from .db import get_session, shutdown
from .deps import get_current_user, require_admin, require_user
from .models import AuditLog, Episode, Job, User

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths and shared instances
# ---------------------------------------------------------------------------

_THIS_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _THIS_DIR / "templates"
_STATIC_DIR = _THIS_DIR / "static"

_md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

limiter = Limiter(key_func=get_remote_address)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add a small set of always-on hardening headers."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # Set HSTS only when the upstream proxy says we're on HTTPS, so we
        # don't break local development over plain HTTP.
        if request.headers.get("x-forwarded-proto", "").lower() == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            )
        return response


class CSRFCookieMiddleware(BaseHTTPMiddleware):
    """Ensure every browser session has a CSRF cookie set on GET responses.

    We only seed it on safe methods so POST handlers can rely on the cookie
    being present already.
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        response: Response = await call_next(request)
        if request.method in {"GET", "HEAD"} and response.status_code < 500:
            cookie_name = settings.csrf_cookie_name
            if cookie_name not in request.cookies:
                response.set_cookie(
                    cookie_name,
                    auth_helpers.new_csrf_token(),
                    httponly=False,  # JS / form template needs to read it
                    samesite="lax",
                    secure=settings.session_cookie_secure,
                    max_age=60 * 60 * 24,
                )
        return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if fwd:
        return fwd
    return request.client.host if request.client else ""


async def _audit(
    session: AsyncSession,
    *,
    action: str,
    user_id: Optional[int],
    ip: str,
    details: Optional[dict[str, Any]] = None,
) -> None:
    row = AuditLog(
        user_id=user_id,
        action=action,
        ip=ip or "",
        details_json=auth_helpers.safe_json(details) if details else None,
    )
    session.add(row)
    await session.commit()


def _set_session_cookie(response: Response, settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=auth_helpers.SESSION_MAX_AGE_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response, settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        samesite="lax",
        secure=settings.session_cookie_secure,
        httponly=True,
    )


def _csrf_from_request(request: Request, form_token: Optional[str]) -> bool:
    settings = get_settings()
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    return auth_helpers.verify_csrf(cookie_token, form_token)


def _episode_paths_for(date_str: str) -> tuple[Path, Path]:
    settings = get_settings()
    base = settings.episodes_dir()
    return base / f"{date_str}.mp3", base / f"{date_str}.md"


def _safe_date(date_str: str) -> date_cls:
    if not _DATE_RE.match(date_str):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return date_cls.fromisoformat(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        try:
            await bootstrap()
        except Exception as exc:  # noqa: BLE001
            log.warning("startup.bootstrap_failed", error=str(exc))
        yield
        # Shutdown
        try:
            await shutdown()
        except Exception as exc:  # noqa: BLE001
            log.warning("shutdown.dispose_failed", error=str(exc))

    app = FastAPI(
        title="Daily Recon — Dashboard",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    # Slowapi rate-limit setup (used on /login).
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return PlainTextResponse(
            "Too many requests. Try again in a minute.",
            status_code=429,
        )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CSRFCookieMiddleware)

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Make the CSRF cookie name and current_user reachable from every template.
    def _ctx(request: Request, **extra) -> dict[str, Any]:
        s = get_settings()
        return {
            "csrf_cookie_name": s.csrf_cookie_name,
            "podcast_title": s.podcast_title,
            **extra,
        }

    def _render(request: Request, name: str, *, status_code: int = 200, **ctx) -> Response:
        return templates.TemplateResponse(
            request, name, _ctx(request, **ctx), status_code=status_code,
        )

    # ----------------------------------------------------------------------
    # Routes
    # ----------------------------------------------------------------------

    @app.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
    async def healthz() -> str:
        return "ok"

    @app.get("/", include_in_schema=False)
    async def root(user: Optional[User] = Depends(get_current_user)):
        if user is not None:
            return RedirectResponse("/episodes", status_code=302)
        return RedirectResponse("/login", status_code=302)

    # ---- Login / logout ----

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(
        request: Request,
        user: Optional[User] = Depends(get_current_user),
    ):
        if user is not None:
            return RedirectResponse("/episodes", status_code=302)
        return _render(request, "login.html", error=None)

    @app.post("/login")
    @limiter.limit(_LOGIN_RATE_LIMIT)
    async def login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
        session: AsyncSession = Depends(get_session),
    ):
        s = get_settings()
        ip = _client_ip(request)

        if not _csrf_from_request(request, csrf_token):
            await _audit(
                session, action="login.csrf_fail", user_id=None, ip=ip,
                details={"username": username},
            )
            raise HTTPException(status_code=400, detail="Invalid request")

        if not s.session_secret:
            log.warning("login.no_session_secret")
            return _render(
                request, "login.html",
                status_code=503,
                error="Server is not configured (missing SESSION_SECRET).",
            )

        # Generic failure message for both unknown user and wrong password.
        generic_error = "Invalid credentials"
        res = await session.execute(select(User).where(User.username == username))
        user = res.scalar_one_or_none()

        if user is None or user.disabled or not auth_helpers.verify_password(
            user.password_hash, password
        ):
            await _audit(
                session, action="login.fail", user_id=user.id if user else None, ip=ip,
                details={"username": username},
            )
            return _render(
                request, "login.html", status_code=401, error=generic_error,
            )

        # Opportunistic rehash if argon2 parameters drift over time.
        if auth_helpers.needs_rehash(user.password_hash):
            try:
                user.password_hash = auth_helpers.hash_password(password)
                await session.commit()
            except Exception:  # noqa: BLE001
                pass

        await _audit(
            session, action="login.ok", user_id=user.id, ip=ip,
            details={"username": username},
        )

        token = auth_helpers.make_session_token(
            s.session_secret, user_id=user.id, username=user.username, role=user.role,
        )
        response = RedirectResponse("/episodes", status_code=303)
        _set_session_cookie(response, s, token)
        return response

    @app.post("/logout")
    async def logout_post(
        request: Request,
        csrf_token: str = Form(""),
        user: Optional[User] = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ):
        s = get_settings()
        if not _csrf_from_request(request, csrf_token):
            raise HTTPException(status_code=400, detail="Invalid request")
        if user is not None:
            await _audit(
                session, action="logout", user_id=user.id, ip=_client_ip(request),
            )
        response = RedirectResponse("/login", status_code=303)
        _clear_session_cookie(response, s)
        return response

    # ---- Episodes ----

    @app.get("/episodes", response_class=HTMLResponse)
    async def episodes_list(
        request: Request,
        page: int = 1,
        user: User = Depends(require_user),
        session: AsyncSession = Depends(get_session),
    ):
        page = max(1, page)
        per_page = 20
        offset = (page - 1) * per_page

        total_res = await session.execute(select(func.count()).select_from(Episode))
        total = int(total_res.scalar() or 0)
        res = await session.execute(
            select(Episode)
            .order_by(desc(Episode.episode_date))
            .offset(offset)
            .limit(per_page)
        )
        episodes = list(res.scalars())
        total_pages = max(1, (total + per_page - 1) // per_page)
        return _render(
            request, "episodes_list.html",
            episodes=episodes, page=page, total_pages=total_pages,
            total=total, current_user=user,
        )

    @app.get("/episodes/{date_str}", response_class=HTMLResponse)
    async def episode_detail(
        request: Request,
        date_str: str,
        user: User = Depends(require_user),
        session: AsyncSession = Depends(get_session),
    ):
        d = _safe_date(date_str)
        res = await session.execute(select(Episode).where(Episode.episode_date == d))
        episode = res.scalar_one_or_none()
        if episode is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        notes_html = ""
        notes_md = ""
        try:
            md_path = Path(episode.notes_md_path)
            if md_path.exists():
                notes_md = md_path.read_text(encoding="utf-8")
                notes_html = _md.render(notes_md)
        except Exception as exc:  # noqa: BLE001
            log.warning("episode_detail.notes_read_failed", error=str(exc))
        return _render(
            request, "episode_detail.html",
            episode=episode, date_str=date_str,
            notes_html=notes_html, notes_md=notes_md, current_user=user,
        )

    @app.get("/episodes/{date_str}/audio")
    async def episode_audio(
        request: Request,
        date_str: str,
        user: User = Depends(require_user),
        session: AsyncSession = Depends(get_session),
    ):
        d = _safe_date(date_str)
        res = await session.execute(select(Episode).where(Episode.episode_date == d))
        episode = res.scalar_one_or_none()
        if episode is None or not episode.mp3_path:
            raise HTTPException(status_code=404, detail="Audio not found")
        path = Path(episode.mp3_path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Audio file missing on disk")
        return _ranged_file_response(request, path, media_type="audio/mpeg")

    @app.get("/episodes/{date_str}/notes")
    async def episode_notes(
        date_str: str,
        user: User = Depends(require_user),
        session: AsyncSession = Depends(get_session),
    ):
        d = _safe_date(date_str)
        res = await session.execute(select(Episode).where(Episode.episode_date == d))
        episode = res.scalar_one_or_none()
        if episode is None or not episode.notes_md_path:
            raise HTTPException(status_code=404, detail="Notes not found")
        path = Path(episode.notes_md_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Notes file missing on disk")
        return FileResponse(
            str(path),
            media_type="text/markdown",
            filename=f"{date_str}.md",
        )

    # ---- Sources ----

    @app.get("/sources", response_class=HTMLResponse)
    async def sources_view(
        request: Request,
        user: User = Depends(require_admin),
    ):
        s = get_settings()
        env_view = {
            "MASTODON_ACCESS_TOKEN": "set" if os.environ.get("MASTODON_ACCESS_TOKEN") else "unset",
            "MASTODON_BASE_URL": os.environ.get("MASTODON_BASE_URL", "(default)"),
            "MASTODON_HASHTAGS": os.environ.get("MASTODON_HASHTAGS", "(default)"),
            "VENDOR_RSS_FEEDS": os.environ.get("VENDOR_RSS_FEEDS", "(unset)"),
            "YOUTUBE_CHANNEL_IDS": os.environ.get("YOUTUBE_CHANNEL_IDS", "(default)"),
            "TELEGRAM_BOT_TOKEN": "set" if os.environ.get("TELEGRAM_BOT_TOKEN") else "unset",
            "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", s.llm_provider),
            "LLM_MODEL": os.environ.get("LLM_MODEL", s.llm_model),
        }
        cfg_path = s.effective_data_dir() / "sources_config.json"
        cfg_text = ""
        try:
            if cfg_path.exists():
                cfg_text = cfg_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            cfg_text = ""
        return _render(
            request, "sources.html",
            env_view=env_view, cfg_text=cfg_text,
            cfg_path=str(cfg_path), current_user=user,
        )

    @app.post("/sources")
    async def sources_save(
        request: Request,
        user: User = Depends(require_admin),
        cfg_text: str = Form(""),
        csrf_token: str = Form(""),
        session: AsyncSession = Depends(get_session),
    ):
        if not _csrf_from_request(request, csrf_token):
            raise HTTPException(status_code=400, detail="Invalid request")
        # Validate JSON before persisting.
        try:
            json.loads(cfg_text or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
        s = get_settings()
        cfg_path = s.effective_data_dir() / "sources_config.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(cfg_text, encoding="utf-8")
        await _audit(
            session, action="sources.save", user_id=user.id, ip=_client_ip(request),
            details={"path": str(cfg_path)},
        )
        return RedirectResponse("/sources", status_code=303)

    # ---- Admin: users ----

    @app.get("/admin/users", response_class=HTMLResponse)
    async def admin_users_list(
        request: Request,
        user: User = Depends(require_admin),
        session: AsyncSession = Depends(get_session),
    ):
        res = await session.execute(select(User).order_by(User.username))
        users = list(res.scalars())
        return _render(request, "admin_users.html", users=users, current_user=user)

    @app.post("/admin/users")
    async def admin_users_create(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        role: str = Form("viewer"),
        csrf_token: str = Form(""),
        user: User = Depends(require_admin),
        session: AsyncSession = Depends(get_session),
    ):
        if not _csrf_from_request(request, csrf_token):
            raise HTTPException(status_code=400, detail="Invalid request")
        if role not in {"admin", "viewer"}:
            raise HTTPException(status_code=400, detail="Invalid role")
        if len(password) < 12:
            raise HTTPException(status_code=400, detail="Password must be at least 12 chars")
        # Reject duplicate usernames at the app layer for a friendlier error.
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=400, detail="Username already exists")
        new_user = User(
            username=username,
            password_hash=auth_helpers.hash_password(password),
            role=role,
            disabled=False,
        )
        session.add(new_user)
        await session.commit()
        await _audit(
            session, action="admin.user_create", user_id=user.id, ip=_client_ip(request),
            details={"new_username": username, "role": role},
        )
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/disable")
    async def admin_users_disable(
        request: Request,
        user_id: int,
        csrf_token: str = Form(""),
        user: User = Depends(require_admin),
        session: AsyncSession = Depends(get_session),
    ):
        if not _csrf_from_request(request, csrf_token):
            raise HTTPException(status_code=400, detail="Invalid request")
        target = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        if target.id == user.id:
            raise HTTPException(status_code=400, detail="Cannot disable yourself")
        target.disabled = not target.disabled
        await session.commit()
        await _audit(
            session, action="admin.user_toggle_disable", user_id=user.id,
            ip=_client_ip(request),
            details={"target_user_id": target.id, "disabled": target.disabled},
        )
        return RedirectResponse("/admin/users", status_code=303)

    # ---- Generate / jobs ----

    @app.get("/generate", response_class=HTMLResponse)
    async def generate_get(
        request: Request,
        user: User = Depends(require_admin),
    ):
        return _render(request, "generate.html", current_user=user)

    @app.post("/generate")
    async def generate_post(
        request: Request,
        background: BackgroundTasks,
        csrf_token: str = Form(""),
        user: User = Depends(require_admin),
        session: AsyncSession = Depends(get_session),
    ):
        if not _csrf_from_request(request, csrf_token):
            raise HTTPException(status_code=400, detail="Invalid request")
        job_id = await jobs_mod.create_job(kind="generate")
        await _audit(
            session, action="job.create", user_id=user.id, ip=_client_ip(request),
            details={"job_id": job_id, "kind": "generate"},
        )
        background.add_task(jobs_mod.run_job, job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail(
        request: Request,
        job_id: int,
        user: User = Depends(require_user),
        session: AsyncSession = Depends(get_session),
    ):
        job = (
            await session.execute(select(Job).where(Job.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        log_text = jobs_mod.tail_log(job.log_path) if job.log_path else ""
        return _render(
            request, "job_detail.html",
            job=job, log_text=log_text, current_user=user,
        )

    @app.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
    async def job_log_tail(
        job_id: int,
        user: User = Depends(require_user),
        session: AsyncSession = Depends(get_session),
    ):
        """HTMX-friendly endpoint returning the tail of the log as plain text."""
        job = (
            await session.execute(select(Job).where(Job.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        text = jobs_mod.tail_log(job.log_path) if job.log_path else ""
        # Append a status footer so the client can decide whether to keep polling.
        return PlainTextResponse(
            f"{text}\n\n# status: {job.status}\n",
            headers={"X-Job-Status": job.status},
        )

    # ----------------------------------------------------------------------
    # Catch-all auth-required redirects: convert 401 from require_user into
    # a friendly redirect to /login (keeps the spec's "302 to /login" promise).
    # ----------------------------------------------------------------------
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == status.HTTP_401_UNAUTHORIZED and _wants_html(request):
            return RedirectResponse("/login", status_code=302)
        if exc.status_code == status.HTTP_403_FORBIDDEN and _wants_html(request):
            return _render(
                request, "error.html", status_code=403, code=403, message="Forbidden",
            )
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
        )

    return app


# ---------------------------------------------------------------------------
# Lower-level helpers that don't need a closure
# ---------------------------------------------------------------------------

def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or accept == ""


def _ranged_file_response(
    request: Request, path: Path, media_type: str = "application/octet-stream"
):
    """Stream a file with HTTP Range support (single-range only)."""
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(str(path), media_type=media_type, filename=path.name)

    m = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not m:
        raise HTTPException(status_code=416, detail="Invalid Range header")
    start_s, end_s = m.group(1), m.group(2)
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else file_size - 1
    if start > end or start < 0 or end >= file_size:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 64 * 1024
    length = end - start + 1

    def _iter():
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                data = fh.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(_iter(), status_code=206, media_type=media_type, headers=headers)


# Module-level app for `uvicorn podcaster_ai.web.main:app`
app = create_app()
