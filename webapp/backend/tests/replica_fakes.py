"""Fake runner servers and DB builders for the replica-pool tests.

A FakeRunner is a real HTTP server on 127.0.0.1 that speaks the runner
protocol (/healthz, /validate, /measure) like harness_runner_server.py with
--reject-when-busy: one job at a time, HTTP 429 while busy. Each runner
reports its own machine descriptor, so pooling and comparability are
exercised end to end through arena_local_runner's real HTTP client.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlmodel import Session

from app.db import engine
from app.models import (
    Attempt, CorrectnessOracle, Harness, Machine, MachineClass, OracleReplica,
    Problem, Submission, User,
)
from sqlmodel import select


class FakeRunner:
    def __init__(self, name: str, *, verdict: str = "Accepted", delay: float = 0.0,
                 runtime_ms: float = 10.0, energy_j: float = 1.0,
                 machine: str | None = None, reject_busy: bool = True):
        self.name = name
        self.verdict = verdict
        self.delay = delay
        self.runtime_ms = runtime_ms
        self.energy_j = energy_j
        self.machine = machine or f"box-{name}-{uuid.uuid4().hex[:6]}"
        self.reject_busy = reject_busy
        self.fail = False            # answer 500
        self.calls: list[tuple[str, dict]] = []
        self.active = 0
        self.peak = 0
        self.on_request = None       # hook(path, body) run before answering
        self._lock = threading.Lock()
        runner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/healthz":
                    if runner.fail:
                        return self._send(500, {"ok": False})
                    return self._send(200, {"ok": True, "hostname": runner.machine})
                self._send(404, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                with runner._lock:
                    if runner.reject_busy and runner.active > 0:
                        return self._send(429, {"error": "busy"})
                    runner.active += 1
                    runner.peak = max(runner.peak, runner.active)
                    runner.calls.append((self.path, body))
                try:
                    if runner.on_request:
                        runner.on_request(self.path, body)
                    if runner.delay:
                        time.sleep(runner.delay)
                    if runner.fail:
                        return self._send(500, {"error": "boom"})
                    if self.path == "/validate":
                        return self._send(200, {"verdict": {
                            "status_msg": runner.verdict, "total_correct": 5,
                            "total_testcases": 5}})
                    if self.path == "/measure":
                        n = int(body.get("repeats", 1))
                        return self._send(200, {"runs": [runner.run() for _ in range(n)]})
                    self._send(404, {})
                finally:
                    with runner._lock:
                        runner.active -= 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def run(self) -> dict:
        return {"status_msg": "Accepted", "runtime_ms": self.runtime_ms,
                "memory_mb": 5.0, "runtime_energy_j": self.energy_j,
                "energy_tool": "rapl", "config": {"budget_s": 1.0},
                "machine": {"name": self.machine, "hostname": self.machine,
                            "cpu_model": "fake"}}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def n(self, path: str) -> int:
        return sum(1 for p, _ in self.calls if p == path)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def uid() -> str:
    return uuid.uuid4().hex[:8]


def make_oracles(primary: FakeRunner, *, harness_policy: dict | None = None,
                 correctness: FakeRunner | None = None,
                 oracle_policy: dict | None = None) -> tuple[int, int | None]:
    """Create a perf harness (and optionally a correctness oracle) whose
    config.endpoint (the primary replica) is the given runner."""
    with Session(engine) as s:
        cfg = {"endpoint": primary.url, "timeout_s": 30, "repeats": 2}
        if harness_policy:
            cfg["replicas"] = harness_policy
        h = Harness(slug=f"h-{uid()}", name="fake harness", kind="arena-local",
                    config=cfg)
        s.add(h)
        o = None
        if correctness is not None:
            ocfg = {"endpoint": correctness.url, "timeout_s": 30}
            if oracle_policy:
                ocfg["replicas"] = oracle_policy
            o = CorrectnessOracle(slug=f"o-{uid()}", name="fake oracle",
                                  kind="arena-local", config=ocfg)
            s.add(o)
        s.commit()
        return h.id, (o.id if o else None)


def add_replica(runner: FakeRunner, *, harness_id: int | None = None,
                oracle_id: int | None = None, **kw) -> int:
    with Session(engine) as s:
        r = OracleReplica(name=runner.name, endpoint=runner.url,
                          harness_id=harness_id, correctness_oracle_id=oracle_id, **kw)
        s.add(r)
        s.commit()
        return r.id


def primary_replica(harness_id: int | None = None, oracle_id: int | None = None) -> int:
    from app import replicas
    with Session(engine) as s:
        o = (s.get(Harness, harness_id) if harness_id
             else s.get(CorrectnessOracle, oracle_id))
        return replicas.ensure_primary(s, o).id


def make_class(*, attempt_id: int | None = None, tolerance_pct: float = 5.0) -> int:
    with Session(engine) as s:
        mc = MachineClass(slug=f"cls-{uid()}", name="fake class",
                          calibration={"attempt_id": attempt_id, "repeats": 3,
                                       "tolerance_pct": tolerance_pct}
                          if attempt_id else None)
        s.add(mc)
        s.commit()
        return mc.id


def admit(replica_id: int, runner: FakeRunner, class_id: int) -> int:
    """Put the runner's machine in the class and mark the replica admitted
    (what a passed calibration leaves behind). Returns the machine id."""
    with Session(engine) as s:
        m = s.exec(select(Machine).where(Machine.name == runner.machine)).first()
        if m is None:
            m = Machine(name=runner.machine, hostname=runner.machine)
        m.machine_class_id = class_id
        s.add(m)
        s.commit()
        r = s.get(OracleReplica, replica_id)
        r.machine_id, r.admitted_class_id = m.id, class_id
        s.add(r)
        s.commit()
        return m.id


def make_submission(harness_id: int, oracle_id: int | None = None, *,
                    n_attempts: int = 1, language: str = "python3") -> tuple[int, list[int]]:
    with Session(engine) as s:
        user = s.exec(select(User)).first()
        problems = s.exec(select(Problem).where(
            Problem.original_problem_id.is_(None))).all()
        sub = Submission(user_id=user.id, model_name=f"m-{uid()}", model_version="v1",
                         language=language, prompt_template="p", model_params={},
                         harness_id=harness_id, correctness_oracle_id=oracle_id)
        s.add(sub)
        s.commit()
        ids = []
        for i in range(n_attempts):
            a = Attempt(submission_id=sub.id,
                        problem_id=problems[i % len(problems)].id,
                        code=f"class Solution:\n    pass  # {i}\n",
                        inference_trace="t")
            s.add(a)
            s.commit()
            ids.append(a.id)
        return sub.id, ids


# ---------- fixtures (import them into a test module by name) ------------------

import pytest  # noqa: E402


def reset_replica_state() -> None:
    from app import replicas
    with replicas._CV:
        for d in (replicas._load, replicas._cool, replicas._ewma_s, replicas._rr,
                  replicas._sww, replicas._sticky, replicas._books):
            d.clear()


@pytest.fixture
def real_mode(monkeypatch):
    """Real dispatch against fake runners: no LeetCode baseline fetches, a
    short replica wait, and clean in-memory pool state."""
    from app import dispatch
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("ARENA_REPLICA_WAIT_S", "10")
    monkeypatch.setattr(dispatch, "_ensure_baselines_for_submission",
                        lambda *a, **k: None)
    reset_replica_state()
    yield
    reset_replica_state()


@pytest.fixture
def runners():
    made: list[FakeRunner] = []

    def make(name: str, **kw) -> FakeRunner:
        r = FakeRunner(name, **kw)
        made.append(r)
        return r

    yield make
    for r in made:
        r.stop()


@pytest.fixture
def admin_headers():
    """An admin whose API key is stored hashed, as auth.py expects."""
    from app.auth import hash_api_key
    from app.models import UserRole, UserStatus
    key = f"replica-admin-{uid()}"
    with Session(engine) as s:
        s.add(User(email=f"{key}@test.dev", display_name="Replica Admin",
                   role=UserRole.admin, status=UserStatus.approved,
                   api_key=hash_api_key(key)))
        s.commit()
    return {"Authorization": f"Bearer {key}"}
