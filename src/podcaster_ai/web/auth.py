"""Authentication primitives: Argon2id password hashing, signed sessions, CSRF.

OPSEC notes:
- Passwords are hashed with Argon2id via argon2-cffi using its strong defaults.
- Session cookies are signed (and timestamped) with itsdangerous so a leaked
  cookie cannot be forged or extended past `max_age`.
- CSRF uses the double-submit cookie pattern: a random token lives in a
  cookie AND must be echoed back as a hidden form field on every POST.
"""

from __future__ import annotations

import hmac
import json
import secrets
from typing import Any, Optional

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

log = structlog.get_logger(__name__)

# argon2-cffi's PasswordHasher() ships sensible defaults (Argon2id, time_cost=3,
# memory_cost=64MB, parallelism=4) — strong enough for an interactive login flow.
_HASHER = PasswordHasher()

SESSION_MAX_AGE_SECONDS = 60 * 60 * 12  # 12h


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password with Argon2id. Never log the input."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    return _HASHER.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify a password against an Argon2id hash. Returns False on any error.

    Constant-time inside argon2-cffi; we never branch on the input value.
    """
    if not stored_hash or not password:
        return False
    try:
        return _HASHER.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHash, Exception):  # noqa: BLE001
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Session tokens (signed cookies)
# ---------------------------------------------------------------------------

def _serializer(secret: str) -> URLSafeTimedSerializer:
    if not secret or len(secret) < 16:
        raise ValueError("SESSION_SECRET must be at least 16 characters")
    return URLSafeTimedSerializer(secret_key=secret, salt="podcaster-ai-session-v1")


def make_session_token(secret: str, *, user_id: int, username: str, role: str) -> str:
    """Encode the user identity into a signed, timestamped token."""
    payload = {"uid": int(user_id), "u": str(username), "r": str(role)}
    return _serializer(secret).dumps(payload)


def read_session_token(secret: str, token: Optional[str]) -> Optional[dict[str, Any]]:
    """Decode and validate a session token. Returns None if invalid/expired."""
    if not token:
        return None
    try:
        data = _serializer(secret).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    return data


# ---------------------------------------------------------------------------
# CSRF (double-submit cookie pattern)
# ---------------------------------------------------------------------------

def new_csrf_token() -> str:
    """Generate a 32-byte URL-safe random CSRF token."""
    return secrets.token_urlsafe(32)


def verify_csrf(cookie_token: Optional[str], form_token: Optional[str]) -> bool:
    """Constant-time compare of cookie vs form value."""
    if not cookie_token or not form_token:
        return False
    try:
        return hmac.compare_digest(str(cookie_token), str(form_token))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Log redaction
# ---------------------------------------------------------------------------

_REDACT_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
}


def redact(value: Any) -> Any:
    """Walk a JSON-ish structure and replace obvious secret fields with '***'."""
    if isinstance(value, dict):
        return {
            k: ("***" if k.lower() in _REDACT_KEYS else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def safe_json(value: Any) -> str:
    try:
        return json.dumps(redact(value), ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return "{}"
