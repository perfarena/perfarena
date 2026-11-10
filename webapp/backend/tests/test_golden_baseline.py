"""Golden baseline as a reference submission (golden_baseline.py).

Three LLMs each win one metric, a fourth one is faster at everything but
was submitted after the freeze date, and one C++ problem has no correct
solution. The tests check the copied values, the frozen provenance, that
nothing can be dispatched again, and that the rankings list the golden
baseline without ranking it.
"""
from datetime import datetime

import pytest
from sqlmodel import Session, select

from conftest import ADMIN
from app import ranking_store
from app.db import engine
from app.models import (
    Attempt, Competition, CompetitionProblem, Harness, Problem, Run, Submission,
    SubmissionStatus, User,
)

COMP = "golden-test"
SLUGS = ["two-sum", "add-digits", "24-game"]
BEFORE = datetime(2026, 10, 9, 12)          # inside the campaign
AFTER = datetime(2026, 10, 12, 12)          # after the freeze
# model -> (latency factor, energy factor, memory MB, tokens, cost, created_at)
LLMS = {"lat-llm": (1.0, 3.0, 30.0, 100, 0.01, BEFORE),
        "en-llm": (2.0, 1.0, 20.0, 200, 0.02, BEFORE),
        "mem-llm": (3.0, 2.0, 10.0, 300, 0.03, BEFORE),
        "late-llm": (0.1, 0.1, 1.0, 400, 0.04, AFTER)}


@pytest.fixture(scope="module", autouse=True)
def _data():
    with Session(engine) as s:
        if s.exec(select(Competition).where(Competition.slug == COMP)).first():
            return
        harness = s.exec(select(Harness).where(Harness.slug == "leetcode")).one()
        comp = Competition(slug=COMP, name="Golden test", harness_id=harness.id,
                           compatible_harness_slugs=["leetcode"])
        s.add(comp); s.commit(); s.refresh(comp)
        pids = [s.exec(select(Problem).where(Problem.title_slug == sl)).one().id for sl in SLUGS]
        for pid in pids:
            s.add(CompetitionProblem(competition_id=comp.id, problem_id=pid))
        user = s.exec(select(User)).first()
        for model, (lat, en, mem, tok, cost, created) in LLMS.items():
            for lang in ("python3", "cpp"):
                sub = Submission(user_id=user.id, model_name=model, model_version="v1",
                                 language=lang, prompt_template="golden test prompt",
                                 model_params={}, status=SubmissionStatus.judged,
                                 competition_id=comp.id, harness_id=harness.id,
                                 created_at=created)
                s.add(sub); s.commit(); s.refresh(sub)
                for i, pid in enumerate(pids):
                    ok = not (lang == "cpp" and SLUGS[i] == "24-game")
                    s.add(Attempt(
                        submission_id=sub.id, problem_id=pid, code=f"{model} {lang} {i}",
                        inference_trace="t", round_index=0, n_runs=1,
                        final_status="Accepted" if ok else "Wrong Answer",
                        avg_runtime_ms=lat * (i + 1) if ok else None,
                        best_runtime_ms=lat * (i + 1) * 0.9 if ok else None,
                        avg_runtime_energy_j=en * (i + 1) if ok else None,
                        avg_memory_mb=mem if ok else None,
                        response_tokens=tok, cost_usd=cost, logged_at=created))
                s.commit()


def _rankings(client, **params):
    r = client.get("/api/efficiency-rankings", params={"competition": COMP, **params})
    assert r.status_code == 200, r.text
    return {e["key"]: e for e in r.json()["entrants"]}


def _golden(lang: str, slug: str) -> Attempt:
    with Session(engine) as s:
        comp = s.exec(select(Competition).where(Competition.slug == COMP)).one()
        sub = s.exec(select(Submission).where(Submission.competition_id == comp.id,
                                              Submission.model_name == "golden-baseline",
                                              Submission.language == lang,
                                              Submission.deleted_at.is_(None))).one()
        pid = s.exec(select(Problem).where(Problem.title_slug == slug)).one().id
        return s.exec(select(Attempt).where(Attempt.submission_id == sub.id,
                                            Attempt.problem_id == pid)).first()


def test_build_copies_each_metric_from_its_winner(client):
    before = _rankings(client)
    r = client.post("/api/admin/golden-baseline",
                    json={"competition": COMP, "models": ["lat-llm", "en-llm", "mem-llm"]},
                    headers=ADMIN)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["pool"] == ["en-llm", "lat-llm", "mem-llm"]
    assert out["model_version"] == "frozen 2026-10-10"
    assert out["contributions"]["lat-llm"] == {"latency": 5}

    a = _golden("python3", "add-digits")
    assert (a.avg_runtime_ms, a.avg_runtime_energy_j, a.avg_memory_mb) == (2.0, 2.0, 10.0)
    assert a.best_runtime_ms == pytest.approx(1.8)
    # code, tokens, and cost come from the latency winner
    assert a.code == "lat-llm python3 1" and a.response_tokens == 100 and a.cost_usd == 0.01
    assert a.trace["golden_baseline"]["sources"]["memory"]["model"] == "mem-llm"

    # the reference row does not change the ranking of the real LLMs (the
    # stored rankings show it after a recompute)
    ranking_store.refresh_pass(force=True)
    after = _rankings(client)
    for k, e in before.items():
        for m in ("latency", "energy", "memory"):
            assert after[k]["metrics"][m]["win_prob"] == pytest.approx(e["metrics"][m]["win_prob"])
    golden = after["golden-baseline|golden-baseline|r1"]
    assert golden["reference"] and not golden["metrics"]["latency"]["rated"]
    assert golden["pareto_level"] is None and golden["model_version"] == "frozen 2026-10-10"


def test_frozen_rows_cannot_be_dispatched_again():
    a = _golden("cpp", "two-sum")
    assert a.logged_at.date().isoformat() == "2026-10-10"
    assert a.final_status == "Accepted" and a.n_runs == 1 and a.dispatch_state is None
    with Session(engine) as s:
        assert len(s.exec(select(Run).where(Run.attempt_id == a.id)).all()) == 1
        sub = s.get(Submission, a.submission_id)
        assert sub.status == SubmissionStatus.judged
        assert sub.total_solved == 2 and sub.pass_at_1 == pytest.approx(2 / 3)
    assert _golden("cpp", "24-game") is None         # nobody solved it


def test_late_submissions_stay_out_of_the_pool(client):
    r = client.post("/api/admin/golden-baseline",
                    json={"competition": COMP, "models": ["late-llm"]}, headers=ADMIN)
    assert r.status_code == 409                       # one golden baseline per competition
    assert _golden("python3", "two-sum").avg_runtime_ms == 1.0   # late-llm has 0.1


def test_head_to_head_against_the_golden_baseline(client):
    r = client.get("/api/head-to-head", params={"competition": COMP, "a": "mem-llm",
                                                "b": "golden-baseline"})
    assert r.status_code == 200, r.text
    m = r.json()["metrics"]
    assert m["memory"]["ratio"] == pytest.approx(1.0)
    assert m["latency"]["ratio"] == pytest.approx(3.0) and m["latency"]["b_wins"] == 1.0


def test_bump_charts_leave_the_golden_baseline_out(client):
    r = client.get("/api/efficiency-ranks", params={"competition": COMP})
    assert r.status_code == 200, r.text
    labels = {e["model"] for e in r.json()["ranks"]["latency"]["python3"]}
    assert not any("golden" in x for x in labels)


def test_unknown_model_is_rejected(client):
    with Session(engine) as s:
        comp = s.exec(select(Competition).where(Competition.slug == COMP)).one()
        for sub in s.exec(select(Submission).where(Submission.competition_id == comp.id,
                                                   Submission.model_name == "golden-baseline")):
            sub.deleted_at = datetime.utcnow()
            s.add(sub)
        s.commit()
    r = client.post("/api/admin/golden-baseline",
                    json={"competition": COMP, "models": ["late-llm"]}, headers=ADMIN)
    assert r.status_code == 400 and "late-llm" in r.text
    r = client.post("/api/admin/golden-baseline",
                    json={"competition": COMP, "languages": ["rust"]}, headers=ADMIN)
    assert r.status_code == 400 and "rust" in r.text


def test_languages_limit_the_golden_baseline(client):
    r = client.post("/api/admin/golden-baseline",
                    json={"competition": COMP, "models": ["lat-llm", "en-llm", "mem-llm"],
                          "languages": ["cpp"]}, headers=ADMIN)
    assert r.status_code == 201, r.text
    assert [x["language"] for x in r.json()["submissions"]] == ["cpp"]


def test_startup_backfill_does_not_register_the_golden_baseline():
    from app.main import _backfill_registered_model_stubs_from_submissions
    from app.models import RegisteredModel
    _backfill_registered_model_stubs_from_submissions()
    with Session(engine) as s:
        assert s.exec(select(RegisteredModel).where(
            RegisteredModel.name == "golden-baseline")).first() is None


def test_excluded_solutions_never_set_a_golden_value(client):
    with Session(engine) as s:
        comp = s.exec(select(Competition).where(Competition.slug == COMP)).one()
        for sub in s.exec(select(Submission).where(Submission.competition_id == comp.id,
                                                   Submission.model_name == "golden-baseline")):
            sub.deleted_at = datetime.utcnow()
            s.add(sub)
        s.commit()
    pool = ["lat-llm", "en-llm", "mem-llm"]
    r = client.post("/api/admin/golden-baseline", headers=ADMIN, json={
        "competition": COMP, "models": pool,
        "exclude": [{"model": "lat-llm", "language": "python3", "problem": "nonexistent"}]})
    assert r.status_code == 400 and "nonexistent" in r.text

    r = client.post("/api/admin/golden-baseline", headers=ADMIN, json={
        "competition": COMP, "models": pool,
        "exclude": [{"model": "lat-llm", "language": "python3", "problem": "add-digits"}]})
    assert r.status_code == 201, r.text
    # the latency of the excluded solution (2.0) gives way to the next best (en-llm, 4.0)
    a = _golden("python3", "add-digits")
    assert a.avg_runtime_ms == 4.0
    assert a.trace["golden_baseline"]["sources"]["latency"]["model"] == "en-llm"
    # the same LLM still sets the latency of its other cells
    assert _golden("cpp", "add-digits").avg_runtime_ms == 2.0
