"""Harness errors: the correctness oracle accepts a solution, but the performance
harness fails to measure it (e.g. a library missing on the runner).

The attempt keeps the oracle's verdict, so pass@k counts it, records the
harness's failure in Attempt.harness_error, gives no performance value, enters
no ranking, and is listed for the admins. Without a correctness oracle, a failed
measurement is still the verdict on the solution.
"""
from __future__ import annotations

from sqlmodel import Session

from conftest import ADMIN, USER
from app import dispatch, integrity
from app.db import engine
from app.models import Attempt, Submission
from replica_fakes import (  # noqa: F401 — fixtures
    make_oracles, make_submission, real_mode, runners,
)

ERROR_LOG = "[case 0] ModuleNotFoundError: No module named 'sortedcontainers'"


def _broken(runner) -> None:
    """Make the runner's /measure fail as the perf harness does on a crash."""
    runner.run = lambda: {
        "status_msg": "Runtime Error", "runtime_ms": None, "memory_mb": None,
        "runtime_energy_j": None,
        "raw_response": {"note": "perf harness runtime error during measurement",
                         "error_log": ERROR_LOG}}


def test_accepted_solution_that_fails_measurement_is_a_harness_error(real_mode, runners, client):
    perf, oracle = runners("perf"), runners("oracle")
    _broken(perf)
    hid, oid = make_oracles(perf, correctness=oracle)
    sid, (aid,) = make_submission(hid, oid)
    dispatch.dispatch_submission(sid)
    with Session(engine) as s:
        a = s.get(Attempt, aid)
        assert a.final_status == "Accepted"
        assert a.harness_error == f"Runtime Error: {ERROR_LOG}"
        assert a.avg_runtime_ms is None and a.avg_runtime_energy_j is None
        assert aid in integrity.invalid_attempt_ids(s)
        sub = s.get(Submission, sid)
        assert sub.total_solved == 1 and sub.avg_runtime_ms is None
    assert perf.n("/measure") == 1

    listed = client.get("/api/admin/integrity/harness-errors", headers=ADMIN).json()
    row = next(r for r in listed["items"] if r["attempt_id"] == aid)
    assert row["harness_error"] == f"Runtime Error: {ERROR_LOG}"
    assert row["submission_id"] == sid
    assert client.get("/api/admin/integrity/harness-errors", headers=USER).status_code == 403
    shown = client.get(f"/api/submissions/{sid}/attempts", headers=ADMIN).json()
    assert [x["harness_error"] for x in shown] == [f"Runtime Error: {ERROR_LOG}"]


def test_without_a_correctness_oracle_a_failed_measurement_is_the_verdict(real_mode, runners):
    perf = runners("perf")
    _broken(perf)
    hid, _ = make_oracles(perf)
    sid, (aid,) = make_submission(hid)
    dispatch.dispatch_submission(sid)
    with Session(engine) as s:
        a = s.get(Attempt, aid)
        assert a.final_status == "Runtime Error" and a.harness_error is None
        assert aid not in integrity.invalid_attempt_ids(s)


def test_a_successful_re_measure_clears_the_harness_error():
    a = Attempt(submission_id=0, problem_id=0, code="", inference_trace="",
                harness_error="Runtime Error: old")
    dispatch._aggregate_attempt(a, [
        {"status_msg": "Accepted", "runtime_ms": 2.0, "memory_mb": 5.0,
         "oracle_accepted": True} for _ in range(2)])
    assert a.final_status == "Accepted" and a.harness_error is None
    assert a.avg_runtime_ms == 2.0
