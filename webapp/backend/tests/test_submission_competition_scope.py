"""Submission → competition scope.

Every submission targets exactly one competition (default
`leetcode-99-perf`). The denominator for total_problems / pass@k is the
competition's problem count — NOT every Problem row in the DB. Without
this scope, the rewritten-99 doubles the universe and reports 45/198 for
a canonical sweep that's actually 45/99 complete."""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE
from app.db import engine
from app.models import (
    Competition, CompetitionProblem, Problem, Submission,
)


def _make_competition(slugs: list[str], comp_slug: str = "scope-test") -> int:
    with Session(engine) as s:
        comp = Competition(slug=comp_slug, name="Scope Test",
                           kind="other", status="active")
        s.add(comp); s.commit(); s.refresh(comp)
        pid_by_slug = {p.title_slug: p.id for p in s.exec(
            select(Problem).where(Problem.title_slug.in_(slugs))).all()}
        for sl in slugs:
            s.add(CompetitionProblem(competition_id=comp.id,
                                     problem_id=pid_by_slug[sl]))
        s.commit()
        return comp.id


def test_total_problems_uses_competition_count(client):
    """A submission against a 3-problem competition reports total_problems=3,
    NOT 'len of every Problem row in the DB'."""
    comp_id = _make_competition(["two-sum", "add-digits", "valid-parentheses"],
                                comp_slug="tiny-comp")
    body = {
        "model_name": "scope-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "competition_slug": "tiny-comp",
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    sid = r.json()["submission_id"]

    with Session(engine) as s:
        sub = s.get(Submission, sid)
        assert sub.competition_id == comp_id
        assert sub.total_problems == 3   # the competition's size, not the DB's


def test_default_competition_is_leetcode_99_perf(client):
    """Omit competition_slug → default to leetcode-99-perf → total_problems
    is the canonical 99 (NOT the 99 + 99 rewritten = 198 the test DB now has
    if the variants competition is present)."""
    body = {
        "model_name": "default-comp-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 9876543210",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        comp = s.get(Competition, sub.competition_id)
        assert comp.slug == "leetcode-99-perf"
        # Canonical count, NOT len(all_problems).
        n_comp = len(s.exec(select(CompetitionProblem).where(
            CompetitionProblem.competition_id == comp.id)).all())
        assert sub.total_problems == n_comp


def test_unknown_competition_slug_is_400(client):
    body = {
        "model_name": "bad-comp", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1111111111",
        "model_params": {"temperature": 0.0},
        "competition_slug": "does-not-exist",
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 400


def test_attempt_outside_competition_is_rejected(client):
    """Submitting a slug that exists in the DB but ISN'T in the target
    competition is a 400 — otherwise a sweep could smuggle in problems
    outside scope and inflate its 'complete' count."""
    _make_competition(["two-sum"], comp_slug="single-problem-comp")
    body = {
        "model_name": "out-of-scope-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234512345",
        "model_params": {"temperature": 0.0},
        "competition_slug": "single-problem-comp",
        "attempts": [
            {"title_slug": "two-sum", "code": GOOD_CODE,
             "inference_trace": TRACE},
            # `add-digits` exists in the DB (canonical seed) but is NOT
            # in `single-problem-comp` — must be rejected.
            {"title_slug": "add-digits", "code": GOOD_CODE,
             "inference_trace": TRACE},
        ],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 400
    assert "single-problem-comp" in r.text


def test_append_respects_submission_competition_scope(client):
    """Appended attempts must also belong to the submission's competition.
    Otherwise the original scope check is bypassable via append."""
    _make_competition(["two-sum"], comp_slug="append-scope-test")
    create_body = {
        "model_name": "append-scope-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 8888888888",
        "model_params": {"temperature": 0.0},
        "competition_slug": "append-scope-test",
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=create_body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]
    # Try to append an attempt for a different (out-of-scope) problem.
    r = client.post(
        f"/api/submissions/{sid}/attempts", headers=ADMIN,
        json={"attempts": [{"title_slug": "add-digits", "code": GOOD_CODE,
                            "inference_trace": TRACE}]},
    )
    assert r.status_code == 400


def test_eligibility_universe_per_competition(client, monkeypatch):
    """The leaderboard's 'is this submission complete?' check uses the
    submission's OWN competition as the universe — independent of any
    URL-level competition filter the caller might pass."""
    monkeypatch.setenv("ARENA_LEADERBOARD_GATE", "on")
    from app import main as _main
    monkeypatch.setattr(_main, "LEADERBOARD_GATE_ON", True)

    # 2-problem competition; submit attempts for BOTH problems and
    # force-set pass_at_1 high so the rate gate passes.
    _make_competition(["two-sum", "add-digits"], comp_slug="two-prob-comp")
    body = {
        "model_name": "univ-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 2222222222",
        "model_params": {"temperature": 0.0},
        "competition_slug": "two-prob-comp",
        "attempts": [
            {"title_slug": "two-sum", "code": GOOD_CODE,
             "inference_trace": TRACE},
            {"title_slug": "add-digits", "code": GOOD_CODE,
             "inference_trace": TRACE},
        ],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        sub.total_solved = 2; sub.total_problems = 2; sub.pass_at_1 = 1.0
        s.add(sub); s.commit()

    # Global rankings: our 2-problem submission targeting `two-prob-comp`
    # is "complete" against ITS OWN competition, so it must appear.
    listed = {r["model_name"] for r in client.get("/api/rankings").json()}
    assert "univ-test" in listed
