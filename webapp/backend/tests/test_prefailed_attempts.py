"""Client-flagged malformed-response attempts (prefailed_reason).

When the client detects the model's response isn't a valid solution (wrong
language extracted, no code, etc.) it sends the attempt with `prefailed_reason`
set. The server must:
  * accept it even if validate_code would normally reject the code
  * persist the raw response in `trace`
  * synthesize a failure Run with status_msg=reason and skip LeetCode dispatch
  * surface `prefailed_reason` in the attempt result
"""
from sqlmodel import Session, select

from conftest import ADMIN, TRACE
from app.db import engine
from app.models import Attempt, Run


# Java code that would obviously not pass validate_code's Python ast parse
# AND would be misjudged by LeetCode if we let it through. Exactly the case
# the prefailed_reason channel exists for.
_WRONG_LANG_CODE = """\
class Solution {
    public int[] twoSum(int[] nums, int target) {
        // Java emitted while python3 was requested
        return new int[]{0, 1};
    }
}
"""


def _submit_prefailed(client):
    body = {
        "model_name": "prefail-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{
            "title_slug": "two-sum",
            "code": _WRONG_LANG_CODE,
            "inference_trace": ("model emitted Java when python3 was "
                                "requested; recorded as malformed"),
            "trace": {"raw_response": "raw model text including the Java code",
                      "provider": "ollama"},
            "prefailed_reason": "wrong_language_extracted",
        }],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def test_prefailed_attempt_bypasses_validate_code(client):
    """validate_code would reject the Java-as-python3 code as 'not Python';
    prefailed_reason must let the API accept it anyway so the data point
    isn't lost."""
    sid = _submit_prefailed(client)
    with Session(engine) as s:
        atts = s.exec(select(Attempt).where(Attempt.submission_id == sid)).all()
    assert len(atts) == 1
    assert atts[0].prefailed_reason == "wrong_language_extracted"


def test_prefailed_attempt_gets_synthetic_failure_run(client):
    """Dispatch creates one synthetic Run (no LeetCode call) with status_msg
    set to the prefailed_reason — so the attempt counts as failed in pass@k."""
    sid = _submit_prefailed(client)
    with Session(engine) as s:
        att = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        runs = s.exec(select(Run).where(Run.attempt_id == att.id)).all()
    assert len(runs) == 1
    assert runs[0].status_msg == "wrong_language_extracted"
    assert runs[0].runtime_ms is None
    assert runs[0].memory_mb is None
    # aggregate sees 1 run, 0 accepted → final_status = the reason.
    assert att.n_runs == 1 and att.n_accepted_runs == 0
    assert att.final_status == "wrong_language_extracted"
    assert att.avg_runtime_ms is None and att.avg_memory_mb is None


def test_prefailed_attempt_preserves_raw_response(client):
    """The whole point: the model's raw text stays on the attempt's trace so
    you can later inspect what the model produced when it failed."""
    sid = _submit_prefailed(client)
    with Session(engine) as s:
        att = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
    assert att.trace and att.trace.get("raw_response") == \
        "raw model text including the Java code"
    # Code (the malformed extraction) is also kept verbatim.
    assert "class Solution" in att.code


def test_prefailed_reason_surfaced_in_attempt_result(client):
    """Public endpoints expose prefailed_reason so the UI can label the row."""
    sid = _submit_prefailed(client)
    rows = client.get(f"/api/submissions/{sid}/attempts").json()
    assert rows[0]["prefailed_reason"] == "wrong_language_extracted"
    assert rows[0]["accepted"] is False
    assert rows[0]["status"] == "wrong_language_extracted"


def test_regular_attempt_without_prefailed_reason_still_validated(client):
    """Sanity: the validate_code gate still rejects empty code when the client
    has NOT marked the attempt as pre-failed (so the channel can't be used to
    smuggle stubs through accidentally)."""
    body = {
        "model_name": "validator-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": "",
                      "inference_trace": "x" * 30}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 400, r.text
