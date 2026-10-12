"""Claim-based work queue for runner-oracle dispatch.

With the `dispatch.scheduler_queue` setting at 1, dispatch_submission() does
not loop over a submission's attempts. It marks them `queued` and returns.
Worker threads, one per replica slot of the submission's harness, claim
attempts and run the same per-attempt pipeline as the legacy loop
(dispatch._run_remote_attempt: correctness gate, then /measure through the
harness's replica policy). With 0 (the default) nothing here runs.

Attempt.dispatch_state: queued -> claimed -> done | failed.

  * Claim: one guarded UPDATE (`... AND dispatch_state='queued'`), so two
    workers never take the same attempt. An expired lease is claimable too.
  * Lease: a claim holds the attempt until lease_until. Claims of a previous
    process are requeued at startup.
  * Idempotency: before every Run write the worker re-checks that it still
    holds the claim (dispatch._check_owner), so a late reply from a lost
    lease is dropped, never written. An attempt that already has Run rows
    is settled as done without a new dispatch.
  * Back-pressure: a busy pool makes the worker wait for a slot
    (replicas.py). A system failure requeues the attempt with backoff until
    dispatch.queue_max_tries, then parks it as failed with dispatch_warning.
  * Fairness: a worker takes the submission it served least recently first.
  * Finish: the worker that settles a submission's last open attempt runs
    the submission rollup (status judged).
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, update
from sqlmodel import Session, select

from . import app_settings, dispatch, replicas
from .db import engine
from .models import Attempt, Harness, Problem, Run, Submission

BOOT = uuid.uuid4().hex[:8]          # this process's claim prefix
OPEN = ("queued", "claimed")
_IDLE_EXIT_S = 30.0
_BACKOFF_BASE_S = 30.0               # retry n waits base * 2**(n-1), max 15 min

_lock = threading.Lock()
_workers: dict[int, list[threading.Thread]] = {}   # harness id -> threads
_served: dict[int, float] = {}                     # submission -> last claim
_prov: dict[int, tuple[set, list, list]] = {}      # submission -> provenance
_finalizing: set[int] = set()


def _log(msg: str) -> None:
    print(f"[scheduler] {msg}", file=sys.stderr, flush=True)


def enabled() -> bool:
    return app_settings.get_int("dispatch.scheduler_queue", 0) == 1


def _claimable(now: datetime):
    return or_(
        and_(Attempt.dispatch_state == "queued",
             or_(Attempt.not_before.is_(None), Attempt.not_before <= now)),
        and_(Attempt.dispatch_state == "claimed", Attempt.lease_until < now))


def submit(session: Session, sub: Submission, pending: list[Attempt]) -> int:
    """Queue a submission's pending attempts and make sure workers run.
    Prefailed attempts settle inline (no runner involved), as on the legacy
    real-mode path: aggregate only. Returns the number queued."""
    n = 0
    for a in pending:
        if a.prefailed_reason:
            runs = dispatch._failure_runs(
                1, a.prefailed_reason,
                "client-detected malformed response; "
                "raw model output preserved in attempt.trace")
            a.dispatch_warning = None
            dispatch._aggregate_attempt(a, runs)
        elif a.dispatch_state not in OPEN:
            a.dispatch_state, a.claimed_by = "queued", None
            a.lease_until, a.not_before, a.dispatch_tries = None, None, 0
            n += 1
        session.add(a)
    session.commit()
    _log(f"sub {sub.id}: queued {n} attempt(s)")
    if n:
        ensure_workers(sub.harness_id)
    else:
        _maybe_finalize(sub.id)
    return n


def _worker_count(session: Session, harness: Harness) -> int:
    rows = replicas.pool(session, harness)
    if replicas.uses_pool(session, harness):
        return max(1, sum(r.capacity for r in rows if r.status == "active"))
    return 1


def ensure_workers(harness_id: int) -> None:
    with Session(engine) as s:
        h = s.get(Harness, harness_id)
        if h is None:
            return
        want = _worker_count(s, h)
    with _lock:
        live = [t for t in _workers.get(harness_id, []) if t.is_alive()]
        for i in range(len(live), want):
            t = threading.Thread(target=_worker, args=(harness_id,),
                                 name=f"queue-h{harness_id}-{BOOT}-{i}", daemon=True)
            t.start()
            live.append(t)
        _workers[harness_id] = live


def _has_open(s: Session, harness_id: int) -> bool:
    return s.exec(select(Attempt.id).join(Submission, Submission.id == Attempt.submission_id)
                  .where(Submission.harness_id == harness_id,
                         Attempt.dispatch_state.in_(OPEN)).limit(1)).first() is not None


def _worker(harness_id: int) -> None:
    me = threading.current_thread().name
    idle_since = time.monotonic()
    while True:
        aid = _claim(harness_id, me)
        if aid is None:
            if time.monotonic() - idle_since > _IDLE_EXIT_S:
                with _lock, Session(engine) as s:
                    # Decide under the lock that ensure_workers takes, so an
                    # attempt queued meanwhile never finds zero workers.
                    if not _has_open(s, harness_id):
                        _workers[harness_id] = [
                            t for t in _workers.get(harness_id, [])
                            if t is not threading.current_thread()]
                        return
                idle_since = time.monotonic()
            time.sleep(1.0)
            continue
        idle_since = time.monotonic()
        try:
            _run_claimed(aid, me)
        except Exception as e:  # noqa: BLE001 — never kill the worker
            _log(f"attempt {aid}: worker error {type(e).__name__}: {e}")


def _lease_s(harness: Harness | None) -> float:
    timeout = float((harness.config or {}).get("timeout_s") or 1800) if harness else 1800
    return 2 * replicas._wait_s() + 2 * (timeout + 300) + 600


def _claim(harness_id: int, me: str) -> int | None:
    """Atomically claim the next attempt: the submission served least
    recently first, then its oldest claimable attempt."""
    now = datetime.utcnow()
    with Session(engine) as s:
        rows = s.exec(
            select(Attempt.id, Attempt.submission_id)
            .join(Submission, Submission.id == Attempt.submission_id)
            .where(Submission.harness_id == harness_id, _claimable(now))
            .order_by(Attempt.id).limit(500)).all()
        if not rows:
            return None
        first: dict[int, int] = {}
        for aid, sid in rows:
            first.setdefault(sid, aid)
        with _lock:
            order = sorted(first, key=lambda sid: _served.get(sid, 0.0))
        lease = now + timedelta(seconds=_lease_s(s.get(Harness, harness_id)))
        for sid in order:
            res = s.execute(
                update(Attempt)
                .where(Attempt.id == first[sid], _claimable(now))
                .values(dispatch_state="claimed", claimed_by=me, lease_until=lease,
                        dispatch_tries=func.coalesce(Attempt.dispatch_tries, 0) + 1))
            s.commit()
            if res.rowcount == 1:
                with _lock:
                    _served[sid] = time.monotonic()
                return first[sid]
    return None


def _owns(aid: int, me: str) -> bool:
    """Fresh read (own connection): does `me` still hold the claim?"""
    with Session(engine) as s:
        a = s.get(Attempt, aid)
        return a is not None and a.dispatch_state == "claimed" and a.claimed_by == me


def _settle(session: Session, aid: int, me: str, **values) -> bool:
    """Owner-guarded state change in the caller's transaction. False when
    the claim was lost (the caller then rolls back)."""
    res = session.execute(
        update(Attempt).where(Attempt.id == aid, Attempt.claimed_by == me,
                              Attempt.dispatch_state == "claimed").values(**values))
    return res.rowcount == 1


def _run_claimed(aid: int, me: str) -> None:
    with Session(engine) as session:
        a = session.get(Attempt, aid)
        sub = session.get(Submission, a.submission_id) if a else None
        if a is None:
            return
        if sub is None or sub.deleted_at is not None:
            _settle(session, aid, me, dispatch_state="done")
            session.commit()
            return
        if session.exec(select(Run.id).where(Run.attempt_id == aid)).first():
            # Written already (by an earlier lease or the legacy loop).
            _settle(session, aid, me, dispatch_state="done")
            session.commit()
            _maybe_finalize(sub.id)
            return
        ctx = dispatch._remote_ctx_for(session, sub)
        if ctx is None:
            _settle(session, aid, me, dispatch_state="failed")
            a.dispatch_warning = "harness is not a remote runner; dispatch it again"
            session.add(a)
            session.commit()
            _maybe_finalize(sub.id)
            return
        ctx.owns = lambda: _owns(aid, me)
        problem = session.get(Problem, a.problem_id)
        try:
            runs, warn = dispatch._run_remote_attempt(session, sub, a, problem, ctx)
        except dispatch.LeaseLost:
            session.rollback()
            _log(f"attempt {aid}: lease lost, results dropped")
            return
        except Exception as e:  # noqa: BLE001 — same as the legacy loop
            session.rollback()
            runs, warn = [], f"Dispatch error: {e}"
        statuses = {r.get("status_msg") for r in runs}
        if warn is None and statuses and statuses <= replicas._SYSTEM:
            # Only transport/busy statuses, no verdict: nothing was written
            # (the persist callback skips them). Retry, as the legacy LeetCode
            # branch does; the legacy remote branch would finalize the attempt
            # as "Dispatch error" instead.
            note = (runs[0].get("raw_response") or {}).get("note") or ""
            warn = f"{runs[0]['status_msg']}: {note}".rstrip(": ")
        a = session.get(Attempt, aid)

        if warn is not None:
            tries = a.dispatch_tries or 1
            max_tries = app_settings.get_int("dispatch.queue_max_tries", 5)
            if tries >= max_tries:
                ok = _settle(session, aid, me, dispatch_state="failed",
                             dispatch_warning=warn, claimed_by=None, lease_until=None)
                _log(f"attempt {aid}: failed after {tries} tries: {warn}")
            else:
                backoff = min(900.0, _BACKOFF_BASE_S * 2 ** (tries - 1))
                ok = _settle(session, aid, me, dispatch_state="queued",
                             claimed_by=None, lease_until=None,
                             not_before=datetime.utcnow() + timedelta(seconds=backoff))
                _log(f"attempt {aid}: try {tries}/{max_tries} failed, retry in "
                     f"{backoff:.0f}s: {warn}")
            session.commit() if ok else session.rollback()
            if ok and tries >= max_tries:
                _maybe_finalize(sub.id)
            return

        a.dispatch_warning = None
        dispatch._aggregate_attempt(a, runs)
        session.add(a)
        if not _settle(session, aid, me, dispatch_state="done",
                       claimed_by=None, lease_until=None):
            session.rollback()
            return
        session.commit()
        with _lock:
            tools, machines, configs = _prov.setdefault(sub.id, (set(), [], []))
            tools |= ctx.tools
            machines.extend(ctx.machines)
            configs.extend(ctx.configs)
        _maybe_finalize(sub.id)


def _maybe_finalize(sub_id: int) -> None:
    """Run the submission rollup once its last open attempt settled."""
    with _lock:
        if sub_id in _finalizing:
            return
        _finalizing.add(sub_id)
    try:
        with Session(engine) as s:
            if s.exec(select(Attempt.id).where(
                    Attempt.submission_id == sub_id,
                    Attempt.dispatch_state.in_(OPEN)).limit(1)).first():
                return
            sub = s.get(Submission, sub_id)
            if sub is None:
                return
            attempts = s.exec(select(Attempt).where(Attempt.submission_id == sub_id)).all()
            with _lock:
                tools, machines, configs = _prov.pop(sub_id, (set(), [], []))
            dispatch._finalize_submission(s, sub, attempts, tools, machines,
                                          configs, real=True)
            _log(f"sub {sub_id}: all attempts settled — judged")
    finally:
        with _lock:
            _finalizing.discard(sub_id)


def start() -> int:
    """Startup: requeue the claims of a previous process and restart the
    workers of every harness with open work. No-op when the queue is off."""
    if not enabled():
        return 0
    with Session(engine) as s:
        res = s.execute(
            update(Attempt)
            .where(Attempt.dispatch_state == "claimed",
                   or_(Attempt.claimed_by.is_(None),
                       ~Attempt.claimed_by.contains(f"-{BOOT}-")))
            .values(dispatch_state="queued", claimed_by=None, lease_until=None))
        s.commit()
        hids = s.exec(select(Submission.harness_id)
                      .join(Attempt, Attempt.submission_id == Submission.id)
                      .where(Attempt.dispatch_state.in_(OPEN)).distinct()).all()
    for hid in hids:
        if hid is not None:
            ensure_workers(hid)
    if res.rowcount:
        _log(f"requeued {res.rowcount} claim(s) of a previous process")
    return res.rowcount
