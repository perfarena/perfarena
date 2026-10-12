"""Per-problem comparison histogram support — verifies the data the
front-end histogram consumes flows end-to-end:

  * each problem row in CompareResponse.problems carries a `per_model` map
    keyed by model name
  * each per-model cell exposes `runtime_percentile`, `memory_percentile`,
    and (when reported) `generation_energy_j` so the histogram can draw
    its 2-or-3 overlapping bars.
"""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE, HARNESS
from app.db import engine
from app.models import Attempt, Submission, SubmissionStatus


def _submit(client, model, energy=None, slug="two-sum"):
    body = {
        "harness_slug": HARNESS,
        "model_name": model, "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": slug, "code": GOOD_CODE,
                      "inference_trace": TRACE,
                      **({"generation_energy_j": energy} if energy is not None else {})}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def _force_judged(sid, *, rt_pct, mem_pct):
    """Pin the attempt-level percentiles the fake judge would otherwise
    randomise so the histogram values are deterministic."""
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        sub.status = SubmissionStatus.judged
        s.add(sub); s.commit()
        atts = s.exec(select(Attempt).where(Attempt.submission_id == sid)).all()
        for a in atts:
            a.final_status = "Accepted"
            a.n_runs = 1
            a.n_accepted_runs = 1
            a.avg_runtime_percentile = rt_pct
            a.avg_memory_percentile = mem_pct
            s.add(a)
        s.commit()


def test_compare_per_problem_carries_energy_when_reported(client):
    sa = _submit(client, "histo-a", energy=42.0)
    sb = _submit(client, "histo-b", energy=84.0)
    _force_judged(sa, rt_pct=80.0, mem_pct=60.0)
    _force_judged(sb, rt_pct=40.0, mem_pct=70.0)

    r = client.get("/api/compare", params=[
        ("models", "histo-a"), ("models", "histo-b"),
        ("language", "python3"),
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    row = next(p for p in body["problems"] if p["problem_slug"] == "two-sum")
    assert "histo-a" in row["per_model"] and "histo-b" in row["per_model"]

    a = row["per_model"]["histo-a"]
    b = row["per_model"]["histo-b"]
    # Histogram axes 1 + 2: runtime / memory percentile.
    assert a["runtime_percentile"] == 80.0
    assert a["memory_percentile"] == 60.0
    assert b["runtime_percentile"] == 40.0
    assert b["memory_percentile"] == 70.0
    # Histogram axis 3 (conditional): generation energy.
    assert a["generation_energy_j"] == 42.0
    assert b["generation_energy_j"] == 84.0


def test_compare_per_problem_energy_absent_when_not_reported(client):
    """LeetCode-only submissions don't report energy — the field is null
    so the histogram knows to OMIT the third bar for that pair."""
    sa = _submit(client, "histo-no-energy-a")
    sb = _submit(client, "histo-no-energy-b")
    _force_judged(sa, rt_pct=50.0, mem_pct=50.0)
    _force_judged(sb, rt_pct=50.0, mem_pct=50.0)

    r = client.get("/api/compare", params=[
        ("models", "histo-no-energy-a"), ("models", "histo-no-energy-b"),
        ("language", "python3"),
    ]).json()
    row = next(p for p in r["problems"] if p["problem_slug"] == "two-sum")
    for name in ("histo-no-energy-a", "histo-no-energy-b"):
        # Present in the cell, just null — keeps the wire shape stable.
        assert "generation_energy_j" in row["per_model"][name]
        assert row["per_model"][name]["generation_energy_j"] is None
