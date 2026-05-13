"""In-process background job runner for "generate now".

Runs the existing pipeline as a subprocess (`python -m podcaster_ai.run`) so
the long-running web process is decoupled from heavy LLM/TTS work, and so
the runtime environment seen by the pipeline can be sanitized.

OPSEC:
- The subprocess inherits a SCRUBBED environment: any var whose name matches
  a secret-looking pattern (KEY/TOKEN/SECRET/PASSWORD/...) is removed before
  the subprocess starts, so its captured stdout/stderr never contains
  credentials even if the pipeline accidentally prints `os.environ`.
  The pipeline itself loads its real secrets from `.env` via pydantic-settings,
  not from inherited shell vars, so this is safe and behavior-preserving.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog
from sqlalchemy import select

from ..config import get_settings
from .db import get_session_factory
from .models import Job

log = structlog.get_logger(__name__)


# Names matching this pattern are removed from the subprocess environment.
_SECRETISH_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|BOT|DSN|CREDENTIAL)",
    re.IGNORECASE,
)
# Allow-list a handful of harmless names even if they happen to match the regex.
_ENV_ALLOW = {"PATH", "LANG", "LC_ALL", "TZ", "HOME", "USER", "PYTHONPATH"}


def _sanitized_env() -> dict[str, str]:
    """Return a copy of os.environ with obvious secrets stripped."""
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        if k in _ENV_ALLOW:
            out[k] = v
            continue
        if _SECRETISH_RE.search(k):
            continue
        out[k] = v
    return out


def _logs_dir() -> Path:
    settings = get_settings()
    p = settings.effective_data_dir() / "joblogs"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def create_job(kind: str = "generate") -> int:
    """Insert a pending job row and return its id."""
    factory = get_session_factory()
    async with factory() as session:
        job = Job(kind=kind, status="pending")
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def get_job(job_id: int) -> Optional[Job]:
    factory = get_session_factory()
    async with factory() as session:
        res = await session.execute(select(Job).where(Job.id == job_id))
        return res.scalar_one_or_none()


async def _update_job(job_id: int, **fields) -> None:
    factory = get_session_factory()
    async with factory() as session:
        res = await session.execute(select(Job).where(Job.id == job_id))
        job = res.scalar_one_or_none()
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        await session.commit()


async def run_job(job_id: int) -> None:
    """Execute the pipeline as a subprocess and stream its output to a log file.

    Designed to be scheduled via FastAPI BackgroundTasks. Any exception is
    captured and recorded as 'failed' instead of bubbling up.
    """
    log_path = _logs_dir() / f"job-{job_id}.log"
    started = datetime.now(timezone.utc)
    await _update_job(job_id, status="running", started_at=started, log_path=str(log_path))

    cmd = [sys.executable, "-m", "podcaster_ai.run"]
    env = _sanitized_env()

    rc = 1
    try:
        with log_path.open("wb") as fh:
            fh.write(
                f"# job {job_id} started {started.isoformat()}\n# cmd: {' '.join(cmd)}\n".encode()
            )
            fh.flush()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                env=env,
            )
            rc = await proc.wait()
    except Exception as exc:  # noqa: BLE001
        log.warning("jobs.run_failed", job_id=job_id, error=str(exc))
        try:
            with log_path.open("ab") as fh:
                fh.write(f"\n# job runner exception: {exc}\n".encode())
        except Exception:  # noqa: BLE001
            pass

    finished = datetime.now(timezone.utc)
    status = "succeeded" if rc == 0 else "failed"
    await _update_job(job_id, status=status, finished_at=finished)
    log.info("jobs.finished", job_id=job_id, status=status, rc=rc)


def tail_log(path: str | Path, max_bytes: int = 64 * 1024) -> str:
    """Return the last `max_bytes` of a log file as text. Empty on error."""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            if size > max_bytes:
                fh.seek(-max_bytes, 2)
            data = fh.read()
        return data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
