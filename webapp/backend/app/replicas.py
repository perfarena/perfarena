"""Replica pools: N interchangeable runner endpoints behind one logical oracle.

The logical oracle (Harness = performance, CorrectnessOracle = correctness)
keeps its identity: submissions, runs, and the leaderboard reference it as
before. Its physical endpoints are OracleReplica rows. The oracle's own
`config.endpoint` is the primary replica, so a one-runner oracle is a pool of
size 1 and dispatches exactly as before.

The oracle's `config.replicas` picks how work spreads over the pool:

    {"policy": "least_busy", "hedge_after_s": 60, "quorum": 3}

Policies (stage v = correctness /validate, m = performance /measure):

  primary          v m  the primary endpoint only (the single-endpoint path)
  round_robin      v m  rotate over the free replicas
  least_busy       v m  the free replica with the lowest load, then latency
  weighted         v m  smooth weighted round robin by replica.weight
  sticky           v m  one replica per submission
  failover         v m  first healthy replica by priority, next one on failure
  hedged           v m  one replica, a second after hedge_after_s, first answer wins
  broadcast        v m  every replica; the home replica's answer counts,
                        the others are stored in ReplicaResult
  broadcast_merge  v m  every replica; v: unanimous verdict, m: runs pooled
  quorum           v    `quorum` replicas, majority verdict
  split_repeats      m  the N repeats split over the replicas, runs pooled

When `policy` is unset: `primary` while the primary is the only active
replica, else `least_busy` (correctness) or `sticky` (performance).

Comparability rule (performance only). A replica takes a submission's work
only if it measures on the same machine as the submission's earlier runs, or
every machine involved is in one machine class and admitted to it by
calibration. Every policy obeys the rule, so a policy that mixes machines
within a submission can only mix machines of one class. Broadcast shadows go
to ReplicaResult, never to Run, so they never mix either.

Back-pressure is in-process: a dispatch waits for a free slot on a replica
(`capacity` jobs per runner endpoint) instead of sending a request that the
runner answers with HTTP 429. A 429 or a transport failure cools the
endpoint for a few seconds and the policy picks another replica.
"""
from __future__ import annotations

import statistics
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable

from sqlmodel import Session, select

from . import app_settings
from . import arena_local_runner as _arena
from .models import (
    Attempt, Harness, Machine, MachineClass, OracleReplica, Problem,
    ReplicaCalibration, ReplicaResult, Run, Submission,
)

VALIDATE_POLICIES = frozenset({
    "primary", "round_robin", "least_busy", "weighted", "sticky", "failover",
    "hedged", "broadcast", "broadcast_merge", "quorum",
})
MEASURE_POLICIES = frozenset({
    "primary", "round_robin", "least_busy", "weighted", "sticky", "failover",
    "hedged", "broadcast", "broadcast_merge", "split_repeats",
})
_SINGLE = frozenset({"round_robin", "least_busy", "weighted", "sticky", "failover"})

INCONSISTENT = "Inconsistent verdict"

# Runner statuses that are not a verdict (transport, auth, busy runner).
_SYSTEM = frozenset({
    "Rate limited", "Auth failed", "Dispatch timeout", "Dispatch error",
    "Dispatch parse error", "LeetCode transient error",
})
_DOWN_S = 60.0     # skip an endpoint this long after a transport failure
_BUSY_S = 5.0      # ... and this long after it answered 429


def _log(msg: str) -> None:
    print(f"[replicas] {msg}", file=sys.stderr, flush=True)


# ---------- pool + policy ----------------------------------------------------


def _is_runner(oracle) -> bool:
    return (oracle is not None and oracle.kind in ("arena-local", "custom")
            and bool((oracle.config or {}).get("endpoint")))


def _owner(oracle):
    if isinstance(oracle, Harness):
        return OracleReplica.harness_id == oracle.id
    return OracleReplica.correctness_oracle_id == oracle.id


def ensure_primary(session: Session, oracle) -> OracleReplica | None:
    """Create the primary replica from the oracle's `config.endpoint`, or
    re-sync its endpoint after a config edit. Commits only on a change."""
    if oracle is None or oracle.id is None:
        return None
    row = session.exec(select(OracleReplica).where(
        _owner(oracle), OracleReplica.is_primary == True)).first()  # noqa: E712
    if not _is_runner(oracle):
        return row
    endpoint = oracle.config["endpoint"].rstrip("/")
    if row is None:
        is_h = isinstance(oracle, Harness)
        row = OracleReplica(
            name=f"{oracle.slug}-primary", endpoint=endpoint, is_primary=True,
            priority=0, harness_id=oracle.id if is_h else None,
            correctness_oracle_id=None if is_h else oracle.id)
        session.add(row)
        session.commit()
        session.refresh(row)
    elif row.endpoint != endpoint:
        # A new endpoint is a new box until its runs say otherwise.
        row.endpoint, row.machine_id, row.admitted_class_id = endpoint, None, None
        session.add(row)
        session.commit()
    return row


def pool(session: Session, oracle) -> list[OracleReplica]:
    """Every replica row of a runner oracle, primary first. Empty for a
    LeetCode oracle or one without an endpoint."""
    if not _is_runner(oracle):
        return []
    ensure_primary(session, oracle)
    rows = session.exec(select(OracleReplica).where(_owner(oracle))).all()
    return sorted(rows, key=lambda r: (not r.is_primary, r.priority, r.id))


def policy_for(oracle, rows: list[OracleReplica]) -> tuple[str, dict]:
    cfg = (oracle.config or {}).get("replicas") or {}
    name = cfg.get("policy")
    if not name:
        active = [r for r in rows if r.status == "active"]
        if all(r.is_primary for r in active):
            name = "primary"
        else:
            name = "sticky" if isinstance(oracle, Harness) else "least_busy"
    return name, cfg


def check_policy_config(config: dict | None, *, performance: bool) -> str | None:
    """Error text for a bad `config.replicas`, None when it is valid."""
    cfg = (config or {}).get("replicas")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        return "config.replicas must be an object"
    name = cfg.get("policy")
    allowed = MEASURE_POLICIES if performance else VALIDATE_POLICIES
    if name is not None and name not in allowed:
        return (f"config.replicas.policy {name!r} is not valid for a "
                f"{'performance' if performance else 'correctness'} oracle; "
                f"use one of {sorted(allowed)}")
    for k in ("hedge_after_s", "quorum"):
        v = cfg.get(k)
        if v is not None and (not isinstance(v, (int, float)) or v <= 0):
            return f"config.replicas.{k} must be a positive number"
    return None


def pools_enabled() -> bool:
    return app_settings.get_int("dispatch.replica_pools", 1) == 1


def _wait_s() -> float:
    return app_settings.get_float("dispatch.replica_wait_s", 900.0)


def uses_pool(session: Session, oracle) -> bool:
    """True when dispatch to `oracle` goes through a policy, False on the
    single-endpoint path."""
    if not _is_runner(oracle) or not pools_enabled():
        return False
    return policy_for(oracle, pool(session, oracle))[0] != "primary"


def _copy(obj):
    """Detached copy of a row, safe to read from worker threads. Reads each
    column with getattr, which reloads an object a commit expired
    (model_dump() would silently return its empty __dict__)."""
    cols = obj.__table__.columns.keys()
    return type(obj)(**{c: getattr(obj, c) for c in cols})


# ---------- endpoint slots, health, policy state ------------------------------
# One lock (reentrant) guards every piece of in-memory state below.

_CV = threading.Condition(threading.RLock())
_load: Counter = Counter()                     # endpoint -> jobs in flight
_cool: dict[str, tuple[float, str]] = {}       # endpoint -> (until, down|busy)
_ewma_s: dict[str, float] = {}                 # endpoint -> smoothed latency
_rr: dict[tuple, int] = {}                     # policy key -> cursor
_sww: dict[tuple, dict[int, float]] = {}       # policy key -> current weights
_sticky: dict[tuple, int] = {}                 # (policy key, sub) -> replica


def _ep(r: OracleReplica) -> str:
    return r.endpoint.rstrip("/")


def _give(ep: str) -> None:
    with _CV:
        _load[ep] -= 1
        if _load[ep] <= 0:
            del _load[ep]
        _CV.notify_all()


@contextmanager
def track(endpoint: str):
    """Count a single-endpoint call as in flight (no wait), so pooled
    dispatches sharing the endpoint see it as busy."""
    ep = endpoint.rstrip("/")
    with _CV:
        _load[ep] += 1
    try:
        yield
    finally:
        _give(ep)


def _cooling(ep: str, now: float, kind: str | None = None) -> bool:
    until, k = _cool.get(ep, (0.0, ""))
    return until > now and (kind is None or k == kind)


def _note(ep: str, outcome: str, elapsed: float | None = None) -> None:
    with _CV:
        if outcome == "ok":
            _cool.pop(ep, None)
            if elapsed is not None:
                prev = _ewma_s.get(ep)
                _ewma_s[ep] = elapsed if prev is None else 0.7 * prev + 0.3 * elapsed
        else:
            _cool[ep] = (time.monotonic() + (_DOWN_S if outcome == "down" else _BUSY_S),
                         outcome)


def _load_key(r: OracleReplica):
    return (_load[_ep(r)] / r.capacity, _ewma_s.get(_ep(r), 0.0), r.priority, r.id)


def _pick(name: str, key: tuple, sub_id: int | None,
          free: list[OracleReplica], live: list[OracleReplica]) -> OracleReplica | None:
    """Choose among `free` replicas (open slot). `live` = all usable
    candidates, free or not. None = wait for a slot."""
    if name == "round_robin":
        ids = [r.id for r in live]
        start = _rr.get(key, 0) % len(ids)
        r = min(free, key=lambda r: (ids.index(r.id) - start) % len(ids))
        _rr[key] = ids.index(r.id) + 1
        return r
    if name == "weighted":
        cw = _sww.setdefault(key, {})
        for r in free:
            cw[r.id] = cw.get(r.id, 0.0) + r.weight
        r = max(free, key=lambda r: (cw[r.id], -r.id))
        cw[r.id] -= sum(x.weight for x in free)
        return r
    if name == "sticky":
        rid = _sticky.get((key, sub_id))
        if rid is not None and any(r.id == rid for r in live):
            return next((r for r in free if r.id == rid), None)
        r = min(free, key=_load_key)
        _sticky[(key, sub_id)] = r.id
        return r
    if name == "failover":
        return live[0] if any(r.id == live[0].id for r in free) else None
    return min(free, key=_load_key)


def _acquire(name: str, key: tuple, sub_id: int | None,
             cands: Callable[[], list[OracleReplica]], deadline: float, *,
             exclude: set[int] = frozenset(),
             on_take: Callable[[OracleReplica], None] | None = None,
             ) -> OracleReplica | None:
    """Wait for a slot on a replica chosen by policy `name`. Returns the
    replica with its slot taken, or None at the deadline or when every
    candidate is down. `cands` and `on_take` run under the lock, so a
    choice and its bookkeeping are atomic across dispatch threads."""
    with _CV:
        while True:
            now = time.monotonic()
            live = [r for r in cands() if r.id not in exclude
                    and not _cooling(_ep(r), now, "down")]
            if not live:
                return None
            free = [r for r in live if not _cooling(_ep(r), now)
                    and _load[_ep(r)] < r.capacity]
            r = _pick(name, key, sub_id, free, live) if free else None
            if r is not None:
                _load[_ep(r)] += 1
                if on_take:
                    on_take(r)
                return r
            if deadline <= now:
                return None
            _CV.wait(min(deadline - now, 1.0))


def _guarded(call: Callable[[OracleReplica], Any], classify, r: OracleReplica):
    """Run `call` on `r` (slot already taken), then free the slot and record
    the outcome for health and latency."""
    t0 = time.monotonic()
    try:
        res = call(r)
    except Exception as e:  # noqa: BLE001 — a crashed call is a system failure
        res = None
        _log(f"call on {_ep(r)} raised {type(e).__name__}: {e}")
    finally:
        _give(_ep(r))
    _note(_ep(r), classify(res), time.monotonic() - t0)
    return res


def _run_single(name, key, sub_id, cands, call, classify, *, on_take=None,
                on_done=None):
    """One replica at a time. A busy or failed replica hands the job to the
    next one the policy picks, until the wait deadline."""
    deadline = time.monotonic() + _wait_s()
    tried: set[int] = set()
    last = None
    while True:
        r = _acquire(name, key, sub_id, cands, deadline, exclude=tried,
                     on_take=on_take)
        if r is None:
            return last
        res = _guarded(call, classify, r)
        if on_done:
            on_done(r)
        outcome = classify(res)
        if outcome == "ok":
            return r, res
        last = (r, res)
        if outcome == "down":
            tried.add(r.id)


def _run_hedged(key, sub_id, cands, call, classify, hedge_after_s, *,
                on_take=None, on_done=None):
    """Start on one replica. When it has not answered after hedge_after_s
    (or failed), start the same job on a second one. First answer wins; the
    loser runs to the end on its own and its answer is dropped."""
    deadline = time.monotonic() + _wait_s()
    first = _acquire("least_busy", key, sub_id, cands, deadline, on_take=on_take)
    if first is None:
        return None

    def job(r):
        res = _guarded(call, classify, r)
        if on_done:
            on_done(r)
        return res

    ex = ThreadPoolExecutor(max_workers=2)
    futs = {ex.submit(job, first): first}
    hedged, last = False, None
    try:
        while futs:
            done, _ = wait(list(futs), timeout=None if hedged else hedge_after_s,
                           return_when=FIRST_COMPLETED)
            for f in done:
                r = futs.pop(f)
                res = f.result()
                if classify(res) == "ok":
                    return r, res
                last = (r, res)
            if not hedged and (not done or not futs):
                hedged = True
                # Hedge without waiting; a failed first replica may wait.
                until = deadline if not futs else time.monotonic()
                r2 = _acquire("least_busy", key, sub_id, cands, until,
                              exclude={first.id}, on_take=on_take)
                if r2 is not None:
                    futs[ex.submit(job, r2)] = r2
        return last
    finally:
        ex.shutdown(wait=False)


def _take_specific(r: OracleReplica, deadline: float, on_take=None) -> bool:
    """Wait for a slot on one given replica."""
    with _CV:
        while True:
            now = time.monotonic()
            if _cooling(_ep(r), now, "down"):
                return False
            if not _cooling(_ep(r), now) and _load[_ep(r)] < r.capacity:
                _load[_ep(r)] += 1
                if on_take:
                    on_take(r)
                return True
            if deadline <= now:
                return False
            _CV.wait(min(deadline - now, 1.0))


def _run_fanout(targets: list[OracleReplica], call, classify, *,
                taken: set[int] = frozenset(), on_take=None, on_done=None):
    """Run `call` on every target at once; each one starts as soon as its
    slot is free. Returns [(replica, result or None)] in target order."""
    deadline = time.monotonic() + _wait_s()

    def one(r):
        if r.id not in taken and not _take_specific(r, deadline, on_take):
            return r, None
        res = _guarded(call, classify, r)
        if on_done:
            on_done(r)
        return r, res

    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
        return list(ex.map(one, targets))


# ---------- health -------------------------------------------------------------


def probe_pool(session: Session, oracle,
               rows: list[OracleReplica] | None = None) -> tuple[bool, str]:
    """Probe every active replica's /healthz at once, store the results on
    the rows, and cool the dead endpoints. ok = at least one is healthy."""
    rows = pool(session, oracle) if rows is None else rows
    active = [r for r in rows if r.status == "active"]
    if not active:
        return False, f"no active replica for {oracle.slug!r}"
    o, snaps = _copy(oracle), [_copy(r) for r in active]
    with ThreadPoolExecutor(max_workers=len(snaps)) as ex:
        res = list(ex.map(lambda r: _arena.probe_healthz(
            o, replica=r, timeout_cap_s=10.0), snaps))
    now = datetime.utcnow()
    for row, (ok, msg) in zip(active, res):
        row.last_health_at, row.last_health_ok = now, ok
        row.last_health_msg = msg[:300]
        session.add(row)
        _note(_ep(row), "ok" if ok else "down")
    session.commit()
    n_ok = sum(1 for ok, _ in res if ok)
    if n_ok:
        return True, f"{n_ok}/{len(res)} replicas healthy"
    return False, "; ".join(msg for _, msg in res)


def preflight(session: Session, oracle) -> tuple[bool, str]:
    """The dispatcher's pre-flight: the primary's /healthz on the
    single-endpoint path, any healthy replica on a pooled one."""
    if uses_pool(session, oracle):
        return probe_pool(session, oracle)
    return _arena.probe_healthz(oracle)


# ---------- correctness ------------------------------------------------------


def _classify_v(res: dict | None) -> str:
    if res is None:
        return "down"
    if not res.get("system_failure"):
        return "ok"
    return "busy" if res.get("status_msg") == "Rate limited" else "down"


def _no_replica(note: str) -> dict:
    return {"status_msg": "Dispatch error", "raw_response": {"note": note},
            "system_failure": True}


def validate(session: Session, sub: Submission, problem: Problem, code: str,
             oracle, *, attempt: Attempt | None = None,
             meta: dict | None = None) -> dict:
    """Correctness verdict for one solution, spread by the oracle's policy.
    Same return shape as arena_local_runner.validate_via_arena_local, plus
    `_replica_id` (the replica whose verdict counts) on the pooled path."""
    rows = pool(session, oracle)
    name, cfg = policy_for(oracle, rows)
    if not pools_enabled() or name == "primary":
        with track(oracle.config["endpoint"]):
            return _arena.validate_via_arena_local(
                problem, sub.language, code, oracle, meta=meta)

    # Worker threads only see plain values and detached copies.
    o, p, language = _copy(oracle), _copy(problem), sub.language
    cands = [_copy(r) for r in rows if r.status == "active"]
    key = ("v", oracle.id)

    def call(r):
        return _arena.validate_via_arena_local(
            p, language, code, o, meta=meta, replica=r)

    def tag(got):
        if got is None:
            return _no_replica(f"no correctness replica of {oracle.slug!r} was "
                               f"free within {_wait_s():.0f}s")
        r, res = got
        return {**(res or _no_replica(f"replica {r.name} failed")),
                "_replica_id": r.id}

    if name in _SINGLE:
        return tag(_run_single(name, key, sub.id, lambda: cands, call, _classify_v))
    if name == "hedged":
        return tag(_run_hedged(key, sub.id, lambda: cands, call, _classify_v,
                               float(cfg.get("hedge_after_s", 30))))

    now = time.monotonic()
    targets = sorted([r for r in cands if not _cooling(_ep(r), now, "down")],
                     key=_load_key)
    if name == "quorum":
        targets = targets[:int(cfg.get("quorum", 3))]
    if not targets:
        return _no_replica(f"every correctness replica of {oracle.slug!r} is down")
    results = _run_fanout(targets, call, _classify_v)
    if attempt is not None:
        for r, res in results:
            if res is not None and not res.get("system_failure"):
                session.add(ReplicaResult(
                    attempt_id=attempt.id, replica_id=r.id, stage="validate",
                    role="vote", status_msg=res["status_msg"],
                    total_correct=res.get("total_correct"),
                    total_testcases=res.get("total_testcases"),
                    raw_response=res.get("raw_response")))
    real = [(r, res) for r, res in results
            if res is not None and not res.get("system_failure")]
    if not real:
        return tag(next(((r, res) for r, res in results if res is not None), None))
    statuses = Counter(res["status_msg"] for _, res in real)
    if name == "broadcast":
        return tag(real[0])
    if name == "broadcast_merge" and len(statuses) == 1:
        return tag(real[0])
    if name == "quorum":
        status, n = statuses.most_common(1)[0]
        if 2 * n > len(real):
            return tag(next(x for x in real if x[1]["status_msg"] == status))
    votes = [{"replica": r.name, "status_msg": res["status_msg"],
              "total_correct": res.get("total_correct"),
              "total_testcases": res.get("total_testcases")} for r, res in real]
    _log(f"{oracle.slug}: replicas disagree on sub {sub.id} "
         f"{problem.title_slug}: {dict(statuses)}")
    return {"status_msg": INCONSISTENT, "system_failure": False,
            "raw_response": {"note": f"{name}: replicas disagree "
                                     f"({dict(statuses)})", "votes": votes}}


# ---------- performance -------------------------------------------------------


def _classify_m(runs: list[dict] | None) -> str:
    statuses = {x.get("status_msg") for x in runs or []}
    if not statuses or statuses <= _SYSTEM:
        return "busy" if statuses == {"Rate limited"} else "down"
    return "ok"


def _mkey(r: OracleReplica):
    """Machine identity of a replica. An unknown machine (never measured)
    is only identical to itself."""
    return r.machine_id if r.machine_id is not None else ("replica", r.id)


def _compatible(k, used: set, machines: dict[int, Machine],
                admitted: set[tuple[int, int]]) -> bool:
    """May machine `k` join a submission whose runs came from `used`?"""
    if not used or used == {k}:
        return True
    if not isinstance(k, int) or k not in machines:
        return False
    cls = machines[k].machine_class_id
    if cls is None:
        return False
    return all(isinstance(m, int) and m in machines
               and machines[m].machine_class_id == cls and (m, cls) in admitted
               for m in used | {k})


class _Book:
    """Machines a submission's performance runs use: confirmed (runs kept)
    plus reserved (a dispatch in flight). Lives across dispatch threads so
    two concurrent attempts of one submission cannot start on two
    incomparable machines."""

    def __init__(self) -> None:
        self.confirmed: set = set()
        self.inflight: Counter = Counter()

    def used(self) -> set:
        return self.confirmed | {k for k, n in self.inflight.items() if n > 0}

    def reserve(self, r: OracleReplica) -> None:
        self.inflight[_mkey(r)] += 1

    def release(self, r: OracleReplica) -> None:
        with _CV:
            self.inflight[_mkey(r)] -= 1


_books: dict[tuple[int, int], _Book] = {}


def _book(harness_id: int, sub_id: int) -> _Book:
    with _CV:
        return _books.setdefault((harness_id, sub_id), _Book())


def _machines_used(session: Session, sub: Submission) -> set[int]:
    used = set(session.exec(
        select(Run.machine_id).join(Attempt, Attempt.id == Run.attempt_id)
        .where(Attempt.submission_id == sub.id, Run.machine_id.isnot(None))
        .distinct()).all())
    if sub.machine_id is not None:
        used.add(sub.machine_id)
    return used


def _adopt(session: Session, r: OracleReplica, runs: list[dict]) -> int | None:
    """Stamp each run with its replica and machine, and learn the machine
    the replica measures on. A replica whose machine changed loses its
    class admission. Returns the machine id."""
    from .dispatch import _upsert_machine
    desc = next((x["machine"] for x in runs if x.get("status_msg") not in _SYSTEM
                 and isinstance(x.get("machine"), dict) and x["machine"].get("name")),
                None)
    mid = _upsert_machine(session, desc).id if desc else None
    for x in runs:
        x["_replica_id"], x["_machine_id"] = r.id, mid
    row = session.get(OracleReplica, r.id)
    if mid is not None and row is not None and row.machine_id != mid:
        if row.machine_id is not None and row.admitted_class_id is not None:
            _log(f"replica {row.name}: machine changed {row.machine_id} -> {mid}; "
                 f"quarantined until recalibrated")
            row.status, row.admitted_class_id = "quarantined", None
        row.machine_id = mid
        session.add(row)
        session.commit()
    return mid


def _fire(runs: list[dict], cb) -> list[dict]:
    if cb is not None:
        for i, run in enumerate(runs, start=1):
            cb(run, i)
    return runs


def measure(session: Session, sub: Submission, attempt: Attempt,
            problem: Problem, harness: Harness, n_repeats: int, *,
            meta: dict | None = None,
            on_run_completed: Callable[[dict, int], None] | None = None,
            ) -> list[dict]:
    """Performance runs for one attempt, spread by the harness policy under
    the comparability rule. Same return shape as
    arena_local_runner.dispatch_via_arena_local; on the pooled path every
    run also carries `_replica_id` and `_machine_id`."""
    rows = pool(session, harness)
    name, cfg = policy_for(harness, rows)
    if not pools_enabled() or name == "primary":
        with track(harness.config["endpoint"]):
            return _arena.dispatch_via_arena_local(
                problem, sub.language, attempt.code, n_repeats,
                harness=harness, on_run_completed=on_run_completed, meta=meta)

    # Worker threads only see plain values and detached copies.
    h, p, language, code = _copy(harness), _copy(problem), sub.language, attempt.code
    cands = [_copy(r) for r in rows if r.status == "active"]
    machines = {m.id: m for m in session.exec(select(Machine)).all()}
    admitted = {(r.machine_id, r.admitted_class_id) for r in rows
                if r.status == "active" and r.machine_id and r.admitted_class_id}
    book = _book(harness.id, sub.id)
    db_used = _machines_used(session, sub)
    key = ("m", harness.id)

    def compatible(r, extra=frozenset()) -> bool:
        return _compatible(_mkey(r), db_used | book.used() | set(extra),
                           machines, admitted)

    def fail(note: str) -> list[dict]:
        return _arena._synthetic_failure(n_repeats, "Dispatch error", note)

    def keep(r, runs) -> list[dict] | None:
        """Adopt `r`'s runs; None when the machine they report breaks the
        comparability rule (the runs are then dropped). The check and the
        confirmation are one step under the lock, so of two concurrent
        attempts on incomparable machines only the first one keeps its runs."""
        mid = _adopt(session, r, runs)
        k = mid if mid is not None else ("replica", r.id)
        if mid is not None and mid not in machines:
            machines[mid] = session.get(Machine, mid)
        with _CV:
            used = db_used | book.confirmed
            if not _compatible(k, used, machines, admitted):
                _log(f"replica {r.name} measured on machine {k}, not comparable "
                     f"with sub {sub.id}'s machines {sorted(map(str, used))}")
                return None
            book.confirmed.add(k)
        return runs

    with _CV:
        if (key, sub.id) not in _sticky:
            hint = session.exec(
                select(Run.replica_id).join(Attempt, Attempt.id == Run.attempt_id)
                .where(Attempt.submission_id == sub.id, Run.replica_id.isnot(None))
                .order_by(Run.id.desc())).first()
            if hint is not None:
                _sticky[(key, sub.id)] = hint

    if name in _SINGLE or name == "hedged":
        def call(r):
            return _arena.dispatch_via_arena_local(
                p, language, code, n_repeats, harness=h, meta=meta, replica=r)
        eligible = lambda: [r for r in cands if compatible(r)]  # noqa: E731
        if name == "hedged":
            got = _run_hedged(key, sub.id, eligible, call, _classify_m,
                              float(cfg.get("hedge_after_s", 300)),
                              on_take=book.reserve, on_done=book.release)
        else:
            got = _run_single(name, key, sub.id, eligible, call, _classify_m,
                              on_take=book.reserve, on_done=book.release)
        if got is None:
            return fail(f"no replica of {harness.slug!r} comparable with "
                        f"submission {sub.id}'s machines was free within "
                        f"{_wait_s():.0f}s")
        r, runs = got
        if runs is None:
            return fail(f"replica {r.name} failed")
        if _classify_m(runs) != "ok":
            return runs
        kept = keep(r, runs)
        return _fire(kept, on_run_completed) if kept is not None else fail(
            f"replica {r.name} measured on a machine not comparable with "
            f"submission {sub.id}'s earlier runs")

    # Fan-out: pick the home replica, then its mates.
    home = _acquire("least_busy", key, sub.id,
                    lambda: [r for r in cands if compatible(r)],
                    time.monotonic() + _wait_s(), on_take=book.reserve)
    if home is None:
        return fail(f"no replica of {harness.slug!r} comparable with "
                    f"submission {sub.id}'s machines was free")
    now = time.monotonic()
    others = [r for r in cands if r.id != home.id
              and not _cooling(_ep(r), now, "down")]
    if name == "broadcast":
        mates, shares = others, [n_repeats] * (1 + len(others))
    else:
        mates = [r for r in others if compatible(r, {_mkey(home)})]
        if name == "split_repeats":
            mates = sorted(mates, key=_load_key)[:max(0, n_repeats - 1)]
            k = 1 + len(mates)
            shares = [n_repeats // k + (1 if i < n_repeats % k else 0)
                      for i in range(k)]
        else:
            shares = [n_repeats] * (1 + len(mates))
    targets = [home] + mates
    share_of = {r.id: s for r, s in zip(targets, shares)}

    def call_share(r):
        return _arena.dispatch_via_arena_local(
            p, language, code, share_of[r.id], harness=h, meta=meta, replica=r)

    pooled = name != "broadcast"
    results = _run_fanout(
        targets, call_share, _classify_m, taken={home.id},
        on_take=book.reserve if pooled else None,
        on_done=lambda r: book.release(r) if (pooled or r.id == home.id) else None)
    home_runs = results[0][1]
    if _classify_m(home_runs) != "ok":
        return home_runs or fail(f"home replica {home.name} failed")
    kept_home = keep(home, home_runs)
    if kept_home is None:
        return fail(f"home replica {home.name} measured on a machine not "
                    f"comparable with submission {sub.id}'s earlier runs")

    if name == "broadcast":
        for r, runs in results[1:]:
            if _classify_m(runs) == "ok":
                _adopt(session, r, runs)
                _store_shadows(session, attempt, r, runs)
        return _fire(kept_home, on_run_completed)

    merged = list(kept_home)
    for r, runs in results[1:]:
        if _classify_m(runs) != "ok":
            _log(f"{name}: replica {r.name} failed its share for attempt "
                 f"{attempt.id}")
            if runs:
                merged.extend(x for x in runs if x.get("status_msg") in _SYSTEM)
            continue
        kept = keep(r, runs)
        if kept is not None:
            merged.extend(kept)
    return _fire(merged, on_run_completed)


def _store_shadows(session: Session, attempt: Attempt, r: OracleReplica,
                   runs: list[dict]) -> None:
    from .measurement_profile import get_or_create_profile
    for i, x in enumerate(runs, start=1):
        if x.get("status_msg") in _SYSTEM:
            continue
        prof = None
        if x.get("_machine_id") and x.get("energy_tool") and isinstance(x.get("config"), dict):
            prof = get_or_create_profile(session, x["_machine_id"],
                                         x["energy_tool"], x["config"])
        session.add(ReplicaResult(
            attempt_id=attempt.id, replica_id=r.id, machine_id=x.get("_machine_id"),
            stage="measure", role="shadow", run_index=i, status_msg=x["status_msg"],
            runtime_ms=x.get("runtime_ms"), memory_mb=x.get("memory_mb"),
            runtime_energy_j=x.get("runtime_energy_j"),
            iterations=x.get("iterations"), energy_j_per_op=x.get("energy_j_per_op"),
            time_us_per_op=x.get("time_us_per_op"),
            total_correct=x.get("total_correct"),
            total_testcases=x.get("total_testcases"),
            measurement_profile_id=prof.id if prof else None,
            raw_response=x.get("raw_response")))


# ---------- calibration (admission gate) -----------------------------------------


def calibrate(session: Session, replica_id: int, *,
              declare_class_id: int | None = None,
              set_baseline: bool = False) -> ReplicaCalibration:
    """Run the class reference workload on one performance replica and admit
    it to the class when runtime and energy land within the class tolerance
    of the baseline. The first calibration of a class (or set_baseline=True)
    sets the baseline. Out of band = quarantined. Raises ValueError on a
    setup problem and RuntimeError when the replica could not run it."""
    from .dispatch import _upsert_machine
    row = session.get(OracleReplica, replica_id)
    if row is None or row.harness_id is None:
        raise ValueError("calibration needs a performance (harness) replica")
    harness = session.get(Harness, row.harness_id)
    cls_id = declare_class_id
    if cls_id is None and row.machine_id is not None:
        cls_id = session.get(Machine, row.machine_id).machine_class_id
    if cls_id is None:
        raise ValueError(f"replica {row.name} has no machine class: declare one "
                         f"(machine_class) or set it on its machine")
    mc = session.get(MachineClass, cls_id)
    if mc is None:
        raise ValueError(f"machine class {cls_id} not found")
    cal = mc.calibration or {}
    ref = session.get(Attempt, cal.get("attempt_id") or 0)
    if ref is None:
        raise ValueError(f"class {mc.slug!r} has no calibration.attempt_id "
                         f"(the reference solution)")
    problem = session.get(Problem, ref.problem_id)
    language = session.get(Submission, ref.submission_id).language
    repeats = int(cal.get("repeats", 3))
    tol = float(cal.get("tolerance_pct", 5.0))

    snap = _copy(row)
    if not _take_specific(snap, time.monotonic() + _wait_s()):
        raise RuntimeError(f"replica {row.name} is down or stayed busy")
    try:
        runs = _arena.dispatch_via_arena_local(
            _copy(problem), language, ref.code, repeats, harness=_copy(harness),
            meta={"calibration": True, "replica_id": row.id}, replica=snap)
    finally:
        _give(_ep(snap))
    if _classify_m(runs) != "ok":
        note = (runs[0].get("raw_response") or {}).get("note") if runs else None
        raise RuntimeError(f"replica {row.name} could not run the reference: "
                           f"{runs[0].get('status_msg') if runs else '?'} {note or ''}")

    acc = [x for x in runs if x.get("status_msg") == "Accepted"]
    rts = [x["runtime_ms"] for x in acc if x.get("runtime_ms") is not None]
    ens = [x["runtime_energy_j"] for x in acc if x.get("runtime_energy_j") is not None]
    rt = statistics.median(rts) if rts else None
    en = statistics.median(ens) if ens else None
    desc = next((x["machine"] for x in acc if isinstance(x.get("machine"), dict)
                 and x["machine"].get("name")), None)
    machine = _upsert_machine(session, desc) if desc else None
    if machine is not None:
        if machine.machine_class_id is None:
            machine.machine_class_id = cls_id
        elif machine.machine_class_id != cls_id:
            raise ValueError(f"machine {machine.name} is in class "
                             f"{machine.machine_class_id}, not {mc.slug!r}")
        row.machine_id = machine.id
        session.add(machine)

    base = mc.baseline or {}
    rec = ReplicaCalibration(
        replica_id=row.id, machine_class_id=cls_id,
        machine_id=machine.id if machine else None,
        reference_attempt_id=ref.id, runtime_ms=rt, energy_j=en,
        tolerance_pct=tol, detail={"statuses": [x.get("status_msg") for x in runs],
                                   "runtime_ms": [x.get("runtime_ms") for x in runs],
                                   "energy_j": [x.get("runtime_energy_j") for x in runs]})
    if not acc or rt is None:
        rec.passed, rec.note = False, "reference solution not Accepted on this replica"
    elif set_baseline or not base.get("runtime_ms"):
        mc.baseline = {"runtime_ms": rt, "energy_j": en, "replica_id": row.id,
                       "machine_id": rec.machine_id, "repeats": repeats,
                       "set_at": datetime.utcnow().isoformat()}
        session.add(mc)
        rec.passed, rec.became_baseline = True, True
        rec.note = "set the class baseline"
    else:
        rec.baseline_runtime_ms, rec.baseline_energy_j = base["runtime_ms"], base.get("energy_j")
        d_rt = abs(rt / base["runtime_ms"] - 1) * 100
        d_en = (abs(en / base["energy_j"] - 1) * 100
                if en is not None and base.get("energy_j") else None)
        missing_energy = base.get("energy_j") is not None and en is None
        rec.passed = d_rt <= tol and not missing_energy and (d_en is None or d_en <= tol)
        rec.note = (f"runtime {d_rt:+.1f}% vs baseline"
                    + (f", energy {d_en:+.1f}%" if d_en is not None else "")
                    + (", no energy reading" if missing_energy else "")
                    + f" (tolerance {tol:g}%)")
    if rec.passed:
        row.admitted_class_id = cls_id
        if row.status == "quarantined":
            row.status = "active"
    else:
        row.admitted_class_id = None
        row.status = "quarantined"
    session.add(row)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    _log(f"calibration {row.name} in {mc.slug}: "
         f"{'admitted' if rec.passed else 'QUARANTINED'} — {rec.note}")
    return rec


def calibrate_in_background(replica_id: int, **kw) -> None:
    from .db import engine

    def job():
        try:
            with Session(engine) as s:
                calibrate(s, replica_id, **kw)
        except Exception as e:  # noqa: BLE001 — logged; the API already returned
            _log(f"calibration of replica {replica_id} failed: {e}")

    threading.Thread(target=job, name=f"calibrate-{replica_id}", daemon=True).start()


def recalibrate_due(session: Session) -> int:
    """Re-run calibration on every admitted performance replica whose last
    calibration is older than the configured interval. Returns the count."""
    hours = app_settings.get_float("replicas.recalibrate_interval_h", 24.0)
    if hours <= 0:
        return 0
    n = 0
    rows = session.exec(select(OracleReplica).where(
        OracleReplica.harness_id.isnot(None), OracleReplica.status == "active",
        OracleReplica.admitted_class_id.isnot(None))).all()
    for row in rows:
        last = session.exec(select(ReplicaCalibration)
                            .where(ReplicaCalibration.replica_id == row.id)
                            .order_by(ReplicaCalibration.id.desc())).first()
        if last and (datetime.utcnow() - last.created_at).total_seconds() < hours * 3600:
            continue
        try:
            calibrate(session, row.id)
            n += 1
        except Exception as e:  # noqa: BLE001
            _log(f"scheduled recalibration of {row.name} failed: {e}")
    return n


def start_recalibration_thread(check_every_s: float = 600.0) -> None:
    from .db import engine

    def loop():
        while True:
            time.sleep(check_every_s)
            try:
                with Session(engine) as s:
                    recalibrate_due(s)
            except Exception as e:  # noqa: BLE001
                _log(f"recalibration pass failed: {e}")

    threading.Thread(target=loop, name="replica-recalibration", daemon=True).start()
