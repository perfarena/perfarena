"""Run events (Observer). The executor publishes; reporters subscribe: the
console, a JSONL file, an in-memory list (tests, MCP jobs)."""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, TextIO

from ..generate import now_iso

# Event kinds, in the order a cell produces them.
KINDS = ("run_started", "cell_started", "cell_skipped", "generated", "generation_failed",
         "submitted", "verdict", "dispatch_failed", "poll_timeout", "cell_done",
         "run_done")


@dataclass
class Event:
    kind: str
    data: dict = field(default_factory=dict)
    at: str = field(default_factory=now_iso)


Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self, *subscribers: Subscriber):
        self._subs = list(subscribers)
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        self._subs.append(fn)

    def emit(self, kind: str, **data) -> None:
        ev = Event(kind, data)
        with self._lock:
            for fn in self._subs:
                fn(ev)


class Collector:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, ev: Event) -> None:
        self.events.append(ev)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]


class JsonlReporter:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def __call__(self, ev: Event) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(ev), default=str) + "\n")


class ConsoleReporter:
    """One line per meaningful event."""

    def __init__(self, stream: TextIO | None = None):
        self.stream = stream or sys.stderr

    def __call__(self, ev: Event) -> None:
        d = ev.data
        cell = d.get("cell", "")
        text = {
            "run_started": lambda: f"run {d.get('run_id')}: {d.get('cells')} cell(s) "
                                   f"to do, {d.get('skipped')} already done",
            "cell_started": lambda: f"→ {cell}",
            "cell_skipped": lambda: f"  skip {cell}: {d.get('reason')}",
            "generated": lambda: f"  generated {d.get('chars')} chars "
                                 f"(round {d.get('round')}){' [cache]' if d.get('cached') else ''}",
            "generation_failed": lambda: f"  generation failed: {d.get('error')}",
            "submitted": lambda: f"  submitted attempt {d.get('attempt_id')} "
                                 f"to submission {d.get('submission_id')}",
            "verdict": lambda: f"  round {d.get('round')}: {d.get('verdict')}",
            "dispatch_failed": lambda: f"  dispatch failed: {d.get('warning')}",
            "poll_timeout": lambda: f"  no verdict within {d.get('timeout_s')}s",
            "run_done": lambda: f"run done: {d.get('completed')} complete, "
                                f"{d.get('skipped')} skipped, {d.get('failed')} failed",
        }.get(ev.kind)
        if text:
            print(text(), file=self.stream, flush=True)
