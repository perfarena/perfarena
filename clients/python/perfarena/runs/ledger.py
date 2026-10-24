"""Resumable state of a run (Repository pattern over a JSON file), and the
generation cache.

Ledger statuses (matrix_sweep's taxonomy):
  complete         a verdict landed (or the problem was already covered)
  skipped          permanently not applicable (e.g. no code snippet)
  failed           generation or submit failed; `transient` says whether a
                   later run should retry it automatically
  dispatch_failed  the server could not dispatch (runner down, no
                   LeetCode account); retried on the next run
  poll_timeout     no verdict within the wait budget; the next run resumes
                   polling the in-flight attempt instead of resubmitting
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from ..generate import now_iso


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                self._data = {}

    def get(self, key: str) -> dict | None:
        with self._lock:
            rec = self._data.get(key)
            return dict(rec) if rec else None

    def record(self, key: str, status: str, **fields) -> None:
        with self._lock:
            self._data[key] = {"status": status, "at": now_iso(), **fields}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
            tmp.replace(self.path)

    def should_skip(self, key: str, *, retry_failed: bool = False) -> bool:
        rec = self.get(key)
        if rec is None:
            return False
        status = rec.get("status")
        if status in ("complete", "skipped"):
            return True
        if status == "failed":
            return not (rec.get("transient") or retry_failed)
        return False                    # dispatch_failed, poll_timeout: retry

    def items(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}


def cache_salt(*, target: str | None, hints: bool, temperature, rounds: int,
               think: bool = True) -> str:
    """matrix_sweep._cache_salt: generation knobs outside the tuple key that
    change the code. Same string, so the two share one cache directory."""
    temp = float(temperature) if temperature is not None else None
    parts = [f"target={target or ''}", f"hints={int(bool(hints))}",
             f"temp={temp}", f"rounds={rounds}"]
    if not think:
        parts.append("think=False")
    return "|".join(parts)


class GenerationCache:
    """Generated code per cell, so a resume does not pay for the model call
    again. Same layout as matrix_sweep's sweep_cache/ (point both at one
    directory to share generations)."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def _path(self, tuple_key: str, salt: str) -> Path:
        h = hashlib.sha256((tuple_key + "\x1f" + salt).encode("utf-8")).hexdigest()
        return self.dir / f"{h[:16]}.json"

    def load(self, tuple_key: str, salt: str) -> tuple[str, dict] | None:
        p = self._path(tuple_key, salt)
        try:
            payload = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        code = payload.get("code")
        if not isinstance(code, str) or not code.strip():
            return None
        return code, payload.get("trace") or {}

    def save(self, tuple_key: str, salt: str, code: str, trace: dict) -> None:
        p = self._path(tuple_key, salt)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"tuple_key": tuple_key, "code": code,
                                       "trace": trace, "saved_at": now_iso()}, indent=2))
            tmp.replace(p)
        except OSError:
            pass                       # a lost cache entry costs one model call

    def drop(self, tuple_key: str, salt: str) -> None:
        try:
            self._path(tuple_key, salt).unlink(missing_ok=True)
        except OSError:
            pass
