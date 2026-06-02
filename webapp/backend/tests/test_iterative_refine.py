"""End-to-end coverage for the iterative-refinement / experiment-knob fields.

Validates the SERVER side of the iterative_refine.py workflow:

  * SubmissionIn accepts (n_rounds, include_hints, optimization_target)
    and stores them on the Submission row.
  * AttemptIn accepts (round_index, previous_attempt_id) and stores them
    on each Attempt row.
  * The chain link is validated: previous_attempt_id must belong to the
    same submission AND the same problem; otherwise 400.
  * optimization_target only accepts {"latency", "memory", null}.
  * SubmissionPublic + AttemptResult echo the fields back so the client can
    walk the chain on resume.
  * The unified `experiment-system` prompt is seeded on first boot.
"""
from conftest import ADMIN, GOOD_CODE, TRACE


def _sub_body(**overrides):
    body = {
        "model_name": "refine-test-m", "model_version": "v1",
        "language": "python3", "prompt_template": "stand-in text 1234567890",
        "model_params": {"temperature": 0.0},
        "n_samples_per_problem": 1,
        "attempts": [],
    }
    body.update(overrides)
    return body


def test_experiment_prompts_are_seeded(client):
    """app.main._seed_default_prompts must register BOTH the unified template
    and the with-shape variant."""
    names = {p["name"] for p in client.get("/api/prompts").json()}
    assert "experiment-system" in names
    assert "experiment-system-with-shape" in names


def test_problem_detail_exposes_code_snippets(client):
    """Problem.code_snippets round-trips through /api/problems/{slug} as a
    dict[str, str], even when empty (pre-fetch state)."""
    # Empty by default — fetch script hasn't run.
    d = client.get("/api/problems/two-sum").json()
    assert "code_snippets" in d
    assert isinstance(d["code_snippets"], dict)

    # Backfill on the DB row and confirm the API echoes it back.
    from sqlmodel import Session
    from app.db import engine
    from app.models import Problem
    snippets = {
        "python3": "class Solution:\n    def twoSum(self, nums, target):\n        pass\n",
        "cpp": "class Solution {\npublic:\n    vector<int> twoSum(…){}\n};",
    }
    with Session(engine) as s:
        p = s.exec(select_problem("two-sum")).first()
        p.code_snippets = snippets
        s.add(p); s.commit()
    d = client.get("/api/problems/two-sum").json()
    assert d["code_snippets"] == snippets


def select_problem(slug):
    """Tiny helper — keeps the test free of sqlmodel import noise."""
    from sqlmodel import select
    from app.models import Problem
    return select(Problem).where(Problem.title_slug == slug)


def test_submission_with_experiment_knobs_persisted_and_echoed(client):
    """Posting a submission with n_rounds + include_hints + optimization_target
    persists each field and the public detail response echoes them back."""
    body = _sub_body(
        n_rounds=3, include_hints=True, optimization_target="latency",
        attempts=[{
            "title_slug": "two-sum", "code": GOOD_CODE,
            "inference_trace": TRACE, "round_index": 0,
        }],
    )
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]

    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    assert pub["n_rounds"] == 3
    assert pub["include_hints"] is True
    assert pub["optimization_target"] == "latency"

    # Attempt result echoes the chain fields back too.
    atts = client.get(f"/api/submissions/{sid}/attempts").json()
    assert len(atts) == 1
    assert atts[0]["round_index"] == 0
    assert atts[0]["previous_attempt_id"] is None


def test_invalid_optimization_target_rejected(client):
    """The Pydantic regex on the field must reject unknown values."""
    body = _sub_body(optimization_target="nonsense",
                     attempts=[{"title_slug": "two-sum", "code": GOOD_CODE,
                                "inference_trace": TRACE}])
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 422   # FastAPI / Pydantic validation error


def test_chain_link_validation_rejects_cross_submission(client):
    """previous_attempt_id must belong to the SAME submission as the new
    attempt. A client trying to spoof a chain across submissions gets 400."""
    # Submission A — gives us a "round 0" attempt the chain could point at.
    body_a = _sub_body(
        n_rounds=2,
        attempts=[{"title_slug": "two-sum", "code": GOOD_CODE,
                   "inference_trace": TRACE, "round_index": 0}],
    )
    a = client.post("/api/submissions", headers=ADMIN, json=body_a)
    assert a.status_code == 202
    sub_a_id = a.json()["submission_id"]
    atts_a = client.get(f"/api/submissions/{sub_a_id}/attempts").json()
    round0_attempt_id = atts_a[0]["attempt_id"]

    # Submission B — try to chain its round 1 to A's round 0. Should 400.
    body_b = _sub_body(
        n_rounds=2,
        attempts=[{
            "title_slug": "two-sum", "code": GOOD_CODE,
            "inference_trace": TRACE, "round_index": 1,
            "previous_attempt_id": round0_attempt_id,
        }],
    )
    b = client.post("/api/submissions", headers=ADMIN, json=body_b)
    assert b.status_code == 400
    assert "different submission" in b.json()["detail"]


def test_chain_link_validation_rejects_different_problem(client):
    """Within the same submission, previous_attempt_id has to point at an
    attempt on the SAME problem — otherwise the chain is meaningless."""
    # Submission with a round-0 attempt on two-sum.
    body = _sub_body(
        n_rounds=2,
        attempts=[{"title_slug": "two-sum", "code": GOOD_CODE,
                   "inference_trace": TRACE, "round_index": 0}],
    )
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]
    round0_id = client.get(f"/api/submissions/{sid}/attempts").json()[0]["attempt_id"]

    # Append a round-1 attempt for a DIFFERENT problem but link it to round 0 of two-sum.
    append = client.post(
        f"/api/submissions/{sid}/attempts",
        headers=ADMIN,
        json={"attempts": [{
            "title_slug": "add-digits",        # different slug
            "code": GOOD_CODE, "inference_trace": TRACE,
            "round_index": 1, "previous_attempt_id": round0_id,
        }]},
    )
    assert append.status_code == 400
    assert "different problem" in append.json()["detail"]


def test_chain_link_within_submission_accepted(client):
    """The happy path: submission + append, both attempts on the SAME problem,
    chain link validated end-to-end."""
    body = _sub_body(
        n_rounds=2,
        attempts=[{"title_slug": "two-sum", "code": GOOD_CODE,
                   "inference_trace": TRACE, "round_index": 0}],
    )
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]
    round0_id = client.get(f"/api/submissions/{sid}/attempts").json()[0]["attempt_id"]

    append = client.post(
        f"/api/submissions/{sid}/attempts",
        headers=ADMIN,
        json={"attempts": [{
            "title_slug": "two-sum",
            "code": GOOD_CODE + "  # round 1",
            "inference_trace": TRACE + " — refined for latency",
            "round_index": 1, "previous_attempt_id": round0_id,
        }]},
    )
    assert append.status_code == 202

    atts = client.get(f"/api/submissions/{sid}/attempts").json()
    by_round = {a["round_index"]: a for a in atts}
    assert by_round[0]["previous_attempt_id"] is None
    assert by_round[1]["previous_attempt_id"] == round0_id
