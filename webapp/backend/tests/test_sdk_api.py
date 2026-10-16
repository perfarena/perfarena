"""Client-helper endpoints: GET /api/attempts/{id} (machine-readable state),
PUT /api/submissions/upsert (find-or-create by experiment cell), and
GET /api/pricing."""
from __future__ import annotations

import uuid

from sqlmodel import Session, select

from conftest import ADMIN, HARNESS, USER
from app.db import engine
from app.models import (
    Agent, Attempt, Problem, RegisteredModel, Run, Submission, User,
)


def _cell(**over) -> dict:
    body = {"model_name": f"upsert-{uuid.uuid4().hex[:6]}", "model_version": "v1",
            "language": "python3", "prompt_template": "solve it cleanly please",
            "model_params": {"temperature": 0.0}, "harness_slug": HARNESS}
    body.update(over)
    return body


def _attempt(**fields) -> int:
    with Session(engine) as s:
        admin = s.exec(select(User).where(User.email == "admin@test.dev")).one()
        sub = Submission(user_id=admin.id, model_name="state-m", model_version="v1",
                         language="python3", prompt_template="p", model_params={})
        s.add(sub)
        s.commit()
        pid = s.exec(select(Problem.id)).first()
        runs = fields.pop("runs", [])
        a = Attempt(submission_id=sub.id, problem_id=pid, code="x",
                    inference_trace="t", **fields)
        s.add(a)
        s.commit()
        for i, st in enumerate(runs, 1):
            s.add(Run(attempt_id=a.id, run_index=i, status_msg=st, runtime_ms=1.0))
        s.commit()
        return a.id


# ---------- GET /api/attempts/{id} --------------------------------------------


def test_attempt_states(client):
    cases = [
        ({}, "queued", None, False),
        ({"dispatch_state": "claimed"}, "running", None, False),
        ({"runs": ["Accepted"]}, "running", None, False),
        ({"runs": ["Accepted"], "final_status": "Accepted", "n_runs": 1},
         "done", "Accepted", True),
        ({"runs": ["Wrong Answer"], "final_status": "Wrong Answer"},
         "done", "Wrong Answer", True),
        ({"prefailed_reason": "wrong_language_extracted"},
         "done", "wrong_language_extracted", True),
        ({"dispatch_warning": "Runner unreachable"}, "failed", None, True),
        ({"dispatch_warning": "retrying", "dispatch_state": "queued"},
         "queued", None, False),
        ({"final_status": "Dispatch error"}, "failed", None, True),
    ]
    for fields, state, verdict, terminal in cases:
        aid = _attempt(**fields)
        r = client.get(f"/api/attempts/{aid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["state"], body["verdict"], body["terminal"]) == (
            state, verdict, terminal), fields
    assert client.get("/api/attempts/99999999").status_code == 404


def test_attempt_state_carries_runs_and_metrics(client):
    aid = _attempt(runs=["Accepted", "Accepted"], final_status="Accepted",
                   n_runs=2, n_accepted_runs=2, avg_runtime_ms=1.0)
    body = client.get(f"/api/attempts/{aid}").json()
    assert body["accepted"] is True and body["avg_runtime_ms"] == 1.0
    assert body["code"] == "x"
    assert [r["run_index"] for r in body["runs"]] == [1, 2]


# ---------- PUT /api/submissions/upsert -----------------------------------------


def test_upsert_creates_then_finds(client):
    body = _cell()
    r1 = client.put("/api/submissions/upsert", headers=ADMIN, json=body)
    assert r1.status_code == 200, r1.text
    assert r1.json()["created"] is True
    r2 = client.put("/api/submissions/upsert", headers=ADMIN, json=body)
    assert r2.json() == {**r1.json(), "created": False}
    # model_params are not part of the cell
    r3 = client.put("/api/submissions/upsert", headers=ADMIN,
                    json={**body, "model_params": {"temperature": 0.7}})
    assert r3.json()["submission_id"] == r1.json()["submission_id"]


def test_upsert_cell_fields_split_submissions(client):
    body = _cell()
    base_id = client.put("/api/submissions/upsert", headers=ADMIN,
                         json=body).json()["submission_id"]
    for over in ({"n_rounds": 3}, {"include_hints": True},
                 {"optimization_target": "latency"}, {"model_version": "v2"},
                 {"language": "cpp"}):
        r = client.put("/api/submissions/upsert", headers=ADMIN, json={**body, **over})
        assert r.json()["created"] is True, over
        assert r.json()["submission_id"] != base_id


def test_upsert_respects_agent_and_fully_accepted(client):
    body = _cell()
    sid = client.put("/api/submissions/upsert", headers=ADMIN,
                     json=body).json()["submission_id"]
    with Session(engine) as s:
        slug = f"agent-{uuid.uuid4().hex[:6]}"
        s.add(Agent(slug=slug, name="an agent"))
        sub = s.get(Submission, sid)
        sub.total_problems, sub.total_solved = 5, 5      # fully accepted
        s.add(sub)
        s.commit()
    other = client.put("/api/submissions/upsert", headers=ADMIN,
                       json={**body, "agent_slug": slug}).json()
    assert other["created"] is True
    fresh = client.put("/api/submissions/upsert", headers=ADMIN, json=body).json()
    assert fresh["created"] is True and fresh["submission_id"] != sid
    again = client.put("/api/submissions/upsert", headers=ADMIN,
                       json={**body, "include_fully_accepted": True}).json()
    assert again["created"] is False


def test_upsert_legacy_null_harness_row_matches(client):
    body = _cell()
    with Session(engine) as s:
        admin = s.exec(select(User).where(User.email == "admin@test.dev")).one()
        legacy = Submission(user_id=admin.id, model_name=body["model_name"],
                            model_version="v1", language="python3",
                            prompt_template="p", model_params={},
                            harness_id=None, competition_id=None)
        s.add(legacy)
        s.commit()
        lid = legacy.id
    r = client.put("/api/submissions/upsert", headers=ADMIN, json=body).json()
    assert r == {"submission_id": lid, "created": False, "status": "received"}


def test_upsert_validation_and_auth(client):
    assert client.put("/api/submissions/upsert", json=_cell()).status_code == 401
    assert client.put("/api/submissions/upsert", headers=USER,
                      json=_cell()).status_code == 403
    r = client.put("/api/submissions/upsert", headers=ADMIN,
                   json=_cell(harness_slug="no-such-harness"))
    assert r.status_code == 400


# ---------- GET /api/pricing ----------------------------------------------------


def test_pricing_lists_models_with_both_rates(client):
    name = f"priced-{uuid.uuid4().hex[:6]}"
    with Session(engine) as s:
        s.add(RegisteredModel(name=name, version="v1", provider="openai",
                              input_price_per_1m_usd=1.5, output_price_per_1m_usd=6.0))
        s.add(RegisteredModel(name=name, version="v2", provider="openai",
                              input_price_per_1m_usd=1.5))
        s.commit()
    rows = [r for r in client.get("/api/pricing").json() if r["model_name"] == name]
    assert rows == [{"model_name": name, "model_version": "v1",
                     "input_price_per_1m_usd": 1.5, "output_price_per_1m_usd": 6.0}]


# ---------- attempt ids on create / append ---------------------------------------


def test_create_and_append_return_attempt_ids(client):
    from conftest import GOOD_CODE, TRACE
    body = _cell(attempts=[{"title_slug": "two-sum", "code": GOOD_CODE,
                            "inference_trace": TRACE}],
                 n_samples_per_problem=2)
    created = client.post("/api/submissions", headers=ADMIN, json=body).json()
    assert len(created["attempt_ids"]) == 1
    appended = client.post(f"/api/submissions/{created['submission_id']}/attempts",
                           headers=ADMIN, json={"attempts": body["attempts"]}).json()
    assert len(appended["attempt_ids"]) == 1
    assert appended["attempt_ids"][0] > created["attempt_ids"][0]
    state = client.get(f"/api/attempts/{appended['attempt_ids'][0]}").json()
    assert state["problem_slug"] == "two-sum"


def test_upsert_lookup_only_creates_nothing(client):
    body = _cell(create=False)
    r = client.put("/api/submissions/upsert", headers=ADMIN, json=body).json()
    assert r == {"submission_id": None, "created": False, "status": None}
    made = client.put("/api/submissions/upsert", headers=ADMIN,
                      json={**body, "create": True}).json()
    found = client.put("/api/submissions/upsert", headers=ADMIN, json=body).json()
    assert found["submission_id"] == made["submission_id"] and not found["created"]
