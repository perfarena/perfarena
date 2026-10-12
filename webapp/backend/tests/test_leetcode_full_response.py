"""Verbatim LeetCode /check/ payload per run.

The dispatch layer stashes whatever LeetCode returned on Run.leetcode_full_response
so the UI can show fields the enumerated RunErrorLog doesn't cover.
Surfaced on AttemptRunDetail ONLY on non-Accepted runs — Accepted runs
ship null so the API response doesn't carry a kilobyte of useless JSON
on the happy path."""
from sqlmodel import Session, select

from conftest import ADMIN, GOOD_CODE, TRACE, HARNESS
from app.db import engine
from app.models import Attempt, Run, Submission


def _submit_and_get_sid(client) -> int:
    body = {
        "harness_slug": HARNESS,
        "model_name": "lc-raw-test", "model_version": "v1",
        "language": "python3", "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def _stuff_full_response(sid: int, payload: dict, *, status_msg: str) -> int:
    """Bypass the fake dispatcher — directly write a Run row carrying the
    payload we want to read back through the API. Simulates a real LC
    /check/ response landing in the DB."""
    with Session(engine) as s:
        att = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        assert att is not None
        run = Run(
            attempt_id=att.id, run_index=99,
            status_msg=status_msg,
            leetcode_full_response=payload,
            raw_response={"status_msg": status_msg},
        )
        s.add(run); s.commit(); s.refresh(run)
        return run.id


def test_full_response_persists_to_db(client):
    """The additive column survives a round-trip through the API."""
    sid = _submit_and_get_sid(client)
    payload = {
        "status_msg": "Wrong Answer", "status_code": 11,
        "last_testcase": "[2,7,11,15]\n9",
        "expected_output": "[0,1]", "code_output": "[1,0]",
        # Field the enumerated RunErrorLog doesn't cover — the whole point
        # of recording the full response is to keep these too.
        "state": "SUCCESS", "task_finish_time": 1234567890,
        "pretty_lang": "Python3",
    }
    _stuff_full_response(sid, payload, status_msg="Wrong Answer")

    with Session(engine) as s:
        r = s.exec(select(Run).where(Run.run_index == 99)).first()
    assert r is not None
    assert r.leetcode_full_response == payload


def test_full_response_surfaces_on_non_accepted_run(client):
    sid = _submit_and_get_sid(client)
    payload = {"status_msg": "Compile Error", "compile_error": "syntax", "state": "SUCCESS"}
    _stuff_full_response(sid, payload, status_msg="Compile Error")

    rows = client.get(f"/api/submissions/{sid}/attempts").json()
    run = next(r for r in rows[0]["runs"] if r["run_index"] == 99)
    assert run["leetcode_full_response"] == payload
    # And the response includes the field that the structured error_log
    # would have dropped — proves it's the verbatim payload.
    assert run["leetcode_full_response"]["state"] == "SUCCESS"


def test_full_response_hidden_on_accepted_run(client):
    """Accepted runs ship null — no need to carry the diagnostic blob."""
    sid = _submit_and_get_sid(client)
    payload = {"status_msg": "Accepted", "state": "SUCCESS"}
    _stuff_full_response(sid, payload, status_msg="Accepted")

    rows = client.get(f"/api/submissions/{sid}/attempts").json()
    run = next(r for r in rows[0]["runs"] if r["run_index"] == 99)
    assert run["leetcode_full_response"] is None


def test_existing_runs_with_null_full_response_still_work(client):
    """Older Run rows (pre-feature) have leetcode_full_response = null.
    The endpoint must still serve them — proves the migration was truly
    additive and didn't break the read path."""
    sid = _submit_and_get_sid(client)
    # Insert a run without touching leetcode_full_response — DB default = null.
    with Session(engine) as s:
        att = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        s.add(Run(attempt_id=att.id, run_index=88,
                  status_msg="Runtime Error",
                  raw_response={"status_msg": "Runtime Error"}))
        s.commit()
    rows = client.get(f"/api/submissions/{sid}/attempts").json()
    run = next(r for r in rows[0]["runs"] if r["run_index"] == 88)
    assert run["status_msg"] == "Runtime Error"
    assert run["leetcode_full_response"] is None
