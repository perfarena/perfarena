"""Per-(IP, endpoint) rate limit for read traffic.

Why: the curated-platform pivot opened up every GET to the community (see
test_access_control_policy.py). That's a feature, but it also opens the
door to scrapers and accidental request storms hammering the database. A
modest per-IP ceiling on GETs is the simplest defence-in-depth — it bounds
worst-case load without inconveniencing real readers.

Mechanism: a sliding-window counter keyed by `(client_ip, request_path)`.
Each GET pushes a timestamp into the bucket for its IP/path pair; on
every check we drop timestamps older than the window. If the deque length
exceeds the cap the request is refused with a 429 and a `Retry-After`
header pointing at the oldest in-window hit.

Per-ENDPOINT (rather than global per-IP) means one IP scraping /problems
doesn't lock the same IP out of /rankings or /competitions — each endpoint
has its own budget. The trade-off: an attacker who walks every endpoint
can extract more total traffic than under the old global cap. That's
acceptable for community-readable data; the per-endpoint write paths
remain admin-only via Bearer auth, untouched by this limiter.

Note: the "path" here is the raw request URL path (e.g. `/api/problems/two-sum`).
Each problem slug therefore gets its own bucket — generous but bounded.

Knobs (env vars):
  * ARENA_IP_RATE_LIMIT_ENABLED   "1" (default) | "0" to disable entirely.
  * ARENA_IP_RATE_LIMIT_PER_MIN   default 60 — GETs / IP / endpoint / 60 s.
  * ARENA_IP_RATE_LIMIT_WINDOW_S  default 60 — window length in seconds.
  * ARENA_TRUST_PROXY             "1" → honour X-Forwarded-For. Default off
                                  so requests behind an untrusted proxy
                                  can't spoof their source IP.

In-memory only — fine for a single-process FastAPI deployment. A multi-
worker setup would want Redis (the contract is the same: key → count + ttl).
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status
from starlette.requests import Request

# ---------- config ----------------------------------------------------------
#
# Held in module-level mutable state so `configure(...)` can flip them at
# runtime (tests, ops dial-down). Defaults are read from env once at import.

_ENABLED = os.environ.get("ARENA_IP_RATE_LIMIT_ENABLED", "1") != "0"
_PER_MIN = int(os.environ.get("ARENA_IP_RATE_LIMIT_PER_MIN", "60"))
_WINDOW_S = float(os.environ.get("ARENA_IP_RATE_LIMIT_WINDOW_S", "60"))
_TRUST_PROXY = os.environ.get("ARENA_TRUST_PROXY", "0") == "1"


def configure(
    *,
    enabled: bool | None = None,
    per_min: int | None = None,
    window_s: float | None = None,
    trust_proxy: bool | None = None,
) -> None:
    """Update the rate-limit policy at runtime. Each param defaults to None
    meaning 'leave unchanged' so partial reconfigs (e.g. tests flipping just
    `per_min`) don't need to repeat the others."""
    global _ENABLED, _PER_MIN, _WINDOW_S, _TRUST_PROXY
    if enabled is not None:     _ENABLED = enabled
    if per_min is not None:     _PER_MIN = per_min
    if window_s is not None:    _WINDOW_S = window_s
    if trust_proxy is not None: _TRUST_PROXY = trust_proxy

# Paths that should never count or be blocked. These are either too cheap
# to bother metering (FastAPI's auto-docs) or important enough to the sign-in
# flow that throttling them would be self-defeating (captcha).
_EXEMPT_PATH_PREFIXES = (
    "/docs", "/redoc", "/openapi.json",
    "/api/captcha",
)

# ---------- state -----------------------------------------------------------

# (ip, path) -> deque of float timestamps, oldest-first.
# Bounded indirectly by the purge step + the per-(ip, endpoint) cap; in
# steady state each deque holds at most _PER_MIN entries for one bucket.
_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
# Global mutex — Python's GIL would serialise dict reads/writes for us, but
# the deque append + len check below is not atomic across multiple readers.
# A single short-held lock keeps the math correct without measurable cost.
_lock = Lock()
# Time of the last full-table purge. The purge is O(ips); doing it on every
# request would be wasteful, so we throttle it to once per window.
_last_purge = 0.0


# ---------- helpers ---------------------------------------------------------

def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honours X-Forwarded-For only when
    ARENA_TRUST_PROXY=1; otherwise an attacker could forge the header to
    bypass the rate limit by rotating fake source IPs."""
    if _TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_exempt(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES)


def _purge_if_due(now: float) -> None:
    """Drop expired hits across every tracked (ip, path) bucket, then evict
    any bucket whose deque emptied. Runs at most once per window so the
    cost stays bounded."""
    global _last_purge
    if now - _last_purge < _WINDOW_S:
        return
    threshold = now - _WINDOW_S
    dead_keys: list[tuple[str, str]] = []
    for key, dq in _hits.items():
        while dq and dq[0] < threshold:
            dq.popleft()
        if not dq:
            dead_keys.append(key)
    for key in dead_keys:
        _hits.pop(key, None)
    _last_purge = now


# ---------- public API ------------------------------------------------------

def _has_auth(request: Request) -> bool:
    """Treat any request carrying credentials as authenticated for rate-limit
    purposes — we don't validate the key here (that'd hit the DB on every
    hit), but a forged Bearer that bypasses the limit still fails at the
    actual endpoint's auth check, so the threat model holds."""
    if request.headers.get("authorization"):
        return True
    # Some clients pass the key as ?api_key=… (legacy / docs examples).
    if request.query_params.get("api_key"):
        return True
    return False


def check_get_request(request: Request) -> None:
    """Enforce the per-IP GET ceiling on UNAUTHENTICATED traffic. Returns
    silently on pass; raises HTTPException(429) with Retry-After when the
    cap is exceeded. No-op for non-GET methods, exempt paths, authenticated
    requests, and when the feature is disabled.

    Authenticated GETs are exempted because (a) anonymous scraping is the
    real failure mode this guard is built for, and (b) authenticated users
    are already throttled at the write path via enforce_submission_limit."""
    if not _ENABLED:
        return
    if request.method != "GET":
        return
    if _is_exempt(request.url.path):
        return
    if _has_auth(request):
        return

    ip = _client_ip(request)
    path = request.url.path
    key = (ip, path)
    now = time.time()
    threshold = now - _WINDOW_S

    with _lock:
        _purge_if_due(now)
        dq = _hits[key]
        # Trim this bucket's deque to the window before counting — guarantees
        # accuracy even between full-table purges.
        while dq and dq[0] < threshold:
            dq.popleft()
        if len(dq) >= _PER_MIN:
            # Retry-After = seconds until the oldest in-window hit ages out.
            retry_in = max(1, int(dq[0] + _WINDOW_S - now))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"rate limit exceeded: {_PER_MIN} GETs / {int(_WINDOW_S)}s "
                    f"per IP per endpoint ({path})"
                ),
                headers={"Retry-After": str(retry_in)},
            )
        dq.append(now)


def snapshot() -> dict:
    """Tests + diagnostics: read out current per-(ip, path) counts within
    the window. Keys are formatted as `"<ip> <path>"` so the dict remains
    JSON-serialisable for any /metrics-style endpoint that might surface
    it later."""
    now = time.time()
    threshold = now - _WINDOW_S
    out: dict[str, int] = {}
    with _lock:
        for (ip, path), dq in _hits.items():
            n = sum(1 for t in dq if t >= threshold)
            if n:
                out[f"{ip} {path}"] = n
    return out


def reset() -> None:
    """Wipe in-memory state — primarily for test isolation."""
    with _lock:
        _hits.clear()
        global _last_purge
        _last_purge = 0.0
