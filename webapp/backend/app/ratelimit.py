"""Per-user submission rate limit.

Two windows:
- COOLDOWN (default 600s): minimum seconds between two batches.
- DAILY_CAP (default 5): max batches per user per rolling 24h.

Configurable via ARENA_SUBMIT_COOLDOWN_S and ARENA_SUBMIT_DAILY_CAP.
Admins are exempt.

Master kill-switch: ARENA_RATE_LIMITS_ENABLED="0" disables ALL backend
rate limits (this module's per-user caps AND the per-IP GET cap in
app.ip_ratelimit). Default "1" (enforced). Use for local dev / debug
when you don't want to flush submissions through cooldowns. Re-read on
every request so toggling at runtime via `docker exec ... env` takes
effect without a restart.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlmodel import Session, select

from .models import Submission, User, UserRole

COOLDOWN_S = int(os.environ.get("ARENA_SUBMIT_COOLDOWN_S", "600"))
DAILY_CAP = int(os.environ.get("ARENA_SUBMIT_DAILY_CAP", "5"))


def rate_limits_enabled() -> bool:
    """Master toggle for every backend rate limit. Reads the env var
    each call so an operator can flip the flag without restarting."""
    return os.environ.get("ARENA_RATE_LIMITS_ENABLED", "1") != "0"


def enforce_submission_limit(user: User, session: Session) -> None:
    if not rate_limits_enabled():
        return
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
