"""API-key auth.

Approved users get a long random API key. Submitters pass it via
`Authorization: Bearer <key>`. Admins additionally have `role=admin`.
"""
from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from .db import get_session
from .models import User, UserRole, UserStatus


def generate_api_key() -> str:
    return "arena_" + secrets.token_urlsafe(32)


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


def _user_from_header(authorization: str | None, session: Session) -> User | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    user = session.exec(select(User).where(User.api_key == token)).first()
    if user and user.status == UserStatus.approved:
        return user
    return None


def current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: Session = Depends(get_session),
) -> User:
    user = _user_from_header(authorization, session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key")
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
