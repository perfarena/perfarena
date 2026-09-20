"""Runtime-editable settings: a small registry of tunable knobs whose value an
admin can change from the Variables tab WITHOUT an env change or a restart.

Resolution order at USE time: DB override (AppSetting row) → environment variable
→ code default. A missing DB row reproduces exactly the legacy env/default
behaviour, so an empty AppSetting table changes nothing.

Only knobs listed in REGISTRY are editable; everything else is ignored. Values
are stored as strings and cast per the registry entry's `type`.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from sqlmodel import Session, select

from .db import engine
from .models import AppSetting

# --- registry -----------------------------------------------------------------
# Each entry: key, label, type, env var, code default, group, help.
# `default` MUST mirror the code default in dispatch.py so an unset row behaves
# identically to today.
REGISTRY: list[dict[str, Any]] = [
    {"key": "dispatch.repeats", "label": "Measurement repeats", "type": "int",
     "env": "ARENA_DISPATCH_REPEATS", "default": 3, "group": "Dispatch",
     "min": 1, "max": 25,
     "help": "Measurement/correctness repeats per attempt for harnesses that "
             "don't set their own config.repeats (a per-harness repeats value "
             "still overrides this)."},
    {"key": "dispatch.tle_retries", "label": "TLE re-submits", "type": "int",
     "env": "ARENA_DISPATCH_TLE_RETRIES", "default": 3, "group": "Dispatch",
     "min": 0, "max": 10,
     "help": "On a Time Limit Exceeded verdict, re-submit up to this many extra "
             "times before accepting it (TLE is flaky on shared judge hardware)."},
    {"key": "dispatch.correctness_gate_retries", "label": "Correctness-gate retries",
     "type": "int", "env": "ARENA_CORRECTNESS_RETRIES", "default": 1,
     "group": "Dispatch", "min": 0, "max": 10,
     "help": "Extra retries for a TRANSIENT correctness-gate failure (LC outage "
             "/ runner blip) before giving up. Real verdicts never retry."},
    {"key": "dispatch.inter_delay_s", "label": "Per-account inter-submit gap (s)",
     "type": "float", "env": "ARENA_DISPATCH_INTER_DELAY", "default": 120.0,
     "group": "Dispatch", "min": 0, "max": 3600,
     "help": "Minimum seconds between LeetCode submissions on the same account "
             "(anti-abuse pacing)."},
]
_BY_KEY = {e["key"]: e for e in REGISTRY}

# --- tiny cache so we don't hit the DB on every dispatch decision -------------
_CACHE_TTL_S = 3.0
_cache: dict[str, str] = {}
_cache_at: float = 0.0


def _load_overrides() -> dict[str, str]:
    """All AppSetting rows as {key: value}, cached for _CACHE_TTL_S. On any DB
    error return the last cache (fail-safe: never break dispatch over settings)."""
    global _cache, _cache_at
    now = time.monotonic()
    if now - _cache_at < _CACHE_TTL_S:
        return _cache
    try:
        with Session(engine) as s:
            rows = s.exec(select(AppSetting)).all()
        _cache = {r.key: r.value for r in rows}
        _cache_at = now
    except Exception:  # noqa: BLE001 — settings must never wedge dispatch
        pass
    return _cache


def _invalidate() -> None:
    global _cache_at
    _cache_at = 0.0


def _raw(key: str) -> str | None:
    """DB override → env → None."""
    ov = _load_overrides().get(key)
    if ov is not None:
        return ov
    entry = _BY_KEY.get(key)
    if entry and entry.get("env"):
        ev = os.environ.get(entry["env"])
        if ev is not None:
            return ev
    return None


def _cast(entry: dict, raw: str) -> Any:
    t = entry["type"]
    if t == "int":
        return int(float(raw))
    if t == "float":
        return float(raw)
    if t == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return raw


def get_int(key: str, default: int) -> int:
    raw = _raw(key)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float) -> float:
    raw = _raw(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def effective(key: str) -> Any:
    """The current effective value using the registry default."""
    entry = _BY_KEY[key]
    raw = _raw(key)
    if raw is None:
        return entry["default"]
    try:
        return _cast(entry, raw)
    except (TypeError, ValueError):
        return entry["default"]


def all_effective() -> list[dict[str, Any]]:
    """Registry + current effective value + source, for the admin tab."""
    overrides = _load_overrides()
    out = []
    for e in REGISTRY:
        ov = overrides.get(e["key"])
        env = os.environ.get(e["env"]) if e.get("env") else None
        source = ("override" if ov is not None
                  else "env" if env is not None else "default")
        out.append({**{k: e[k] for k in
                       ("key", "label", "type", "group", "help", "default",
                        "env") if k in e},
                    "min": e.get("min"), "max": e.get("max"),
                    "value": effective(e["key"]), "source": source,
                    "override": ov})
    return out


def set_value(session: Session, key: str, raw_value: Any,
              user_id: int | None = None) -> Any:
    """Upsert an override (validated + cast). Pass raw_value=None to CLEAR the
    override (revert to env/default). Returns the new effective value."""
    from datetime import datetime
    entry = _BY_KEY.get(key)
    if entry is None:
        raise ValueError(f"unknown setting {key!r}")
    row = session.get(AppSetting, key)
    if raw_value is None or raw_value == "":
        if row is not None:
            session.delete(row); session.commit()
        _invalidate()
        return effective(key)
    val = _cast(entry, str(raw_value))          # validate/cast (raises on bad)
    if entry["type"] in ("int", "float"):
        lo, hi = entry.get("min"), entry.get("max")
        if lo is not None and val < lo:
            raise ValueError(f"{key}: {val} < min {lo}")
        if hi is not None and val > hi:
            raise ValueError(f"{key}: {val} > max {hi}")
    sval = str(val)
    if row is None:
        row = AppSetting(key=key, value=sval, updated_by_user_id=user_id)
    else:
        row.value = sval; row.updated_at = datetime.utcnow()
        row.updated_by_user_id = user_id
    session.add(row); session.commit()
    _invalidate()
    return effective(key)
