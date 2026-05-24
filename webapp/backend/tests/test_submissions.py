"""Submission lifecycle: create, append, progress, attempts, detail, derived
model/submitter views."""
from conftest import (
    ADMIN, GOOD_CODE, STUB_CODE, TRACE, USER, make_submission,
)


def test_create_requires_auth(client):
    body = {"model_name": "m", "model_version": "v", "language": "python3",
            "prompt_template": "solve the problem", "model_params": {},
            "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                          "inference_trace": TRACE}]}
    assert client.post("/api/submissions", json=body).status_code == 401


def test_create_rejects_stub_and_unknown_slug(client):
    base = {"model_name": "m", "model_version": "v", "language": "python3",
            "prompt_template": "solve the problem", "model_params": {}}
    stub = client.post("/api/submissions", headers=ADMIN, json={
        **base, "attempts": [{"title_slug": "two-sum", "code": STUB_CODE,
                              "inference_trace": TRACE}]})
    assert stub.status_code == 400

    unknown = client.post("/api/submissions", headers=ADMIN, json={
        **base, "attempts": [{"title_slug": "ghost-problem", "code": GOOD_CODE,
                              "inference_trace": TRACE}]})
    assert unknown.status_code == 400


def test_full_lifecycle(client):
    sid = make_submission(client, slugs=("two-sum", "add-digits"))

    detail = client.get(f"/api/submissions/{sid}")
    assert detail.status_code == 200
    d = detail.json()
    assert d["submission"]["status"] == "judged"           # fake judge ran
    assert {"runs", "traces", "submission", "model_params"} <= d.keys()
    assert all("provenance" in t for t in d["traces"])
    assert client.get("/api/submissions/424242").status_code == 404

    prog = client.get(f"/api/submissions/{sid}/progress")
    assert prog.status_code == 200
    p = prog.json()
    assert p["attempted"] == 2
    assert p["accepted"] + len(p["rejected"]) + p["queued"] == 2
    assert client.get("/api/submissions/424242/progress").status_code == 404

    att = client.get(f"/api/submissions/{sid}/attempts")
    assert att.status_code == 200
    rows = att.json()
    assert len(rows) == 2
    for a in rows:
        assert {"attempt_id", "problem_slug", "status", "accepted",
                "n_runs", "n_accepted_runs", "runs"} <= a.keys()
        assert a["n_runs"] == len(a["runs"])
        assert a["accepted"] == (a["status"] == "Accepted")
    # worst-first ordering: an accepted attempt never precedes a non-accepted one
    seen_accepted = False
    for a in rows:
        if a["accepted"]:
            seen_accepted = True
        elif seen_accepted:
            raise AssertionError("accepted attempt ordered before a rejected one")
    assert client.get("/api/submissions/424242/attempts").status_code == 404


def test_append_attempts_rules(client):
    sid = make_submission(client, slugs=("two-sum",))

    ok = client.post(f"/api/submissions/{sid}/attempts", headers=ADMIN, json={
        "attempts": [{"title_slug": "valid-parentheses", "code": GOOD_CODE,
                      "inference_trace": TRACE}]})
    assert ok.status_code == 202 and ok.json()["appended"] == 1

    # stub rejected on append too
    bad = client.post(f"/api/submissions/{sid}/attempts", headers=ADMIN, json={
        "attempts": [{"title_slug": "add-digits", "code": STUB_CODE,
                      "inference_trace": TRACE}]})
    assert bad.status_code == 400

    # not the owner (admin made it) -> 403 for plain user
    forbidden = client.post(f"/api/submissions/{sid}/attempts", headers=USER, json={
        "attempts": [{"title_slug": "add-digits", "code": GOOD_CODE,
                      "inference_trace": TRACE}]})
    assert forbidden.status_code == 403

    assert client.post("/api/submissions/424242/attempts", headers=ADMIN, json={
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}]}).status_code == 404

    rows = client.get(f"/api/submissions/{sid}/attempts").json()
    assert {"two-sum", "valid-parentheses"} <= {r["problem_slug"] for r in rows}


def test_default_k1_rejects_duplicate_slug(client):
    """With the default n_samples_per_problem=1, posting a second attempt for
    the same problem must be rejected by the sample-budget cap."""
    sid = make_submission(client, slugs=("two-sum",))
    r = client.post(f"/api/submissions/{sid}/attempts", headers=ADMIN, json={
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}]})
    assert r.status_code == 400
    body = r.json()
    assert "sample budget" in str(body).lower()


def test_pass_at_k_with_multiple_samples(client):
    """With n_samples_per_problem=3, attempts for the same problem accumulate,
    pass@k is computed from per-problem (n,c), and the dedup still ensures
    one accepted problem counts as one solve in total_solved."""
    body = {
        "model_name": "k3-model", "model_version": "v1",
        "language": "python3", "prompt_template": "solve please",
        "model_params": {}, "n_samples_per_problem": 3,
        "attempts": [
            {"title_slug": "two-sum", "code": GOOD_CODE, "inference_trace": TRACE},
        ],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]

    # second and third samples for the same problem — allowed up to k=3
    for _ in range(2):
        rr = client.post(f"/api/submissions/{sid}/attempts", headers=ADMIN, json={
            "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                          "inference_trace": TRACE}]})
        assert rr.status_code == 202, rr.text

    # fourth attempt → over budget
    over = client.post(f"/api/submissions/{sid}/attempts", headers=ADMIN, json={
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}]})
    assert over.status_code == 400

    detail = client.get(f"/api/submissions/{sid}").json()
    sub = detail["submission"]
    attempts = client.get(f"/api/submissions/{sid}/attempts").json()
    assert sub["n_samples_per_problem"] == 3
    assert len([a for a in attempts if a["problem_slug"] == "two-sum"]) == 3
    # dedup at submission level: a problem with any accepted sample = 1 solve
    assert sub["total_solved"] <= 1
    # pass@1 / pass@k / pass@full are populated and in [0, 1]
    for key in ("pass_at_1", "pass_at_k", "pass_at_full"):
        v = sub[key]
        assert v is None or (0.0 <= v <= 1.0), f"{key} out of range: {v}"


def test_rankings_filter_by_k(client):
    # k=1 (default) submission already exists from other tests; create a k=2 one
    body = {
        "model_name": "k2-model", "model_version": "v1",
        "language": "python3", "prompt_template": "solve please",
        "model_params": {}, "n_samples_per_problem": 2,
        "attempts": [
            {"title_slug": s, "code": GOOD_CODE, "inference_trace": TRACE}
            for s in ("two-sum", "add-digits", "24-game",
                      "valid-parentheses", "reverse-integer")
        ] * 2,  # 5 problems × 2 samples = 10 attempts
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    # k filter excludes everyone not matching
    only_k2 = client.get("/api/rankings", params={"k": 2}).json()
    assert all(e.get("n_samples_per_problem") == 2 for e in only_k2)
    only_k1 = client.get("/api/rankings", params={"k": 1}).json()
    assert all(e.get("n_samples_per_problem") == 1 for e in only_k1)


def test_delete_submission(client):
    sid = make_submission(client, slugs=("two-sum", "add-digits"))

    # no auth
    assert client.delete(f"/api/submissions/{sid}").status_code == 401
    # not the owner (admin made it, plain user tries) → 403
    assert client.delete(f"/api/submissions/{sid}", headers=USER).status_code == 403
    # unknown id → 404
    assert client.delete("/api/submissions/424242", headers=ADMIN).status_code == 404

    # owner (admin here) deletes successfully
    r = client.delete(f"/api/submissions/{sid}", headers=ADMIN)
    assert r.status_code == 204

    # SOFT delete: every public read surface now 404s …
    assert client.get(f"/api/submissions/{sid}").status_code == 404
    assert client.get(f"/api/submissions/{sid}/attempts").status_code == 404
    assert client.get(f"/api/submissions/{sid}/progress").status_code == 404
    # …and the submission no longer shows up in the public list or in /me…
    assert sid not in {s["id"] for s in client.get("/api/submissions").json()}
    assert sid not in {s["id"] for s in client.get("/api/me/submissions", headers=ADMIN).json()}
    # …but the row and its attempts/runs/trace-log entries are still in the DB.
    from sqlmodel import Session, select
    from app.db import engine
    from app.models import Attempt, Submission, TraceLogEntry
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        assert sub is not None and sub.deleted_at is not None
        atts = s.exec(select(Attempt).where(Attempt.submission_id == sid)).all()
        assert atts, "soft delete must NOT erase attempts"
        log_entries = s.exec(
            select(TraceLogEntry).where(TraceLogEntry.attempt_id.in_([a.id for a in atts]))
        ).all()
        assert log_entries, "transparency log entries must survive a soft delete"

    # second delete on the now-hidden submission → 404 (idempotent-ish)
    assert client.delete(f"/api/submissions/{sid}", headers=ADMIN).status_code == 404


def test_rankings_eligibility_complete_and_pass_rate(client):
    """The leaderboard only shows submissions that (1) have at least one
    attempt for EVERY supported problem and (2) clear the > 25% pass-rate
    threshold. An incomplete submission must never appear, even with 100%
    accepted attempts."""
    from sqlmodel import Session, select
    from app.db import engine
    from app.models import Submission

    all_slugs = ("two-sum", "add-digits", "24-game", "valid-parentheses",
                 "reverse-integer")

    # A — complete + force-high pass rate (deterministic, ignores fake-judge noise)
    sid_complete = make_submission(client, slugs=all_slugs)
    with Session(engine) as s:
        sub = s.get(Submission, sid_complete)
        sub.total_solved = 4
        sub.total_problems = 5
        sub.pass_at_1 = 0.8
        s.add(sub); s.commit()

    # B — incomplete (only 2 of 5 problems)
    sid_incomplete = make_submission(client, slugs=("two-sum", "add-digits"))
    with Session(engine) as s:
        sub = s.get(Submission, sid_incomplete)
        sub.total_solved = 2; sub.total_problems = 5; sub.pass_at_1 = 1.0
        s.add(sub); s.commit()

    # C — complete but ≤ 25% pass-rate (force 1/5)
    sid_lowpass = make_submission(client, slugs=all_slugs)
    with Session(engine) as s:
        sub = s.get(Submission, sid_lowpass)
        sub.total_solved = 1; sub.total_problems = 5; sub.pass_at_1 = 0.2
        s.add(sub); s.commit()

    rankings = client.get("/api/rankings").json()
    listed = {r["submission_id"] for r in rankings}
    assert sid_complete in listed, "complete + 80% pass-rate must appear"
    assert sid_incomplete not in listed, "incomplete submissions must NEVER appear"
    assert sid_lowpass not in listed, "complete but ≤ 25% pass-rate must NOT appear"


def test_me_views_and_derived_pages(client):
    make_submission(client, slugs=("two-sum",))

    assert client.get("/api/me/submissions").status_code == 401
    mine = client.get("/api/me/submissions", headers=ADMIN)
    assert mine.status_code == 200 and len(mine.json()) >= 1

    prog = client.get("/api/me/progress", headers=ADMIN)
    assert prog.status_code == 200 and isinstance(prog.json(), list)

    model = client.get("/api/models/test-model")
    assert model.status_code == 200
    assert model.json()["n_submissions"] >= 1
    assert client.get("/api/models/no-such-model").status_code == 404

    # admin submitter slug derives from display name ("Admin")
    sub = client.get("/api/submitters/admin")
    assert sub.status_code == 200
    assert sub.json()["n_submissions"] >= 1
    assert client.get("/api/submitters/nobody-here").status_code == 404
