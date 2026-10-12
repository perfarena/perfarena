"""Cost ($) + code-token accounting per attempt and rolled up per submission.

  * Each Attempt carries cost_usd (the $ of the one LLM call that produced
    it; 0 for Ollama) and response_tokens (code-only token count).
  * Submission.total_cost_usd / total_response_tokens are the sums across
    every attempt — so k samples × N problems all roll up.
  * Convention: a total stays None ("not reported") until at least one
    attempt reports the field — so a genuinely-free ($0) submission is
    distinguishable from a pre-feature one with no data.
"""
from conftest import ADMIN, GOOD_CODE, TRACE, HARNESS


def _submit(client, attempts, k=1):
    body = {
        "harness_slug": HARNESS,
        "model_name": "cost-test", "model_version": "v1",
        "language": "python3", "prompt_template": "stand-in 1234567890",
        "model_params": {"temperature": 0.0},
        "n_samples_per_problem": k,
        "attempts": attempts,
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def test_costs_and_tokens_roll_up(client):
    """Two problems, each with a cost + token figure → submission totals are
    the sums."""
    sid = _submit(client, [
        {"title_slug": "two-sum", "code": GOOD_CODE, "inference_trace": TRACE,
         "cost_usd": 0.0, "response_tokens": 120},
        {"title_slug": "add-digits", "code": GOOD_CODE, "inference_trace": TRACE,
         "cost_usd": 0.0, "response_tokens": 85},
    ])
    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    assert pub["total_cost_usd"] == 0.0
    assert pub["total_response_tokens"] == 205


def test_paid_costs_sum(client):
    """Non-zero costs (simulating a paid API) sum correctly with rounding."""
    sid = _submit(client, [
        {"title_slug": "two-sum", "code": GOOD_CODE, "inference_trace": TRACE,
         "cost_usd": 0.0012, "response_tokens": 100},
        {"title_slug": "add-digits", "code": GOOD_CODE, "inference_trace": TRACE,
         "cost_usd": 0.0008, "response_tokens": 50},
    ])
    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    assert abs(pub["total_cost_usd"] - 0.0020) < 1e-9
    assert pub["total_response_tokens"] == 150


def test_totals_none_when_not_reported(client):
    """Attempts that don't carry cost/token fields leave the totals None —
    distinguishing 'no data' from a real $0."""
    sid = _submit(client, [
        {"title_slug": "two-sum", "code": GOOD_CODE, "inference_trace": TRACE},
    ])
    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    assert pub["total_cost_usd"] is None
    assert pub["total_response_tokens"] is None


def test_append_updates_totals(client):
    """Appending more attempts re-sums the submission totals (k=2 budget so
    a second sample for the same problem is allowed)."""
    sid = _submit(client, [
        {"title_slug": "two-sum", "code": GOOD_CODE, "inference_trace": TRACE,
         "cost_usd": 0.001, "response_tokens": 100},
    ], k=2)
    # append a second sample
    r = client.post(f"/api/submissions/{sid}/attempts", headers=ADMIN, json={
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE + " # v2",
                      "inference_trace": TRACE + " variant 2",
                      "cost_usd": 0.002, "response_tokens": 140}],
    })
    assert r.status_code == 202
    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    assert abs(pub["total_cost_usd"] - 0.003) < 1e-9
    assert pub["total_response_tokens"] == 240


def test_per_attempt_cost_tokens_echoed(client):
    """The attempts endpoint exposes per-attempt cost_usd + response_tokens
    (the 'per LLM call' view)."""
    sid = _submit(client, [
        {"title_slug": "two-sum", "code": GOOD_CODE, "inference_trace": TRACE,
         "cost_usd": 0.0, "response_tokens": 99},
    ])
    atts = client.get(f"/api/submissions/{sid}/attempts").json()
    assert len(atts) == 1
    assert atts[0]["cost_usd"] == 0.0
    assert atts[0]["response_tokens"] == 99
