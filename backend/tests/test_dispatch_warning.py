"""Auth-failure / system-error dispatches must NOT persist phantom Run rows.

Before this change every pre-flight auth failure synthesized REPEATS=5 fake
"Auth failed" Run rows per attempt — those entries leaked into pass@k math
and submission-detail tables, painting a model as "tried, failed" when it
in fact never reached LC. New behaviour:

  * Pre-flight whoami fails ⇒ stamp `dispatch_warning` on every pending
    attempt; write ZERO Run rows; bail. Next dispatch picks the attempts
    back up.
  * Per-attempt retries exhaust on auth/transient/timeout ⇒ same treatment.
  * A successful dispatch clears any stale `dispatch_warning`.

The dispatch_warning string is surfaced on AttemptResult so the sweep
client can show an informative message instead of hanging on a useless
poll loop."""
from conftest import ADMIN, GOOD_CODE, TRACE, HARNESS
from sqlmodel import Session, select

from app.db import engine
from app.models import Attempt, Run, Submission


def _submit_one(client, monkeypatch=None) -> int:
    """Build a single-attempt submission and return its submission_id.
    No dispatch happens until the caller invokes app.dispatch.dispatch_submission."""
    body = {
        "harness_slug": HARNESS,
        "model_name": "warn-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{
            "title_slug": "two-sum", "code": GOOD_CODE,
            "inference_trace": TRACE,
        }],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def test_auth_pre_flight_failure_writes_no_runs(client, monkeypatch):
    """When the pre-flight whoami fails, no Run rows are created for the
    affected attempts; instead each gets `dispatch_warning` set.

    Order matters: FastAPI's BackgroundTasks run synchronously inside
    TestClient as soon as the request returns, so the monkeypatches MUST
    be in place BEFORE the submission lands — otherwise dispatch runs
    against the real `_check_leetcode_auth` and the test sees fully-judged
    state instead of the warning we're after."""
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "1")
    from app import dispatch
    monkeypatch.setattr(dispatch, "_check_leetcode_auth",
                        lambda: (False, "cookies expired"))

    sid = _submit_one(client)

    with Session(engine) as s:
        atts = s.exec(select(Attempt).where(Attempt.submission_id == sid)).all()
        assert len(atts) == 1
        a = atts[0]
        assert a.dispatch_warning is not None
        assert "Auth failed" in a.dispatch_warning
        assert "cookies expired" in a.dispatch_warning
        # The actionable hint should mention the admin page so the user
        # knows where to go without having to look at the source.
        assert "LeetCode accounts" in a.dispatch_warning
        # And critically: NO Run rows.
        runs = s.exec(select(Run).where(Run.attempt_id == a.id)).all()
        assert runs == []
        # n_runs aggregate stays at 0; pass@k contributions are clean.
        assert a.n_runs == 0


def test_attempt_result_surfaces_dispatch_warning(client, monkeypatch):
    """The polling endpoint (/api/submissions/{id}/attempts) must include
    `dispatch_warning` so the sweep client can show an informative message
    instead of hanging on a poll loop forever."""
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "1")
    from app import dispatch
    monkeypatch.setattr(dispatch, "_check_leetcode_auth",
                        lambda: (False, "cookies expired"))

    sid = _submit_one(client)

    r = client.get(f"/api/submissions/{sid}/attempts", headers=ADMIN)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert "dispatch_warning" in row
    assert row["dispatch_warning"] is not None
    assert "Auth failed" in row["dispatch_warning"]
    # n_runs == 0 so the row doesn't contribute to leaderboard math.
    assert row["n_runs"] == 0


def test_pending_attempt_is_re_picked_after_auth_restored(client, monkeypatch):
    """After auth comes back, the same attempt should re-dispatch
    successfully — it stays in `pending` (no Run rows yet) so the next
    dispatch_submission() call processes it just like a fresh one. The
    successful pass clears the stale dispatch_warning."""
    from app import dispatch

    # 1. First pass: auth fails, warning stamped, no runs.
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "1")
    monkeypatch.setattr(dispatch, "_check_leetcode_auth",
                        lambda: (False, "dead"))
    sid = _submit_one(client)

    # 2. Second pass: drop back to fake mode, dispatch succeeds.
    monkeypatch.delenv("ARENA_DISPATCH_ENABLED", raising=False)
    dispatch.dispatch_submission(sid)

    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        runs = s.exec(select(Run).where(Run.attempt_id == a.id)).all()
        assert len(runs) > 0, "second pass should have written runs"
        assert a.dispatch_warning is None, (
            "successful dispatch must clear the stale warning"
        )


def test_existing_data_with_runs_keeps_dispatch_warning_null(client, monkeypatch):
    """Successful (fake-mode) dispatches must NOT spuriously stamp a
    warning — only system-level failures do. Belt-and-braces guard against
    a regression where the clearing logic backfires."""
    sid = _submit_one(client)
    from app import dispatch
    dispatch.dispatch_submission(sid)
    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        assert a.dispatch_warning is None
        assert a.n_runs > 0


def test_dispatch_warning_attempt_does_not_block_retry(client, monkeypatch):
    """The sample-budget gate must let an operator retry a slug whose
    only prior attempt died with a dispatch_warning. Without this:
    every auth-failed sweep tuple becomes permanently un-retryable at
    k=1 — the retry POST 400s with 'sample budget exceeded' even though
    no LC verdict was ever recorded for that slot."""
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "1")
    from app import dispatch
    # First pass: auth fails, attempt stored with dispatch_warning, no runs.
    monkeypatch.setattr(dispatch, "_check_leetcode_auth",
                        lambda: (False, "dead"))
    body = {
        "harness_slug": HARNESS,
        "model_name": "retry-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        # n_samples_per_problem stays at the default 1 — the trickiest case.
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r1 = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r1.status_code == 202
    sid = r1.json()["submission_id"]
    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        assert a.dispatch_warning is not None
        assert a.n_runs == 0

    # Second pass: the sweep retries the SAME slug. This used to 400 with
    # "sample budget exceeded" — the dispatch_warning attempt was counted
    # against the k=1 budget. After the fix it's a vacant retry slot.
    monkeypatch.setattr(dispatch, "_check_leetcode_auth",
                        lambda: (True, "ok"))
    r2 = client.post(
        f"/api/submissions/{sid}/attempts", headers=ADMIN,
        json={"attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                            "inference_trace": TRACE}]},
    )
    assert r2.status_code == 202, r2.text


def test_dispatch_warning_attempt_does_not_block_retry_at_k_gt_1(client, monkeypatch):
    """Same idea at k=3: 2 real attempts + 1 dispatch_warning attempt
    leaves room for ONE retry (not zero). Budget = real-attempt count."""
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "0")
    # Build a k=3 submission, write attempts directly so we can craft the
    # exact mix we need.
    body = {
        "harness_slug": HARNESS,
        "model_name": "k3-retry-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 9999999999",
        "model_params": {"temperature": 0.0},
        "n_samples_per_problem": 3,
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202
    sid = r.json()["submission_id"]

    # Manually add a phantom dispatch_warning attempt (representing a
    # retry the sweep tried but never got a verdict for).
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        from app.models import Problem
        problem = s.exec(select(Problem).where(Problem.title_slug == "two-sum")).first()
        phantom = Attempt(
            submission_id=sid, problem_id=problem.id,
            code="ghost", inference_trace="ghost",
            n_runs=0, n_accepted_runs=0,
            dispatch_warning="Auth failed: cookies expired",
        )
        s.add(phantom); s.commit()

    # We now have: 1 real attempt (initial) + 1 phantom + 1 fake-judge
    # auto-dispatched. The phantom shouldn't count. Real count <= 3, so the
    # next attempt should succeed (would fail with 400 if phantom counted).
    r2 = client.post(
        f"/api/submissions/{sid}/attempts", headers=ADMIN,
        json={"attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                            "inference_trace": TRACE}]},
    )
    assert r2.status_code == 202, r2.text


# ---------- automatic stale-attempt marker ----------------------------------

def test_stale_marker_stamps_old_pending_attempts(client, monkeypatch):
    """Pending attempts with no runs, no dispatch_warning, no prefailed
    reason, and a logged_at older than ARENA_ATTEMPT_STALE_AFTER_S get
    auto-marked as dispatch errors. Submissions that timed out mid-flight
    (worker crash, container bounce, …) shouldn't linger as 'queued'."""
    import datetime as dt
    from app import main as _main

    # Build a normal submission, then force one attempt into the
    # "queued forever" shape (no runs, no warning, ancient timestamp).
    body = {
        "harness_slug": HARNESS,
        "model_name": "stale-marker-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 1234512345",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]
    far_past = dt.datetime.utcnow() - dt.timedelta(hours=2)
    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        runs = s.exec(select(Run).where(Run.attempt_id == a.id)).all()
        for r in runs: s.delete(r)
        a.n_runs = 0
        a.final_status = None      # never finalized: the marker's key
        a.dispatch_warning = None
        a.prefailed_reason = None
        a.logged_at = far_past
        s.add(a); s.commit()
        target_attempt_id = a.id

    n = _main._mark_stale_pending_attempts()
    assert n >= 1

    with Session(engine) as s:
        a = s.get(Attempt, target_attempt_id)
        assert a.dispatch_warning is not None
        assert "Auto-marked dispatch error" in a.dispatch_warning


def test_stale_marker_skips_fresh_attempts(client):
    """An attempt that's only seconds old must NOT be auto-failed. The
    marker only operates past ATTEMPT_STALE_AFTER_S."""
    from app import main as _main
    body = {
        "harness_slug": HARNESS,
        "model_name": "fresh-attempt-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 6543219876",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]
    # Don't tamper with logged_at — it should be ~now.
    n = _main._mark_stale_pending_attempts()
    # n may be > 0 if other tests left stale rows behind; what matters is
    # that THIS attempt didn't get a warning stamped.
    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        assert a.dispatch_warning is None, (
            f"fresh attempt got marked: {a.dispatch_warning!r}"
        )


def test_stale_marker_idempotent(client, monkeypatch):
    """Running the marker twice in a row should mark zero on the second
    pass — already-warned rows are skipped via the IS NULL filter."""
    import datetime as dt
    from app import main as _main
    body = {
        "harness_slug": HARNESS,
        "model_name": "idem-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 1111122222",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]
    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        runs = s.exec(select(Run).where(Run.attempt_id == a.id)).all()
        for r in runs: s.delete(r)
        a.n_runs = 0
        a.final_status = None      # never finalized: the marker's key
        a.dispatch_warning = None
        a.logged_at = dt.datetime.utcnow() - dt.timedelta(hours=3)
        s.add(a); s.commit()

    n1 = _main._mark_stale_pending_attempts()
    n2 = _main._mark_stale_pending_attempts()
    assert n1 >= 1
    assert n2 == 0


def test_admin_mark_stale_endpoint(client):
    """The on-demand admin endpoint returns the number of attempts
    auto-failed and the threshold so the operator can sanity-check
    before relying on the result."""
    r = client.post("/api/admin/attempts/mark-stale", headers=ADMIN)
    assert r.status_code == 200
    d = r.json()
    assert "marked" in d
    assert "threshold_seconds" in d
    assert isinstance(d["marked"], int)


def test_append_supersedes_phantom_pending_attempt(client, monkeypatch):
    """An attempt with n_runs=0, no dispatch_warning, no prefailed_reason
    that's still in the 'actively pending' window (younger than the
    auto-marker's 30-min threshold) shouldn't block a retry. On append
    for the same slug, the phantom gets HARD-DELETED so the budget check
    sees the slot as vacant AND the BG dispatch the retry POST kicks off
    only processes the new attempt (not the dead phantom)."""
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "0")
    body = {
        "harness_slug": HARNESS,
        "model_name": "supersede-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 9988776655",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]
    # Force the attempt back into the "actively pending" shape — runs
    # cleared, no warning, no prefailed reason. This is what a sweep
    # that hit the poll-timeout window leaves behind.
    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        for r2 in s.exec(select(Run).where(Run.attempt_id == a.id)).all():
            s.delete(r2)
        a.n_runs = 0
        a.dispatch_warning = None
        a.prefailed_reason = None
        s.add(a); s.commit()
        original_id = a.id

    # Retry for the same slug. Without the supersede pass this 400s with
    # "sample budget exceeded" — the phantom counts as sample #1.
    r2 = client.post(
        f"/api/submissions/{sid}/attempts", headers=ADMIN,
        json={"attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                            "inference_trace": TRACE}]},
    )
    assert r2.status_code == 202, r2.text

    # The phantom must be GONE — hard-deleted, not just sentinel-stamped.
    # If it lingered, the BG dispatch triggered by the retry POST would
    # process it alongside the new attempt and silently double the
    # sample count past k=1.
    #
    # Don't check by id — SQLite may reuse the phantom's id for the new
    # row created by _ingest_attempts, masking a "not deleted" bug as a
    # passing test. Check by EXACT row count: exactly one Attempt should
    # remain (the retry), and it should have been dispatched (n_runs>0
    # via the fake-mode dispatch that fired after the retry POST).
    with Session(engine) as s:
        live = s.exec(select(Attempt).where(Attempt.submission_id == sid)).all()
        assert len(live) == 1, (
            f"expected exactly 1 attempt after supersede + retry, "
            f"got {len(live)} ({[(a.id, a.n_runs) for a in live]})"
        )
        # The surviving attempt should have real runs (the fake-mode
        # BG dispatch processed it). If the phantom had also been
        # dispatched alongside it we'd see runs on BOTH — but there's
        # only one Attempt now, so this serves as a regression guard.
        assert live[0].n_runs > 0
