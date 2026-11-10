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
    # Replica pools + claim-based scheduler (replicas.py, scheduler.py).
    {"key": "dispatch.replica_pools", "label": "Replica pools (0/1)", "type": "int",
     "env": "ARENA_REPLICA_POOLS", "default": 1, "group": "Replicas",
     "min": 0, "max": 1,
     "help": "1 = spread work over each runner oracle's registered replicas by "
             "its config.replicas.policy. 0 = primary endpoint only (the "
             "single-endpoint path), whatever is registered."},
    {"key": "dispatch.replica_wait_s", "label": "Max wait for a free replica (s)",
     "type": "float", "env": "ARENA_REPLICA_WAIT_S", "default": 900.0,
     "group": "Replicas", "min": 0, "max": 86400,
     "help": "How long a dispatch waits for a free, compatible replica before "
             "it gives up for now (the attempt stays pending)."},
    {"key": "dispatch.scheduler_queue", "label": "Claim-based scheduler (0/1)",
     "type": "int", "env": "ARENA_SCHEDULER_QUEUE", "default": 0,
     "group": "Replicas", "min": 0, "max": 1,
     "help": "1 = runner-oracle submissions go to the attempt work queue, "
             "processed in parallel by one worker per replica slot. 0 = the "
             "legacy sequential per-submission loop."},
    {"key": "dispatch.queue_max_tries", "label": "Queue tries per attempt",
     "type": "int", "env": "ARENA_QUEUE_MAX_TRIES", "default": 5,
     "group": "Replicas", "min": 1, "max": 50,
     "help": "Claims per attempt before a system failure (dead runner, busy "
             "pool) parks it with a dispatch warning."},
    {"key": "replicas.recalibrate_interval_h", "label": "Recalibration interval (h)",
     "type": "float", "env": "ARENA_RECALIBRATE_INTERVAL_H", "default": 24.0,
     "group": "Replicas", "min": 0, "max": 8760,
     "help": "Re-run the class reference workload on every admitted performance "
             "replica this often. A replica out of band is quarantined. 0 = off."},
    # Efficiency rankings (ranking.py): the method of the paper.
    {"key": "ranking.bootstrap_samples", "label": "Ranking bootstrap samples", "type": "int",
     "env": "ARENA_RANKING_BOOTSTRAP", "default": 10000, "group": "Rankings",
     "min": 0, "max": 20000,
     "help": "Bootstrap samples behind the tied leaders and the Pareto levels. "
             "More samples give steadier significance at a higher compute cost "
             "(the rankings are stored and recomputed in the background)."},
    {"key": "ranking.refresh_interval_s", "label": "Ranking refresh interval (s)",
     "type": "float", "env": "ARENA_RANKING_REFRESH_INTERVAL_S", "default": 3600.0,
     "group": "Rankings", "min": 60, "max": 86400,
     "help": "After new data, a stored ranking is recomputed at most this often. "
             "A change of the ranking settings recomputes it at the next request, and "
             "the Admin page can recompute every ranking at once."},
    {"key": "ranking.tie_latency_pct", "label": "Latency tie margin (%)", "type": "float",
     "env": "ARENA_RANKING_TIE_LATENCY", "default": 1.84, "group": "Rankings",
     "min": 0, "max": 50,
     "help": "Two latencies closer than this tie (half a win each). Default: the "
             "noise of a difference of two 7-run means on LeetCode. A competition can "
             "set its own margins (Competition.ranking_tie_pct)."},
    {"key": "ranking.tie_energy_pct", "label": "Energy tie margin (%)", "type": "float",
     "env": "ARENA_RANKING_TIE_ENERGY", "default": 1.84, "group": "Rankings",
     "min": 0, "max": 50,
     "help": "Two energy readings closer than this tie."},
    {"key": "ranking.tie_memory_pct", "label": "Memory tie margin (%)", "type": "float",
     "env": "ARENA_RANKING_TIE_MEMORY", "default": 9.83, "group": "Rankings",
     "min": 0, "max": 50,
     "help": "Two peak-memory readings closer than this tie (one reading per solution). "
             "Default: sqrt(2) x the largest 95th percentile of the single-reading "
             "deviation in the memory pilot (6.95%, C#)."},
    {"key": "ranking.min_cells_per_language", "label": "Min solved problems per language",
     "type": "int", "env": "ARENA_RANKING_MIN_CELLS", "default": 3, "group": "Rankings",
     "min": 1, "max": 100,
     "help": "An LLM with fewer solved problems in a language gets no rank there."},
    {"key": "ranking.min_problem_clusters", "label": "Problems needed to resample problems",
     "type": "int", "env": "ARENA_RANKING_MIN_CLUSTERS", "default": 30, "group": "Rankings",
     "min": 2, "max": 10000,
     "help": "With at least this many problems, the bootstrap resamples whole problems "
             "(the languages of a problem are correlated); with fewer, it resamples cells."},
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
