"""Admin review of suspicious solutions (GET/PUT/DELETE /api/admin/integrity/review).

One peer group on its own harness: a solution 100x faster than its peers, four
ordinary peers, a peer of ordinary speed that loads C through ctypes, a failed
attempt, and a reference (golden-baseline) copy that is even faster. The tests
check that the queue lists the outlier and the ctypes solution but not the
reference copy, that a verdict moves a solution between the statuses, that a
verdict survives on a solution no flag catches, and that verdicts travel
through export and import.
"""
import pytest
from sqlmodel import Session, select

from conftest import ADMIN, USER
from app.db import engine
from app.models import (
    Agent, Attempt, Competition, CompetitionProblem, Harness, Problem, SolutionReview,
    Submission, SubmissionStatus, User,
)

COMP = "review-test"
SLUG = "two-sum"
# model -> best runtime (ms); "fast" is the outlier.
RUNTIMES = {"fast": 0.1, "p1": 10.0, "p2": 11.0, "p3": 12.0, "p4": 13.0}
IDS: dict[str, int] = {}


@pytest.fixture(scope="module", autouse=True)
def _data():
    with Session(engine) as s:
        harness = Harness(slug="review-test-harness", name="Review test", kind="local")
        s.add(harness); s.commit(); s.refresh(harness)
        comp = Competition(slug=COMP, name="Review test", harness_id=harness.id,
                           compatible_harness_slugs=[harness.slug])
        s.add(comp); s.commit(); s.refresh(comp)
        pid = s.exec(select(Problem).where(Problem.title_slug == SLUG)).one().id
        s.add(CompetitionProblem(competition_id=comp.id, problem_id=pid))
        ref = Agent(slug="review-test-reference", name="Reference", kind="reference")
        s.add(ref); s.commit(); s.refresh(ref)
        user = s.exec(select(User)).first()

        def attempt(model, rt, status="Accepted", agent_id=None, code=None):
            sub = Submission(user_id=user.id, model_name=model, model_version="v1",
                             language="python3", prompt_template="review test prompt",
                             model_params={}, status=SubmissionStatus.judged,
                             competition_id=comp.id, harness_id=harness.id,
                             agent_id=agent_id)
            s.add(sub); s.commit(); s.refresh(sub)
            a = Attempt(submission_id=sub.id, problem_id=pid, code=code or f"code of {model}",
                        inference_trace="t", round_index=0, n_runs=1, final_status=status,
                        best_runtime_ms=rt, avg_runtime_ms=rt)
            s.add(a); s.commit(); s.refresh(a)
            return a.id
        for model, rt in RUNTIMES.items():
            IDS[model] = attempt(model, rt)
        IDS["native"] = attempt("native", 12.5, code="import ctypes\nlib = ctypes.CDLL('x.so')")
        IDS["failed"] = attempt("failed", None, status="Wrong Answer")
        IDS["reference"] = attempt("review-test-reference", 0.05, agent_id=ref.id)


def _queue(client, status="pending"):
    r = client.get("/api/admin/integrity/review",
                   params={"competition": COMP, "status": status}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def test_review_endpoints_are_admin_only(client):
    aid = IDS["fast"]
    for method, path in (("get", "/api/admin/integrity/review"),
                         ("get", f"/api/admin/integrity/review/{aid}"),
                         ("put", f"/api/admin/integrity/review/{aid}"),
                         ("delete", f"/api/admin/integrity/review/{aid}")):
        kw = {"json": {"verdict": "ok"}} if method == "put" else {}
        assert getattr(client, method)(path, **kw).status_code == 401
        assert getattr(client, method)(path, headers=USER, **kw).status_code == 403


def test_queue_lists_the_flagged_solutions_but_not_the_reference_copy(client):
    q = _queue(client)
    # Code flags first, then speed outliers.
    assert [r["attempt_id"] for r in q["items"]] == [IDS["native"], IDS["fast"]]
    native, fast = q["items"]
    assert native["code_flags"] == ["loads native code with ctypes"]
    assert native["flagged"] is True and native["speed_flag"] is False
    assert fast["competition"] == COMP and fast["speed_flag"] is True
    assert fast["code_flags"] == []
    assert fast["ratio"] == pytest.approx(120.0)        # median of 10, 11, 12, 12.5, 13
    assert fast["n_peers"] == 5                         # the reference copy is no peer
    assert fast["review"] is None
    assert q["counts"] == {"pending": 2, "invalid": 0, "ok": 0}


def test_unknown_competition_is_404(client):
    r = client.get("/api/admin/integrity/review", params={"competition": "nope"},
                   headers=ADMIN)
    assert r.status_code == 404


def test_detail_has_code_statement_and_a_typical_peer(client):
    r = client.get(f"/api/admin/integrity/review/{IDS['fast']}", headers=ADMIN)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["code"] == "code of fast"
    assert d["problem"]["slug"] == SLUG and d["problem"]["content"]
    assert d["speed_flag"] is True and d["ratio"] == pytest.approx(120.0)
    assert d["code_flags"] == []
    assert d["typical"]["model_name"] == "p3"          # 12 ms, the peer median
    assert d["typical"]["code"] == "code of p3"
    native = client.get(f"/api/admin/integrity/review/{IDS['native']}", headers=ADMIN).json()
    assert native["code_flags"] == ["loads native code with ctypes"]


def test_verdict_moves_a_solution_between_statuses(client):
    aid = IDS["fast"]
    r = client.put(f"/api/admin/integrity/review/{aid}", headers=ADMIN,
                   json={"verdict": "invalid", "note": "  closed form  "})
    assert r.status_code == 200, r.text
    assert r.json()["review"]["verdict"] == "invalid"
    assert [x["attempt_id"] for x in _queue(client)["items"]] == [IDS["native"]]
    inv = _queue(client, "invalid")
    assert [x["attempt_id"] for x in inv["items"]] == [aid]
    review = inv["items"][0]["review"]
    assert review["note"] == "closed form" and review["reviewed_by"] == "Admin"
    assert inv["counts"] == {"pending": 1, "invalid": 1, "ok": 0}
    # A second verdict replaces the first.
    client.put(f"/api/admin/integrity/review/{aid}", headers=ADMIN, json={"verdict": "ok"})
    assert [x["attempt_id"] for x in _queue(client, "ok")["items"]] == [aid]
    with Session(engine) as s:
        assert len(s.exec(select(SolutionReview).where(
            SolutionReview.attempt_id == aid)).all()) == 1
    # Clearing it makes the solution pending again.
    assert client.delete(f"/api/admin/integrity/review/{aid}", headers=ADMIN).status_code == 204
    assert [x["attempt_id"] for x in _queue(client)["items"]] == [IDS["native"], aid]
    assert client.delete(f"/api/admin/integrity/review/{aid}", headers=ADMIN).status_code == 404


def test_verdict_needs_an_accepted_solution_and_a_known_verdict(client):
    r = client.put(f"/api/admin/integrity/review/{IDS['failed']}", headers=ADMIN,
                   json={"verdict": "invalid"})
    assert r.status_code == 400
    r = client.put(f"/api/admin/integrity/review/{IDS['p1']}", headers=ADMIN,
                   json={"verdict": "maybe"})
    assert r.status_code == 422


def test_a_verdict_on_an_unflagged_solution_stays_listed(client):
    aid = IDS["p1"]
    assert client.put(f"/api/admin/integrity/review/{aid}", headers=ADMIN,
                      json={"verdict": "ok"}).status_code == 200
    rows = _queue(client, "all")["items"]
    row = next(x for x in rows if x["attempt_id"] == aid)
    assert row["flagged"] is False and row["code_flags"] == []
    assert row["ratio"] == pytest.approx(1.2, abs=0.06)  # 12 ms median / 10 ms
    assert row["review"]["verdict"] == "ok"
    assert [x["attempt_id"] for x in rows[:2]] == [IDS["native"], IDS["fast"]]


def _sub_of(model):
    with Session(engine) as s:
        return s.get(Submission, s.get(Attempt, IDS[model]).submission_id)


def test_an_invalid_solution_leaves_the_rankings_but_counts_as_solved(client):
    from app.ranking_api import load

    def fast_entrant():
        with Session(engine) as s:
            d = load(s, COMP)
            e = next(e for e in d["entrants"].values() if e.model_name == "fast")
            return e, d["cells"]["latency"]
    e, cells = fast_entrant()
    assert e.n_solved == 1 and e.key in next(iter(cells.values()))
    client.put(f"/api/admin/integrity/review/{IDS['fast']}", headers=ADMIN,
               json={"verdict": "invalid"})
    e, cells = fast_entrant()
    assert e.n_solved == 1                              # pass@1 unchanged
    listed = client.get(f"/api/submissions/{_sub_of('fast').id}/attempts").json()
    assert [x["review_verdict"] for x in listed] == ["invalid"]
    assert all(e.key not in by_key for by_key in cells.values())
    # The stored aggregates of its submission lose the performance values.
    assert _sub_of("fast").avg_runtime_ms is None
    client.delete(f"/api/admin/integrity/review/{IDS['fast']}", headers=ADMIN)
    assert _sub_of("fast").avg_runtime_ms == pytest.approx(0.1)
    e, cells = fast_entrant()
    assert e.key in next(iter(cells.values()))


def test_an_invalid_solution_sets_no_golden_value(client):
    client.put(f"/api/admin/integrity/review/{IDS['fast']}", headers=ADMIN,
               json={"verdict": "invalid"})
    r = client.post("/api/admin/golden-baseline", headers=ADMIN,
                    json={"competition": COMP, "frozen_on": "2099-01-01"})
    assert r.status_code == 201, r.text
    with Session(engine) as s:
        sid = r.json()["submissions"][0]["submission_id"]
        golden = s.exec(select(Attempt).where(Attempt.submission_id == sid)).one()
        assert golden.avg_runtime_ms == pytest.approx(10.0)    # p1, not the invalid 0.1
    # The golden copy is a reference row: it never enters the review queue.
    assert golden.id not in {x["attempt_id"] for x in _queue(client, "all")["items"]}
    client.delete(f"/api/admin/integrity/review/{IDS['fast']}", headers=ADMIN)


def test_verdicts_travel_through_export_and_import(client):
    client.put(f"/api/admin/integrity/review/{IDS['fast']}", headers=ADMIN,
               json={"verdict": "invalid", "note": "n"})
    exp = client.get("/api/admin/export", headers=ADMIN, params={
        "tables": "users,problems,prompt_templates,submissions,attempts,solution_reviews"}).json()
    n = exp["counts"]["solution_reviews"]
    assert n >= 2
    r = client.post("/api/admin/import", headers=ADMIN, json=exp)
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["solution_reviews"]["inserted"] == n
    with Session(engine) as s:
        copies = [x for x in s.exec(select(SolutionReview)).all()
                  if s.get(Attempt, x.attempt_id).code == "code of fast"]
        assert len(copies) == 2 and {x.verdict for x in copies} == {"invalid"}
