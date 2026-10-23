"""POST /api/admin/attempts/timing: replace the latency and energy of accepted
attempts with new runs, keep their memory, and roll the submission up again.

One local-harness submission with two problems: "two-sum" gets new runs,
"add-digits" stays as it is.
"""
import pytest
from sqlmodel import Session, select

from conftest import ADMIN, USER
from app.db import engine
from app.models import Attempt, Harness, Problem, Run, Submission, SubmissionStatus, User

OLD_RT, OLD_EN, MEM = [1e-6, 1e-6, 1e-6], [1e-8, 1e-8, 1e-8], [3.75, 3.75, 3.875]


@pytest.fixture(scope="module")
def ids():
    with Session(engine) as s:
        h = Harness(slug="timing-test-local", name="Timing test", kind="custom")
        s.add(h); s.commit(); s.refresh(h)
        user = s.exec(select(User)).first()
        sub = Submission(user_id=user.id, model_name="timing-llm", model_version="v1",
                         language="cpp", prompt_template="timing test", model_params={},
                         status=SubmissionStatus.judged, harness_id=h.id)
        s.add(sub); s.commit(); s.refresh(sub)
        out = {"sub": sub.id}
        for slug, rt in (("two-sum", OLD_RT), ("add-digits", [0.5, 0.5, 0.5])):
            pid = s.exec(select(Problem).where(Problem.title_slug == slug)).one().id
            a = Attempt(submission_id=sub.id, problem_id=pid, code=slug, inference_trace="t",
                        round_index=0, n_runs=3, n_accepted_runs=3, final_status="Accepted",
                        avg_runtime_ms=sum(rt) / 3, best_runtime_ms=min(rt),
                        avg_runtime_energy_j=sum(OLD_EN) / 3, avg_memory_mb=sum(MEM) / 3)
            s.add(a); s.commit(); s.refresh(a)
            for i in range(3):
                s.add(Run(attempt_id=a.id, run_index=i + 1, status_msg="Accepted",
                          runtime_ms=rt[i], runtime_energy_j=OLD_EN[i], memory_mb=MEM[i],
                          harness_id=h.id,
                          raw_response={"time_us_per_op": rt[i] * 1e3, "peak_rss_kb": 3840,
                                        "memory_method": "peak-rss-helper-prebuilt",
                                        "per_case": [{"idx": 0, "time_us_per_op": rt[i] * 1e3,
                                                      "peak_rss_kb_case": 3712}]}))
            s.commit()
            out[slug] = a.id
        sub = s.get(Submission, sub.id)
        sub.avg_runtime_ms = (sum(OLD_RT) / 3 + 0.5) / 2
        s.add(sub); s.commit()
    return out


def _new_runs(rt):
    return [{"runtime_ms": rt, "runtime_energy_j": rt * 2e-2, "iterations": 1000,
             "time_us_per_op": rt * 1e3, "energy_j_per_op": rt * 2e-2,
             "raw_response": {"time_us_per_op": rt * 1e3, "peak_rss_kb": 9999,
                              "memory_method": "other", "measure_wall_s": 14.8,
                              "per_case": [{"idx": 0, "time_us_per_op": rt * 1e3,
                                            "peak_rss_kb_case": 9999}]}}
            for _ in range(3)]


def _item(ids, rt=0.025, **kw):
    return {"attempt_id": ids["two-sum"], "runs": _new_runs(rt), "method": "barrier",
            "title_slug": "two-sum", "language": "cpp", "submission_id": ids["sub"], **kw}


def test_needs_admin(client, ids):
    r = client.post("/api/admin/attempts/timing", json={"items": [_item(ids)]}, headers=USER)
    assert r.status_code == 403


def test_dry_run_writes_nothing(client, ids):
    r = client.post("/api/admin/attempts/timing",
                    json={"items": [_item(ids)], "dry_run": True}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1 and r.json()["dry_run"] is True
    with Session(engine) as s:
        assert s.get(Attempt, ids["two-sum"]).avg_runtime_ms == pytest.approx(1e-6)


def test_mismatch_and_run_count_are_skipped(client, ids):
    items = [_item(ids, title_slug="add-digits"), _item(ids, language="java"),
             _item(ids, submission_id=ids["sub"] + 999),
             {**_item(ids), "runs": _new_runs(0.025) * 2},
             {**_item(ids), "attempt_id": 10 ** 9}]
    r = client.post("/api/admin/attempts/timing", json={"items": items}, headers=ADMIN)
    out = r.json()
    assert out["updated"] == 0
    reasons = [s["reason"] for s in out["skipped"]]
    assert reasons[0].startswith("mismatch: title_slug")
    assert reasons[1].startswith("mismatch: language")
    assert reasons[2].startswith("mismatch: submission_id")
    assert reasons[3] == "6 new runs for 3 accepted runs"
    assert reasons[4] == "no such attempt"


def test_replaces_timing_and_keeps_memory(client, ids):
    r = client.post("/api/admin/attempts/timing",
                    json={"items": [_item(ids)], "keep_previous": True}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    with Session(engine) as s:
        a = s.get(Attempt, ids["two-sum"])
        assert a.avg_runtime_ms == pytest.approx(0.025)
        assert a.best_runtime_ms == pytest.approx(0.025)
        assert a.avg_runtime_energy_j == pytest.approx(0.025 * 2e-2)
        assert a.avg_iterations == pytest.approx(1000)
        assert a.avg_memory_mb == pytest.approx(sum(MEM) / 3)        # memory untouched
        assert a.final_status == "Accepted"
        runs = s.exec(select(Run).where(Run.attempt_id == a.id).order_by(Run.run_index)).all()
        assert [r.memory_mb for r in runs] == MEM
        raw = runs[0].raw_response
        assert raw["peak_rss_kb"] == 3840 and raw["memory_method"] == "peak-rss-helper-prebuilt"
        assert raw["per_case"] == [{"idx": 0, "time_us_per_op": 25.0, "peak_rss_kb_case": 3712}]
        assert raw["measure_wall_s"] == 14.8 and raw["timing_method"] == "barrier"
        assert raw["timing_previous"]["runtime_ms"] == pytest.approx(1e-6)
        sub = s.get(Submission, ids["sub"])
        assert sub.avg_runtime_ms == pytest.approx((0.025 + 0.5) / 2)
        assert sub.best_runtime_ms == pytest.approx((0.025 + 0.5) / 2)
        other = s.get(Attempt, ids["add-digits"])
        assert other.avg_runtime_ms == pytest.approx(0.5)


def test_extra_accepted_runs_lose_their_timing(client, ids):
    """add-digits: 2 new runs for 3 accepted runs (a duplicate dispatch); the
    third run keeps its memory but no latency, and the aggregates come from
    the 2 new runs only."""
    item = {"attempt_id": ids["add-digits"], "runs": _new_runs(0.25)[:2], "method": "barrier"}
    r = client.post("/api/admin/attempts/timing", json={"items": [item]}, headers=ADMIN)
    assert r.json()["updated"] == 1, r.text
    with Session(engine) as s:
        a = s.get(Attempt, ids["add-digits"])
        assert a.avg_runtime_ms == pytest.approx(0.25) and a.n_runs == 3
        runs = s.exec(select(Run).where(Run.attempt_id == a.id).order_by(Run.run_index)).all()
        assert [r.runtime_ms for r in runs] == [0.25, 0.25, None]
        assert [r.memory_mb for r in runs] == MEM
        assert runs[2].raw_response["per_case"] == [{"peak_rss_kb_case": 3712}]
        assert "time_us_per_op" not in runs[2].raw_response
        assert s.get(Submission, ids["sub"]).avg_runtime_ms == pytest.approx((0.025 + 0.25) / 2)
