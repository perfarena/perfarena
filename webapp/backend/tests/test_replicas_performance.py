"""Performance replica pools (Phases 1-2): the class-aware profile key, the
comparability rule under every policy, pooling within an admitted class,
broadcast shadows, calibration, and quarantine."""
from __future__ import annotations

import threading

from sqlmodel import Session, select

from app import dispatch, replicas
from app.db import engine
from app.measurement_profile import get_or_create_profile
from app.models import (
    Attempt, Harness, Machine, MeasurementProfile, OracleReplica, ReplicaResult,
    Run, Submission,
)
from replica_fakes import (  # noqa: F401 — fixtures
    add_replica, admit, make_class, make_oracles, make_submission,
    primary_replica, real_mode, runners, uid,
)


def _runs(sid: int) -> list[Run]:
    with Session(engine) as s:
        return s.exec(select(Run).join(Attempt, Attempt.id == Run.attempt_id)
                      .where(Attempt.submission_id == sid)).all()


def _sub(sid: int) -> Submission:
    with Session(engine) as s:
        return s.get(Submission, sid)


def _machine_of(runner) -> int:
    with Session(engine) as s:
        return s.exec(select(Machine).where(Machine.name == runner.machine)).first().id


def _pair(runners, policy, *, same_class=True, admitted=True, **kw):
    """Harness with primary a + replica b, optionally one admitted class."""
    a, b = runners("a", **kw), runners("b", **kw)
    hid, _ = make_oracles(a, harness_policy={"policy": policy})
    rb = add_replica(b, harness_id=hid)
    if same_class:
        cid = make_class()
        ra = primary_replica(harness_id=hid)
        for rid, r in ((ra, a), (rb, b)):
            admit(rid, r, cid)
            if not admitted:
                with Session(engine) as s:
                    row = s.get(OracleReplica, rid)
                    row.admitted_class_id = None
                    s.add(row)
                    s.commit()
        return a, b, hid, cid
    return a, b, hid, None


# ---------- Phase 1: profile key ------------------------------------------------


def test_classless_profile_key_is_per_machine():
    with Session(engine) as s:
        m1, m2 = Machine(name=f"p-{uid()}"), Machine(name=f"p-{uid()}")
        s.add(m1)
        s.add(m2)
        s.commit()
        p1 = get_or_create_profile(s, m1.id, "rapl", {"budget_s": 1})
        again = get_or_create_profile(s, m1.id, "rapl", {"budget_s": 1})
        p2 = get_or_create_profile(s, m2.id, "rapl", {"budget_s": 1})
        assert p1.id == again.id and p1.id != p2.id
        assert p1.machine_id == m1.id and p1.machine_class_id is None


def test_class_profile_key_pools_identical_machines():
    cid = make_class()
    with Session(engine) as s:
        m1 = Machine(name=f"p-{uid()}", machine_class_id=cid)
        m2 = Machine(name=f"p-{uid()}", machine_class_id=cid)
        s.add(m1)
        s.add(m2)
        s.commit()
        p1 = get_or_create_profile(s, m1.id, "rapl", {"budget_s": 1})
        p2 = get_or_create_profile(s, m2.id, "rapl", {"budget_s": 1})
        p3 = get_or_create_profile(s, m2.id, "rapl", {"budget_s": 2})
        assert p1.id == p2.id != p3.id
        assert p1.machine_id is None and p1.machine_class_id == cid


# ---------- single endpoint ------------------------------------------------------


def test_single_endpoint_measure_is_legacy(real_mode, runners):
    a = runners("a")
    hid, _ = make_oracles(a)
    sid, aids = make_submission(hid, n_attempts=2)
    dispatch.dispatch_submission(sid)
    runs = _runs(sid)
    assert len(runs) == 4 and a.n("/measure") == 2
    assert {r.replica_id for r in runs} == {None} and {r.machine_id for r in runs} == {None}
    sub = _sub(sid)
    assert sub.machine_id == _machine_of(a) and sub.machine_class_id is None
    with Session(engine) as s:
        prof = s.get(MeasurementProfile, runs[0].measurement_profile_id)
        assert prof.machine_id == sub.machine_id and prof.machine_class_id is None
    assert all(body["title_slug"] for _, body in a.calls)


# ---------- comparability rule ---------------------------------------------------


def test_unclassed_round_robin_stays_on_one_machine(real_mode, runners):
    a, b, hid, _ = _pair(runners, "round_robin", same_class=False)
    sid, _ = make_submission(hid, n_attempts=4)
    dispatch.dispatch_submission(sid)
    runs = _runs(sid)
    assert len(runs) == 8 and len({r.machine_id for r in runs}) == 1
    assert {a.n("/measure"), b.n("/measure")} == {0, 4}
    assert _sub(sid).machine_id == runs[0].machine_id


def test_sticky_spreads_submissions_not_attempts(real_mode, runners):
    a, b, hid, _ = _pair(runners, "sticky", same_class=False)
    sid1, _ = make_submission(hid, n_attempts=3)
    sid2, _ = make_submission(hid, n_attempts=3)
    with replicas._CV:               # sub 1 starts on a, sub 2 on b
        replicas._ewma_s[b.url] = 9.0
    dispatch.dispatch_submission(sid1)
    with replicas._CV:
        replicas._ewma_s[b.url], replicas._ewma_s[a.url] = 0.0, 9.0
    dispatch.dispatch_submission(sid2)
    m1 = {r.machine_id for r in _runs(sid1)}
    m2 = {r.machine_id for r in _runs(sid2)}
    assert len(m1) == len(m2) == 1 and m1 != m2


def test_admitted_class_round_robin_pools(real_mode, runners):
    a, b, hid, cid = _pair(runners, "round_robin")
    sid, _ = make_submission(hid, n_attempts=4)
    dispatch.dispatch_submission(sid)
    runs = _runs(sid)
    assert (a.n("/measure"), b.n("/measure")) == (2, 2)
    assert {r.machine_id for r in runs} == {_machine_of(a), _machine_of(b)}
    sub = _sub(sid)
    assert sub.machine_class_id == cid and sub.machine_id is None
    profiles = {r.measurement_profile_id for r in runs}
    assert len(profiles) == 1
    with Session(engine) as s:
        prof = s.get(MeasurementProfile, profiles.pop())
        assert prof.machine_class_id == cid and prof.machine_id is None


def test_declared_but_not_admitted_does_not_pool(real_mode, runners):
    a, b, hid, cid = _pair(runners, "round_robin", admitted=False)
    sid, _ = make_submission(hid, n_attempts=4)
    dispatch.dispatch_submission(sid)
    assert len({r.machine_id for r in _runs(sid)}) == 1


def test_quarantined_replica_takes_no_work(real_mode, runners):
    a, b, hid, cid = _pair(runners, "round_robin")
    with Session(engine) as s:
        rb = s.exec(select(OracleReplica).where(
            OracleReplica.harness_id == hid, OracleReplica.is_primary == False)).first()  # noqa: E712
        rb.status = "quarantined"
        s.add(rb)
        s.commit()
    sid, _ = make_submission(hid, n_attempts=3)
    dispatch.dispatch_submission(sid)
    assert b.n("/measure") == 0 and a.n("/measure") == 3


def test_legacy_stamped_machine_is_respected(real_mode, runners):
    """A submission measured before pools (sub.machine_id set, runs without
    machine_id) only continues on that machine."""
    a, b, hid, _ = _pair(runners, "round_robin", same_class=False)
    sid, _ = make_submission(hid, n_attempts=3)
    with Session(engine) as s:
        m = Machine(name=b.machine, hostname=b.machine)
        s.add(m)
        s.commit()
        sub = s.get(Submission, sid)
        sub.machine_id = m.id
        s.add(sub)
        s.commit()
        rb = s.exec(select(OracleReplica).where(OracleReplica.endpoint == b.url)).first()
        rb.machine_id = m.id
        s.add(rb)
        s.commit()
    dispatch.dispatch_submission(sid)
    assert (a.n("/measure"), b.n("/measure")) == (0, 3)


def test_concurrent_attempts_never_split_machines(real_mode, runners):
    """Two dispatch threads on one fresh submission and two unclassed
    replicas: the runs still come from a single machine."""
    a, b, hid, _ = _pair(runners, "least_busy", same_class=False, delay=0.3)
    sid, aids = make_submission(hid, n_attempts=2)

    def one(aid):
        with Session(engine) as s:
            sub, att = s.get(Submission, sid), s.get(Attempt, aid)
            h = s.get(Harness, hid)
            ctx = dispatch._remote_ctx_for(s, sub)
            dispatch._run_remote_attempt(s, sub, att, s.get(
                __import__("app.models", fromlist=["Problem"]).Problem, att.problem_id), ctx)
            s.commit()

    ts = [threading.Thread(target=one, args=(aid,)) for aid in aids]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len({r.machine_id for r in _runs(sid)}) == 1


# ---------- fan-out ---------------------------------------------------------------


def test_split_repeats_pools_within_class(real_mode, runners):
    a, b, hid, cid = _pair(runners, "split_repeats")
    with Session(engine) as s:
        h = s.get(Harness, hid)
        h.config = {**h.config, "repeats": 4}
        s.add(h)
        s.commit()
    sid, (aid,) = make_submission(hid)
    dispatch.dispatch_submission(sid)
    runs = _runs(sid)
    assert sorted(r.run_index for r in runs) == [1, 2, 3, 4]
    assert [body["repeats"] for _, body in a.calls + b.calls] == [2, 2]
    assert len({r.replica_id for r in runs}) == 2
    with Session(engine) as s:
        assert s.get(Attempt, aid).n_runs == 4


def test_split_repeats_without_class_runs_solo(real_mode, runners):
    a, b, hid, _ = _pair(runners, "split_repeats", same_class=False)
    sid, _ = make_submission(hid)
    dispatch.dispatch_submission(sid)
    assert sorted([a.n("/measure"), b.n("/measure")]) == [0, 1]
    assert len(_runs(sid)) == 2


def test_broadcast_merge_pools_all_runs(real_mode, runners):
    a, b, hid, cid = _pair(runners, "broadcast_merge")
    sid, (aid,) = make_submission(hid)
    dispatch.dispatch_submission(sid)
    runs = _runs(sid)
    assert len(runs) == 4 and len({r.machine_id for r in runs}) == 2
    assert sorted(r.run_index for r in runs) == [1, 2, 3, 4]


def test_broadcast_keeps_other_machines_apart(real_mode, runners):
    a, b, hid, _ = _pair(runners, "broadcast", same_class=False, runtime_ms=10.0)
    b.runtime_ms = 30.0
    with replicas._CV:               # a is the home replica
        replicas._ewma_s[b.url] = 9.0
    sid, (aid,) = make_submission(hid)
    dispatch.dispatch_submission(sid)
    runs = _runs(sid)
    assert len(runs) == 2 and {r.runtime_ms for r in runs} == {10.0}
    with Session(engine) as s:
        shadows = s.exec(select(ReplicaResult).where(ReplicaResult.attempt_id == aid)).all()
        assert len(shadows) == 2 and {x.role for x in shadows} == {"shadow"}
        assert {x.runtime_ms for x in shadows} == {30.0}
        assert {x.machine_id for x in shadows} == {_machine_of(b)}
        prof = s.get(MeasurementProfile, shadows[0].measurement_profile_id)
        assert prof.machine_id == _machine_of(b)
    assert _sub(sid).machine_id == _machine_of(a)


def test_hedged_measure_takes_the_fast_replica(real_mode, runners):
    a, b, hid, cid = _pair(runners, "hedged")
    with Session(engine) as s:
        h = s.get(Harness, hid)
        h.config = {**h.config, "replicas": {"policy": "hedged", "hedge_after_s": 0.2}}
        s.add(h)
        s.commit()
    a.delay = 2.0
    with replicas._CV:
        replicas._ewma_s[b.url] = 9.0
    sid, _ = make_submission(hid)
    dispatch.dispatch_submission(sid)
    runs = _runs(sid)
    assert len(runs) == 2 and {r.machine_id for r in runs} == {_machine_of(b)}


# ---------- machine identity + calibration ------------------------------------------


def test_machine_change_quarantines_an_admitted_replica(real_mode, runners):
    a, b, hid, cid = _pair(runners, "round_robin")
    b.machine = f"swapped-{uid()}"
    sid, _ = make_submission(hid, n_attempts=4)
    dispatch.dispatch_submission(sid)
    with Session(engine) as s:
        rb = s.exec(select(OracleReplica).where(OracleReplica.endpoint == b.url)).first()
        assert rb.status == "quarantined" and rb.admitted_class_id is None
    # The swapped box's runs never joined the class pool.
    assert _machine_of(b) not in {r.machine_id for r in _runs(sid)}


def test_calibration_admits_within_band_and_quarantines_outliers(real_mode, runners):
    a = runners("a", runtime_ms=10.0, energy_j=1.0)
    b = runners("b", runtime_ms=10.3, energy_j=1.02)
    c = runners("c", runtime_ms=14.0, energy_j=1.4)
    hid, _ = make_oracles(a)
    rb, rc = add_replica(b, harness_id=hid), add_replica(c, harness_id=hid)
    ra = primary_replica(harness_id=hid)
    _, (ref,) = make_submission(hid)
    cid = make_class(attempt_id=ref, tolerance_pct=5.0)
    with Session(engine) as s:
        first = replicas.calibrate(s, ra, declare_class_id=cid)
        assert first.passed and first.became_baseline
        ok = replicas.calibrate(s, rb, declare_class_id=cid)
        bad = replicas.calibrate(s, rc, declare_class_id=cid)
        assert ok.passed and not ok.became_baseline
        assert not bad.passed and "runtime" in bad.note
        rows = {r.id: r for r in s.exec(select(OracleReplica).where(
            OracleReplica.harness_id == hid)).all()}
        assert rows[ra].admitted_class_id == rows[rb].admitted_class_id == cid
        assert rows[rc].status == "quarantined" and rows[rc].admitted_class_id is None
        m = s.get(Machine, rows[rb].machine_id)
        assert m.machine_class_id == cid
    assert all(body["meta"].get("calibration") for _, body in a.calls)


def test_recalibration_quarantines_drift(real_mode, runners, monkeypatch):
    a = runners("a", runtime_ms=10.0)
    hid, _ = make_oracles(a)
    ra = primary_replica(harness_id=hid)
    _, (ref,) = make_submission(hid)
    cid = make_class(attempt_id=ref)
    with Session(engine) as s:
        replicas.calibrate(s, ra, declare_class_id=cid)
    a.runtime_ms = 13.0                         # the box drifted
    monkeypatch.setenv("ARENA_RECALIBRATE_INTERVAL_H", "0.000001")
    with Session(engine) as s:
        assert replicas.recalibrate_due(s) >= 1
        row = s.get(OracleReplica, ra)
        assert row.status == "quarantined"
