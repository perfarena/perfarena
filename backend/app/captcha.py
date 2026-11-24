"""Lightweight in-process CAPTCHA + per-IP rate limit for /api/register.

Captcha:
  GET /api/captcha → {"token": str, "prompt": "What is 7 + 4?"}
  Client echoes (token, answer) on register; we verify & consume the token.

The challenge is a small arithmetic problem — enough to slow bots without a
3rd-party service. Tokens are random, single-use, and expire after 10 minutes.

Per-IP rate limit:
  ARENA_REGISTER_IP_HOURLY (default 5)
  ARENA_REGISTER_IP_DAILY  (default 20)

Both stores are process-local. If you run multiple workers, switch to Redis.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


_TTL_S = 600
_HOURLY = int(os.environ.get("ARENA_REGISTER_IP_HOURLY", "5"))
_DAILY = int(os.environ.get("ARENA_REGISTER_IP_DAILY", "20"))


@dataclass
class _Challenge:
    answer: int
    expires_at: float


_challenges: dict[str, _Challenge] = {}
_ip_hits: dict[str, list[float]] = {}


def _purge(now: float) -> None:
    expired = [k for k, c in _challenges.items() if c.expires_at < now]
    for k in expired:
        _challenges.pop(k, None)
    for ip, hits in list(_ip_hits.items()):
        cutoff = now - 86400
        fresh = [t for t in hits if t >= cutoff]
        if fresh:
            _ip_hits[ip] = fresh
        else:
            _ip_hits.pop(ip, None)


def issue() -> dict[str, str]:
    now = time.time()
    _purge(now)
    a = secrets.randbelow(15) + 2
    b = secrets.randbelow(15) + 2
    op = secrets.choice(("+", "-"))
    answer = a + b if op == "+" else a - b
    token = secrets.token_urlsafe(24)
    _challenges[token] = _Challenge(answer=answer, expires_at=now + _TTL_S)
    return {"token": token, "prompt": f"What is {a} {op} {b}?"}


def verify(token: str | None, answer: str | None) -> None:
    if not token or answer is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing captcha")
    ch = _challenges.pop(token, None)
    if ch is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "captcha expired or unknown; request a new one")
    if ch.expires_at < time.time():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "captcha expired")
    try:
        if int(str(answer).strip()) != ch.answer:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "captcha answer is wrong")
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "captcha answer must be a number")


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_register_ip_limit(request: Request) -> None:
    now = time.time()
    _purge(now)
    ip = _client_ip(request)
    hits = _ip_hits.setdefault(ip, [])

    one_hour_ago = now - 3600
    one_day_ago = now - 86400
    hourly = sum(1 for t in hits if t >= one_hour_ago)
    daily = sum(1 for t in hits if t >= one_day_ago)

    if hourly >= _HOURLY:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"too many registration attempts from this IP ({_HOURLY}/hour)",
            headers={"Retry-After": "3600"},
        )
    if daily >= _DAILY:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"daily registration cap reached for this IP ({_DAILY}/day)",
            headers={"Retry-After": "86400"},
        )

    hits.append(now)
