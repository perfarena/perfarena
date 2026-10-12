"""Claim-based scheduler (Phase 3): parallel processing over the replica
pool, requeue with backoff, giving up, lease loss, startup recovery, a
replica dying mid-sweep, fairness, and the stale marker."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

from app import dispatch, scheduler
from app.db import engine
from app.models import Attempt, Run, Submission, SubmissionStatus
from replica_fakes import (  # noqa: F401 — fixtures
    add_replica, admit, make_class, make_oracles, make_submission,
    primary_replica, real_mode, runners,
)


@pytest.fixture
def queue(real_mode, monkeypatch):
    monkeypatch.setenv("ARENA_SCHEDULER_QUEUE", "1")
    monkeypatch.setattr(scheduler, "_IDLE_EXIT_S", 1.0)
    monkeypatch.setattr(scheduler, "_BACKOFF_BASE_S", 0.05)
    yield


def _wait(pred, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def _judged(sid: int) -> bool:
    with Session(engine) as s:
        return s.get(Submission, sid).status == SubmissionStatus.judged


def _attempts(sid: int) -> list[Attempt]:
    with Session(engine) as s:
        return s.exec(select(Attempt).where(Attempt.submission_id == sid)
                      .order_by(Attempt.id)).all()


def _runs(aid: int) -> list[Run]:
    with Session(engine) as s:
        return s.exec(select(Run).where(Run.attempt_id == aid)).all()


def _class_pair(runners, **kw):
    a, b = runners("a", **kw), runners("b", **kw)
    hid, _ = make_oracles(a, harness_policy={"policy": "least_busy"})
    rb = add_replica(b, harness_id=hid)
    cid = make_class()
    admit(primary_replica(harness_id=hid), a, cid)
    admit(rb, b, cid)
    return a, b, hid


def test_queue_is_off_by_default(real_mode, runners):
    a = runners("a")
    hid, _ = make_oracles(a)
    sid, (aid,) = make_submission(hid)
    dispatch.dispatch_submission(sid)
    (att,) = _attempts(sid)
    assert att.dispatch_state is None and len(_runs(aid)) == 2 and _judged(sid)


def test_queue_runs_attempts_in_parallel_and_judges(queue, runners):
    a, b, hid = _class_pair(runners, delay=0.5)
    sid, aids = make_submission(hid, n_attempts=4)
    t0 = time.monotonic()
    dispatch.dispatch_submission(sid)
    assert time.monotonic() - t0 < 0.5           # returned without measuring
    _wait(lambda: _judged(sid))
    elapsed = time.monotonic() - t0
    atts = _attempts(sid)
    assert {x.dispatch_state for x in atts} == {"done"}
    assert all(x.n_runs == 2 and x.final_status == "Accepted" for x in atts)
    assert (a.n("/measure"), b.n("/measure")) == (2, 2)
    assert elapsed < 1.9                         # 4 x 0.5 s in two lanes


def test_queue_requeues_a_system_failure(queue, runners):
    a = runners("a")
    hid, _ = make_oracles(a)
    sid, (aid,) = make_submission(hid)
    calls = []

    def flaky(path, body):
        calls.append(path)
        a.fail = len(calls) == 1                  # only the first call fails

    a.on_request = flaky
    dispatch.dispatch_submission(sid)
    _wait(lambda: _judged(sid))
    (att,) = _attempts(sid)
    assert att.dispatch_state == "done" and att.dispatch_tries == 2
    assert len(_runs(aid)) == 2 and att.dispatch_warning is None


def test_queue_gives_up_after_max_tries(queue, runners, monkeypatch):
    monkeypatch.setenv("ARENA_QUEUE_MAX_TRIES", "2")
    a = runners("a")
    a.fail = True
    hid, _ = make_oracles(a)
    sid, (aid,) = make_submission(hid)
    dispatch.dispatch_submission(sid)
    _wait(lambda: _judged(sid))
    (att,) = _attempts(sid)
    assert att.dispatch_state == "failed" and att.dispatch_tries == 2
    assert att.dispatch_warning and _runs(aid) == []


def test_lost_lease_drops_the_results(real_mode, runners, monkeypatch):
    monkeypatch.setenv("ARENA_SCHEDULER_QUEUE", "1")
    a = runners("a")
    hid, _ = make_oracles(a)
    sid, (aid,) = make_submission(hid)
    with Session(engine) as s:
        att = s.get(Attempt, aid)
        att.dispatch_state = "queued"
        s.add(att)
        s.commit()
    assert scheduler._claim(hid, "me") == aid

    def steal(path, body):                        # the lease moves meanwhile
        with Session(engine) as s:
            x = s.get(Attempt, aid)
            x.claimed_by = "thief"
            s.add(x)
            s.commit()

    a.on_request = steal
    scheduler._run_claimed(aid, "me")
    assert _runs(aid) == []
    (att,) = _attempts(sid)
    assert att.dispatch_state == "claimed" and att.claimed_by == "thief"


def test_attempt_with_runs_settles_without_dispatch(queue, runners):
    a = runners("a")
    hid, _ = make_oracles(a)
    sid, (aid,) = make_submission(hid)
    with Session(engine) as s:
        s.add(Run(attempt_id=aid, status_msg="Accepted", runtime_ms=1.0))
        att = s.get(Attempt, aid)
        att.dispatch_state = "queued"
        s.add(att)
        s.commit()
    scheduler.ensure_workers(hid)
    _wait(lambda: _attempts(sid)[0].dispatch_state == "done")
    assert a.n("/measure") == 0 and len(_runs(aid)) == 1


def test_startup_requeues_a_previous_process_claims(queue, runners):
    a = runners("a")
    hid, _ = make_oracles(a)
    sid, (aid,) = make_submission(hid)
    with Session(engine) as s:
        att = s.get(Attempt, aid)
        att.dispatch_state, att.claimed_by = "claimed", "queue-h1-deadbeef-0"
        att.lease_until = datetime.utcnow() + timedelta(hours=5)
        s.add(att)
        s.commit()
    assert scheduler.start() >= 1
    _wait(lambda: _attempts(sid)[0].dispatch_state == "done")
    assert len(_runs(aid)) == 2


def test_replica_dying_mid_sweep(queue, runners):
    a, b, hid = _class_pair(runners, delay=0.2)
    sid, aids = make_submission(hid, n_attempts=6)
    b.on_request = lambda path, body: setattr(b, "fail", b.n("/measure") > 1)
    dispatch.dispatch_submission(sid)
    _wait(lambda: _judged(sid), timeout=30)
    atts = _attempts(sid)
    assert {x.dispatch_state for x in atts} == {"done"}
    for x in atts:                                # no double-written runs
        idx = sorted(r.run_index for r in _runs(x.id))
        assert idx == [1, 2]
    assert a.n("/measure") >= 4


def test_fair_share_across_submissions(queue, runners):
    a = runners("a", delay=0.15)
    hid, _ = make_oracles(a)
    s1, a1 = make_submission(hid, n_attempts=3)
    s2, a2 = make_submission(hid, n_attempts=3)
    dispatch.dispatch_submission(s1)
    dispatch.dispatch_submission(s2)
    _wait(lambda: _judged(s1) and _judged(s2))
    order = [body["meta"]["attempt_id"] for _, body in a.calls]
    owner = ["s1" if x in a1 else "s2" for x in order]
    # After the first claim the two submissions alternate.
    assert owner[1:5] in (["s2", "s1", "s2", "s1"], ["s1", "s2", "s1", "s2"])


def test_stale_marker_leaves_queued_attempts_alone(queue, runners):
    from app.main import _mark_stale_pending_attempts
    a = runners("a")
    hid, _ = make_oracles(a)
    sid, (aid,) = make_submission(hid)
    with Session(engine) as s:
        att = s.get(Attempt, aid)
        att.dispatch_state = "queued"
        att.not_before = datetime.utcnow() + timedelta(hours=1)   # not claimable yet
        att.logged_at = datetime.utcnow() - timedelta(days=2)
        s.add(att)
        s.commit()
    _mark_stale_pending_attempts()
    assert _attempts(sid)[0].dispatch_warning is None
