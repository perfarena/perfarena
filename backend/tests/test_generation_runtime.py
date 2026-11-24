"""Per-attempt generation wall-clock (wall_clock_ms) + per-submission rollup
(total_generation_ms). Mirrors the cost/token accounting tests."""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE, HARNESS
from app.db import engine
from app.models import Submission


def _submit_with_gen(client, gen_values):
    """Create a submission whose attempts carry the given wall_clock_ms."""
    slugs = ["two-sum", "add-digits"]
    attempts = [
        {"title_slug": slug, "code": GOOD_CODE, "inference_trace": TRACE,
         "wall_clock_ms": gen}
        for slug, gen in zip(slugs, gen_values)
    ]
    body = {
        "harness_slug": HARNESS,
        "model_name": "gen-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": attempts,
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def test_total_generation_ms_sums_attempts(client):
    sid = _submit_with_gen(client, [1200, 3400])
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        assert sub.total_generation_ms == 1200 + 3400


def test_total_generation_ms_none_when_unreported(client):
    """A submission whose attempts report no wall_clock_ms keeps total None
    (distinguishes 'not reported' from a genuine 0)."""
    body = {
        "harness_slug": HARNESS,
        "model_name": "gen-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 0192837465",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    sid = client.post("/api/submissions", headers=ADMIN, json=body).json()["submission_id"]
    with Session(engine) as s:
        assert s.get(Submission, sid).total_generation_ms is None


def test_attempt_result_exposes_wall_clock_ms(client):
    sid = _submit_with_gen(client, [2500, 800])
    rows = client.get(f"/api/submissions/{sid}/attempts").json()
    by_slug = {a["problem_slug"]: a for a in rows}
    assert by_slug["two-sum"]["wall_clock_ms"] == 2500
    assert by_slug["add-digits"]["wall_clock_ms"] == 800


def test_submission_public_exposes_total_generation_ms(client):
    sid = _submit_with_gen(client, [1000, 2000])
    d = client.get(f"/api/submissions/{sid}").json()
    assert d["submission"]["total_generation_ms"] == 3000
