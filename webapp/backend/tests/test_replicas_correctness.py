"""Correctness replica pools (Phase 0): each validate policy, health and
back-pressure, unanimous / majority merges, and the single-endpoint path
staying exactly as before."""
from __future__ import annotations

import threading
import time

from sqlmodel import Session, select

from app import dispatch, replicas
from app.db import engine
from app.models import (
    Attempt, CorrectnessOracle, OracleReplica, Problem, ReplicaResult, Run,
    Submission,
)
from replica_fakes import (  # noqa: F401 — fixtures
    add_replica, make_oracles, make_submission, real_mode, runners,
)


def _validate(oid: int, sid: int, aid: int) -> dict:
    with Session(engine) as s:
        o, sub, a = s.get(CorrectnessOracle, oid), s.get(Submission, sid), s.get(Attempt, aid)
        res = replicas.validate(s, sub, s.get(Problem, a.problem_id), a.code, o,
                                attempt=a, meta={})
        s.commit()
        return res


def _pool3(runners, policy: dict | None, **kw):
    """Correctness oracle whose pool is a (primary), b, c."""
    a, b, c = (runners(n, **kw) for n in "abc")
    hid, oid = make_oracles(runners("perf"), correctness=a, oracle_policy=policy)
    add_replica(b, oracle_id=oid)
    add_replica(c, oracle_id=oid)
    sid, aids = make_submission(hid, oid)
    return (a, b, c), oid, sid, aids[0]


def test_single_endpoint_is_the_legacy_path(real_mode, runners):
    a = runners("a")
    hid, oid = make_oracles(runners("perf"), correctness=a)
    sid, (aid,) = make_submission(hid, oid)
    with Session(engine) as s:
        o = s.get(CorrectnessOracle, oid)
        assert replicas.policy_for(o, replicas.pool(s, o))[0] == "primary"
        assert not replicas.uses_pool(s, o)
    res = _validate(oid, sid, aid)
    assert res["status_msg"] == "Accepted" and "_replica_id" not in res
    assert a.n("/validate") == 1


def test_primary_replica_is_seeded_and_follows_config(real_mode, runners):
    a, b = runners("a"), runners("b")
    _, oid = make_oracles(runners("perf"), correctness=a)
    with Session(engine) as s:
        o = s.get(CorrectnessOracle, oid)
        row = replicas.ensure_primary(s, o)
        assert row.is_primary and row.endpoint == a.url and row.priority == 0
        o.config = {**o.config, "endpoint": b.url + "/"}
        s.add(o)
        s.commit()
        again = replicas.ensure_primary(s, o)
        assert again.id == row.id and again.endpoint == b.url
        assert len(replicas.pool(s, o)) == 1


def test_unset_policy_with_extra_replicas_defaults_to_least_busy(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, None)
    with Session(engine) as s:
        o = s.get(CorrectnessOracle, oid)
        assert replicas.policy_for(o, replicas.pool(s, o))[0] == "least_busy"


def test_round_robin_rotates(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "round_robin"})
    for _ in range(6):
        assert _validate(oid, sid, aid)["status_msg"] == "Accepted"
    assert [r.n("/validate") for r in (a, b, c)] == [2, 2, 2]


def test_weighted_follows_weights(real_mode, runners):
    a, b = runners("a"), runners("b")
    hid, oid = make_oracles(runners("perf"), correctness=a,
                            oracle_policy={"policy": "weighted"})
    with Session(engine) as s:
        p = s.get(OracleReplica, replicas.ensure_primary(
            s, s.get(CorrectnessOracle, oid)).id)
        p.weight = 3
        s.add(p)
        s.commit()
    add_replica(b, oracle_id=oid, weight=1)
    sid, (aid,) = make_submission(hid, oid)
    for _ in range(8):
        _validate(oid, sid, aid)
    assert (a.n("/validate"), b.n("/validate")) == (6, 2)


def test_least_busy_runs_in_parallel(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "least_busy"}, delay=0.6)
    out: list[dict] = []
    t0 = time.monotonic()
    ts = [threading.Thread(target=lambda: out.append(_validate(oid, sid, aid)))
          for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(out) == 3 and all(r["status_msg"] == "Accepted" for r in out)
    assert [r.n("/validate") for r in (a, b, c)] == [1, 1, 1]
    assert time.monotonic() - t0 < 1.5          # 3 x 0.6 s ran side by side
    assert max(r.peak for r in (a, b, c)) == 1  # never two jobs on one runner


def test_slots_queue_instead_of_429(real_mode, runners):
    """More callers than replicas: they wait for a slot, nobody gets a 429."""
    a = runners("a", delay=0.3)
    b = runners("b", delay=0.3)
    hid, oid = make_oracles(runners("perf"), correctness=a,
                            oracle_policy={"policy": "least_busy"})
    add_replica(b, oracle_id=oid)
    sid, (aid,) = make_submission(hid, oid)
    out: list[dict] = []
    ts = [threading.Thread(target=lambda: out.append(_validate(oid, sid, aid)))
          for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert [r["status_msg"] for r in out] == ["Accepted"] * 6
    assert a.n("/validate") + b.n("/validate") == 6


def test_sticky_keeps_a_submission_on_one_replica(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "sticky"})
    first = _validate(oid, sid, aid)["_replica_id"]
    for _ in range(4):
        assert _validate(oid, sid, aid)["_replica_id"] == first


def test_failover_uses_primary_until_it_fails(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "failover"})
    for _ in range(3):
        _validate(oid, sid, aid)
    assert (a.n("/validate"), b.n("/validate"), c.n("/validate")) == (3, 0, 0)
    a.fail = True
    res = _validate(oid, sid, aid)
    assert res["status_msg"] == "Accepted"
    assert b.n("/validate") == 1 and c.n("/validate") == 0


def test_busy_replica_hands_over(real_mode, runners):
    """A runner busy with someone else's job (429) cools briefly; the job
    moves to the next replica."""
    a, b = runners("a"), runners("b")
    hid, oid = make_oracles(runners("perf"), correctness=a,
                            oracle_policy={"policy": "least_busy"})
    add_replica(b, oracle_id=oid)
    sid, (aid,) = make_submission(hid, oid)
    a.active = 1                       # an outside job holds the runner
    try:
        res = _validate(oid, sid, aid)
    finally:
        a.active = 0
    assert res["status_msg"] == "Accepted" and b.n("/validate") == 1
    assert replicas._cool[a.url][1] == "busy"


def test_failover_waits_for_a_busy_primary(real_mode, runners, monkeypatch):
    """failover moves on only after a FAILURE; a busy primary is waited for."""
    monkeypatch.setenv("ARENA_REPLICA_WAIT_S", "1.5")
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "failover"})
    a.active = 1
    try:
        res = _validate(oid, sid, aid)
    finally:
        a.active = 0
    assert res["status_msg"] == "Rate limited" and b.n("/validate") == 0


def test_all_replicas_down_is_a_system_failure(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "round_robin"})
    for r in (a, b, c):
        r.fail = True
    res = _validate(oid, sid, aid)
    assert res["system_failure"] is True


def test_hedged_takes_the_first_answer(real_mode, runners):
    a = runners("a", delay=2.0)
    b = runners("b")
    hid, oid = make_oracles(runners("perf"), correctness=a,
                            oracle_policy={"policy": "hedged", "hedge_after_s": 0.2})
    add_replica(b, oracle_id=oid, priority=200)
    sid, (aid,) = make_submission(hid, oid)
    with replicas._CV:                 # make the slow primary the first pick
        replicas._ewma_s[b.url] = 9.0
    t0 = time.monotonic()
    res = _validate(oid, sid, aid)
    assert res["status_msg"] == "Accepted"
    assert time.monotonic() - t0 < 1.5
    assert a.n("/validate") == 1 and b.n("/validate") == 1


def test_broadcast_merge_unanimous_records_votes(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "broadcast_merge"})
    assert _validate(oid, sid, aid)["status_msg"] == "Accepted"
    assert [r.n("/validate") for r in (a, b, c)] == [1, 1, 1]
    with Session(engine) as s:
        votes = s.exec(select(ReplicaResult).where(ReplicaResult.attempt_id == aid)).all()
        assert sorted(v.status_msg for v in votes) == ["Accepted"] * 3
        assert {v.role for v in votes} == {"vote"}


def test_broadcast_merge_disagreement_is_inconsistent(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "broadcast_merge"})
    c.verdict = "Wrong Answer"
    res = _validate(oid, sid, aid)
    assert res["status_msg"] == replicas.INCONSISTENT
    assert res["system_failure"] is False
    assert sorted(v["status_msg"] for v in res["raw_response"]["votes"]) == [
        "Accepted", "Accepted", "Wrong Answer"]


def test_quorum_takes_the_majority(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "quorum", "quorum": 3})
    c.verdict = "Wrong Answer"
    assert _validate(oid, sid, aid)["status_msg"] == "Accepted"
    b.verdict = "Runtime Error"
    assert _validate(oid, sid, aid)["status_msg"] == replicas.INCONSISTENT


def test_broadcast_counts_the_home_verdict(real_mode, runners):
    (a, b, c), oid, sid, aid = _pool3(runners, {"policy": "broadcast"})
    c.verdict = "Wrong Answer"
    with replicas._CV:                 # a is the least busy (home)
        replicas._ewma_s[b.url] = replicas._ewma_s[c.url] = 9.0
    assert _validate(oid, sid, aid)["status_msg"] == "Accepted"
    assert [r.n("/validate") for r in (a, b, c)] == [1, 1, 1]


def test_policy_config_validation():
    ok = replicas.check_policy_config
    assert ok({"replicas": {"policy": "split_repeats"}}, performance=True) is None
    assert ok({"replicas": {"policy": "quorum"}}, performance=False) is None
    assert "not valid" in ok({"replicas": {"policy": "quorum"}}, performance=True)
    assert "not valid" in ok({"replicas": {"policy": "split_repeats"}}, performance=False)
    assert "positive" in ok({"replicas": {"policy": "hedged", "hedge_after_s": 0}},
                            performance=True)
    assert ok(None, performance=True) is None


# ---------- through dispatch_submission ------------------------------------------


def test_gate_inconsistent_verdict_skips_measure(real_mode, runners):
    perf = runners("perf")
    a, b = runners("a"), runners("b", verdict="Wrong Answer")
    hid, oid = make_oracles(perf, correctness=a,
                            oracle_policy={"policy": "broadcast_merge"})
    add_replica(b, oracle_id=oid)
    sid, (aid,) = make_submission(hid, oid)
    dispatch.dispatch_submission(sid)
    with Session(engine) as s:
        runs = s.exec(select(Run).where(Run.attempt_id == aid)).all()
        assert [r.status_msg for r in runs] == [replicas.INCONSISTENT]
        assert s.get(Attempt, aid).final_status == replicas.INCONSISTENT
    assert perf.n("/measure") == 0


def test_gate_system_failure_writes_no_run(real_mode, runners, monkeypatch):
    """A transient correctness failure leaves ZERO Run rows (the attempt stays
    pending), on the pooled path and on the single-endpoint path."""
    monkeypatch.setenv("ARENA_CORRECTNESS_RETRIES", "0")
    perf, a = runners("perf"), runners("a")
    hid, oid = make_oracles(perf, correctness=a)
    sid, (aid,) = make_submission(hid, oid)
    a.on_request = lambda path, body: setattr(a, "fail", True)
    dispatch.dispatch_submission(sid)
    with Session(engine) as s:
        assert s.exec(select(Run).where(Run.attempt_id == aid)).all() == []
        assert s.get(Attempt, aid).dispatch_warning
    assert perf.n("/measure") == 0


def test_gate_rejection_run_names_its_replica(real_mode, runners):
    perf = runners("perf")
    a = runners("a", verdict="Wrong Answer")
    hid, oid = make_oracles(perf, correctness=a,
                            oracle_policy={"policy": "least_busy"})
    sid, (aid,) = make_submission(hid, oid)
    dispatch.dispatch_submission(sid)
    with Session(engine) as s:
        (run,) = s.exec(select(Run).where(Run.attempt_id == aid)).all()
        prim = replicas.ensure_primary(s, s.get(CorrectnessOracle, oid))
        assert run.status_msg == "Wrong Answer" and run.replica_id == prim.id
