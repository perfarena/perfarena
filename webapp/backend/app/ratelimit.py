"""Per-user submission rate limit.

Two windows:
- COOLDOWN (default 600s): minimum seconds between two batches.
- DAILY_CAP (default 5): max batches per user per rolling 24h.

Configurable via ARENA_SUBMIT_COOLDOWN_S and ARENA_SUBMIT_DAILY_CAP.
Admins are exempt.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlmodel import Session, select

from .models import Submission, User, UserRole

COOLDOWN_S = int(os.environ.get("ARENA_SUBMIT_COOLDOWN_S", "600"))
DAILY_CAP = int(os.environ.get("ARENA_SUBMIT_DAILY_CAP", "5"))


def enforce_submission_limit(user: User, session: Session) -> None:
    if user.role == UserRole.admin:
        return

    now = datetime.utcnow()

    latest = session.exec(
        select(Submission)
        .where(Submission.user_id == user.id)
        .order_by(Submission.created_at.desc())
    ).first()
    if latest is not None:
        delta = (now - latest.created_at).total_seconds()
        if delta < COOLDOWN_S:
            retry_in = int(COOLDOWN_S - delta)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"submission cooldown active; retry in {retry_in}s",
                headers={"Retry-After": str(retry_in)},
            )

    since = now - timedelta(hours=24)
    recent = session.exec(
        select(Submission).where(
            Submission.user_id == user.id,
            Submission.created_at >= since,
        )
    ).all()
    if len(recent) >= DAILY_CAP:
        oldest = min(s.created_at for s in recent)
        retry_in = int((oldest + timedelta(hours=24) - now).total_seconds())
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"daily submission cap reached ({DAILY_CAP}); retry in {retry_in}s",
            headers={"Retry-After": str(max(retry_in, 1))},
        )
