"""Startup-time bootstrap: create tables, seed initial admin if requested."""

from __future__ import annotations

import structlog
from sqlalchemy import select

from ..config import get_settings
from . import auth as auth_helpers
from .db import get_session_factory, init_models
from .models import User

log = structlog.get_logger(__name__)


async def bootstrap() -> None:
    """Create tables and seed the initial admin user if env-configured.

    Idempotent: if a user with the bootstrap username already exists, this
    function does nothing — it never overwrites an existing password.
    """
    await init_models()

    settings = get_settings()
    user = settings.bootstrap_admin_user
    pw = settings.bootstrap_admin_password
    if not user or not pw:
        log.info("bootstrap.no_admin_seed", reason="BOOTSTRAP_ADMIN_USER/PASSWORD not set")
        return

    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(User).where(User.username == user))
        ).scalar_one_or_none()
        if existing is not None:
            log.info("bootstrap.admin_exists", username=user)
            return
        admin = User(
            username=user,
            password_hash=auth_helpers.hash_password(pw),
            role="admin",
            disabled=False,
        )
        session.add(admin)
        await session.commit()
        log.info("bootstrap.admin_created", username=user)
