"""GET /api/compare/prompts — prompt-strategy head-to-head within one
(model × version × language) cell. Same metric vocabulary as /api/compare,
axis swapped: prompts on rows, model held fixed."""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE, HARNESS
from app.db import engine
from app.models import PromptTemplate, Submission, SubmissionStatus


def _seed_prompts(*names):
    """Insert prompts and return their IDs in the same order. Idempotent on
    name collisions — re-uses existing rows."""
    ids = []
    with Session(engine) as s:
        for n in names:
            existing = s.exec(select(PromptTemplate).where(PromptTemplate.name == n)).first()
            if existing:
                ids.append(existing.id); continue
            pt = PromptTemplate(
                name=n, system_prompt="x " * 6, user_prompt_template="y " * 6,
                notes="test", status="active",
            )
            s.add(pt); s.commit(); s.refresh(pt)
            ids.append(pt.id)
    return ids


def _submit(client, model, ver, lang, prompt_id, slug="two-sum"):
    body = {
        "harness_slug": HARNESS,
        "model_name": model, "model_version": ver,
        "language": lang, "prompt_template": "solve cleanly 1234567890",
        "prompt_id": prompt_id,
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": slug, "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def _force_judged(sid, pass1, rt_pct, mem_pct):
    """Override the fake-judge's random output so the comparison math is
    deterministic. Sets the headline metrics; the row aggregator reads them."""
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        sub.status = SubmissionStatus.judged
        sub.pass_at_1 = pass1
        sub.pass_at_k = pass1
        sub.pass_at_full = pass1
        sub.avg_runtime_percentile = rt_pct
        sub.avg_memory_percentile = mem_pct
        s.add(sub); s.commit()


def test_compare_prompts_returns_one_row_per_requested_prompt(client):
    pa, pb = _seed_prompts("cp-prompt-a", "cp-prompt-b")
    _submit(client, "cp-model", "v1", "python3", pa)
    _submit(client, "cp-model", "v1", "python3", pb)

    r = client.get("/api/compare/prompts", params=[
        ("model_name", "cp-model"), ("model_version", "v1"),
        ("language", "python3"),
        ("prompt_ids", pa), ("prompt_ids", pb),
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {row["prompt_id"]: row for row in body["rows"]}
    assert pa in by_id and pb in by_id
    assert by_id[pa]["prompt_name"] == "cp-prompt-a"


def test_compare_prompts_requires_two_prompt_ids(client):
    pa, _ = _seed_prompts("cp-only-a", "cp-only-b")
    r = client.get("/api/compare/prompts", params=[
        ("model_name", "cp-model-2"), ("model_version", "v1"),
        ("language", "python3"), ("prompt_ids", pa),
    ])
    # FastAPI returns 422 on Query(..., min_length=2) violations.
    assert r.status_code == 422


def test_compare_prompts_unknown_prompt_id_400(client):
    pa, _ = _seed_prompts("cp-real", "cp-real-2")
    r = client.get("/api/compare/prompts", params=[
        ("model_name", "x"), ("model_version", "v1"), ("language", "python3"),
        ("prompt_ids", pa), ("prompt_ids", 999999),
    ])
    assert r.status_code == 400
    assert "prompt_id" in r.text.lower()


def test_compare_prompts_winners_pick_best_per_metric(client):
    """When prompts have measurably different pass@1 the winner banner picks
    the right prompt name. (Performance metrics in the row are aggregated
    from per-attempt fields the fake judge fills randomly, so we only assert
    the pass-related axis the test can force deterministically.)"""
    pa, pb = _seed_prompts("cp-winner-a", "cp-winner-b")
    sa = _submit(client, "cp-winners-m", "v1", "python3", pa)
    sb = _submit(client, "cp-winners-m", "v1", "python3", pb)
    _force_judged(sa, pass1=0.9, rt_pct=85.0, mem_pct=40.0)
    _force_judged(sb, pass1=0.5, rt_pct=30.0, mem_pct=80.0)

    r = client.get("/api/compare/prompts", params=[
        ("model_name", "cp-winners-m"), ("model_version", "v1"),
        ("language", "python3"),
        ("prompt_ids", pa), ("prompt_ids", pb),
    ]).json()
    w = r["winners"]
    assert w["best_pass_at_1"] == "cp-winner-a"
    # Structure: every metric key is present, overall_wins_by_prompt names
    # both prompts.
    for k in ("best_pass_at_k", "best_pass_at_full",
              "most_problems_solved", "fastest_avg_runtime",
              "leanest_avg_memory", "best_runtime_percentile",
              "best_memory_percentile", "overall"):
        assert k in w
    assert set(w["overall_wins_by_prompt"]) == {"cp-winner-a", "cp-winner-b"}


def test_compare_prompts_problems_filter_narrows_scope(client):
    """A `problems=` filter restricts aggregation to those slugs. Submissions
    that touch only other problems contribute zero attempted/accepted to the
    row (n_submissions still counts since the submission itself is in scope)."""
    pa, pb = _seed_prompts("cp-prob-a", "cp-prob-b")
    sa = _submit(client, "cp-probs-m", "v1", "python3", pa, slug="two-sum")
    _submit(client, "cp-probs-m", "v1", "python3", pb, slug="add-digits")
    _force_judged(sa, 0.5, 50.0, 50.0)

    r = client.get("/api/compare/prompts", params=[
        ("model_name", "cp-probs-m"), ("model_version", "v1"),
        ("language", "python3"),
        ("prompt_ids", pa), ("prompt_ids", pb),
        ("problems", "two-sum"),       # filter excludes add-digits
    ]).json()
    by_id = {row["prompt_id"]: row for row in r["rows"]}
    assert by_id[pa]["n_problems_attempted"] >= 1
    assert by_id[pb]["n_problems_attempted"] == 0
