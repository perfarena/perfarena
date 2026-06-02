"""Per-(problem, language) baseline guarantee.

The leaderboard's LeetCode-coefficient math needs a Baseline row for every
accepted (problem, language) pair. Real LeetCode distributions require auth +
an accepted anchor submission id; in their absence we must still seed a
synthetic placeholder so no pair is left "missing". These tests pin that
fallback (the test env has no LeetCode auth, so every pair lands synthetic)."""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE
from app.db import engine
from app.models import Baseline, Problem


def _problem_id(slug: str) -> int:
    with Session(engine) as s:
        p = s.exec(select(Problem).where(Problem.title_slug == slug)).first()
        assert p is not None, f"seed problem {slug!r} missing"
        return p.id


def _baseline(problem_id: int, language: str) -> Baseline | None:
    with Session(engine) as s:
        return s.exec(
            select(Baseline).where(
                Baseline.problem_id == problem_id, Baseline.language == language,
            )
        ).first()


def test_submission_seeds_synthetic_baseline_for_new_pair(client):
    """Submitting for a (problem, language) pair with no baseline yet must
    leave a synthetic placeholder behind — never "missing"."""
    lang = "haskell"   # unused elsewhere, so it starts with zero baselines
    pid = _problem_id("two-sum")
    assert _baseline(pid, lang) is None, "precondition: no baseline for this pair"

    body = {
        "model_name": "ensure-test", "model_version": "v1",
        "language": lang, "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    assert client.post("/api/submissions", headers=ADMIN, json=body).status_code == 202

    b = _baseline(pid, lang)
    assert b is not None, "expected a baseline to be created on submission"
    assert b.source == "synthetic"           # no LeetCode auth in test env
    assert b.runtime_ms_avg > 0 and b.memory_mb_avg > 0


def test_refresh_backfills_missing_pairs(client):
    """POST /api/admin/baselines/refresh backfills synthetic placeholders for
    accepted pairs that have none, so a single call clears the "missing" set."""
    lang = "ocaml"     # another unused language
    body = {
        "model_name": "backfill-test", "model_version": "v1",
        "language": lang, "prompt_template": "solve cleanly 0987654321",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "add-digits", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    assert client.post("/api/submissions", headers=ADMIN, json=body).status_code == 202

    # Refresh is async now: POST returns 202 immediately; we poll the
    # progress endpoint until the worker finishes (instantly in this tiny
    # test DB), then verify the synthetic placeholder landed.
    import time
    r = client.post("/api/admin/baselines/refresh", headers=ADMIN)
    assert r.status_code == 202
    deadline = time.time() + 30
    while time.time() < deadline:
        progress = client.get("/api/admin/baselines/refresh/progress",
                              headers=ADMIN).json()
        if progress["state"] != "running":
            break
        time.sleep(0.1)
    pid = _problem_id("add-digits")
    assert _baseline(pid, lang) is not None
