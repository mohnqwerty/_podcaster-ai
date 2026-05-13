"""Request-scoped dependencies: current user, role checks."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from . import auth as auth_helpers
from .db import get_session
from .models import User


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Optional[User]:
    """Return the logged-in User or None.

    Reads the signed session cookie, decodes it, and re-fetches the user from
    the DB so a disabled flag flip takes effect on the very next request.
    """
    settings = get_settings()
    if not settings.session_secret:
        return None
    cookie_value = request.cookies.get(settings.session_cookie_name)
    payload = auth_helpers.read_session_token(settings.session_secret, cookie_value)
    if not payload:
        return None
    uid = payload.get("uid")
    if not isinstance(uid, int):
        return None
    res = await session.execute(select(User).where(User.id == uid))
    user = res.scalar_one_or_none()
    if user is None or user.disabled:
        return None
    return user


def require_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user
