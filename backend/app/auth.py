"""Authentication.

Two credential types, both presented via `Authorization: Bearer <token>`:

  * **API key** (``arena_...``) — long-lived, programmatic (scripts, sweeps).
    Stored HASHED at rest and shown to the user only once, at creation.
  * **Session token** (``sess.<payload>.<sig>``) — short-lived, issued by
    ``/login`` for the browser SPA. Stateless HMAC (no DB row), so it never
    persists a reusable secret and expires on its own.

`current_user` accepts either. Admins additionally have ``role=admin``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from .db import get_session
from .models import User, UserRole, UserStatus


def generate_api_key() -> str:
    return "arena_" + secrets.token_urlsafe(32)


# --- API-key hashing --------------------------------------------------------
# Keys are high-entropy (256-bit), so an unsalted SHA-256 is enough and keeps
# an O(1) equality lookup. The plaintext is shown once at creation and never
# recoverable afterward.
_HASH_PREFIX = "sha256$"


def hash_api_key(plain: str) -> str:
    return _HASH_PREFIX + hashlib.sha256(plain.encode("utf-8")).hexdigest()


def looks_hashed(stored: str | None) -> bool:
    return bool(stored) and stored.startswith(_HASH_PREFIX)


def hash_password(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- Stateless session tokens (SPA login) -----------------------------------
# Signed with ARENA_SESSION_SECRET. When unset we generate a per-process secret
# (a restart then invalidates SPA sessions and users re-login — API-key auth is
# unaffected). Set the env var in any real deployment for stable sessions.
_SESSION_SECRET = (os.environ.get("ARENA_SESSION_SECRET") or "").strip() \
    or secrets.token_urlsafe(48)
_SESSION_TTL_S = int(os.environ.get("ARENA_SESSION_TTL_S", str(30 * 24 * 3600)))
_SESSION_PREFIX = "sess."


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    return _b64e(hmac.new(_SESSION_SECRET.encode(), payload.encode(),
                          hashlib.sha256).digest())


def issue_session(user_id: int) -> str:
    payload = _b64e(json.dumps(
        {"uid": int(user_id), "exp": int(time.time()) + _SESSION_TTL_S}
    ).encode())
    return f"{_SESSION_PREFIX}{payload}.{_sign(payload)}"


def verify_session(token: str) -> int | None:
    """Return the user id for a valid, unexpired session token, else None."""
    try:
        body = token[len(_SESSION_PREFIX):]
        payload, sig = body.split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(_b64d(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return int(data["uid"])
    except Exception:                                                  # noqa: BLE001
        return None


def _user_from_header(authorization: str | None, session: Session) -> User | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    # Session token (SPA) — stateless, no DB secret.
    if token.startswith(_SESSION_PREFIX):
        uid = verify_session(token)
        if uid is None:
            return None
        user = session.get(User, uid)
    else:
        # Programmatic API key — compare the hash, never the plaintext.
        user = session.exec(
            select(User).where(User.api_key == hash_api_key(token))
        ).first()
    if user and user.status == UserStatus.approved:
        return user
    return None


def current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: Session = Depends(get_session),
) -> User:
    user = _user_from_header(authorization, session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid credentials")
    return user


def current_admin(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


def maybe_user(
    authorization: Annotated[str | None, Header()] = None,
    session: Session = Depends(get_session),
) -> User | None:
    return _user_from_header(authorization, session)
