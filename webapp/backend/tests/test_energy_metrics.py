"""Per-attempt generation energy (Joules) + per-submission rollup.

Mirrors the wall_clock / cost / tokens pattern. LeetCode never reports energy
so for LC-only data paths every attempt's value stays null and the front-end
omits the column / stat — these tests verify the SERVER-side wiring."""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE, HARNESS
from app.db import engine
from app.models import Submission


def _submit_with_energy(client, energies):
    slugs = ["two-sum", "add-digits"]
    attempts = [
        {"title_slug": slug, "code": GOOD_CODE, "inference_trace": TRACE,
         "generation_energy_j": e}
        for slug, e in zip(slugs, energies)
    ]
    body = {
        "harness_slug": HARNESS,
        "model_name": "energy-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": attempts,
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def test_total_generation_energy_sums_attempts(client):
    sid = _submit_with_energy(client, [42.5, 17.25])
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        assert sub.total_generation_energy_j == 59.75


def test_total_generation_energy_none_when_unreported(client):
    """Submissions without energy fields (the LC-only case) keep totals null
    so the UI can omit the column rather than show '0 J' misleadingly."""
    body = {
        "harness_slug": HARNESS,
        "model_name": "no-energy", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 0192837465",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    sid = client.post("/api/submissions", headers=ADMIN, json=body).json()["submission_id"]
    with Session(engine) as s:
        assert s.get(Submission, sid).total_generation_energy_j is None


def test_attempt_result_exposes_generation_energy_j(client):
    sid = _submit_with_energy(client, [12.0, 8.5])
    rows = client.get(f"/api/submissions/{sid}/attempts").json()
    by_slug = {a["problem_slug"]: a for a in rows}
    assert by_slug["two-sum"]["generation_energy_j"] == 12.0
    assert by_slug["add-digits"]["generation_energy_j"] == 8.5


def test_submission_public_exposes_total_generation_energy_j(client):
    sid = _submit_with_energy(client, [100.0, 50.0])
    d = client.get(f"/api/submissions/{sid}").json()
    assert d["submission"]["total_generation_energy_j"] == 150.0


def test_partial_energy_reports_still_sum(client):
    """If one attempt reports energy and another doesn't, the total is the
    sum of what was reported (missing treated as 0). Matches the cost / tokens
    convention."""
    sid = _submit_with_energy(client, [33.0, None])
    with Session(engine) as s:
        assert s.get(Submission, sid).total_generation_energy_j == 33.0
