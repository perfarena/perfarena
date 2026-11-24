"""'Submitted' attempts gate the leaderboard's completeness check.

System-level failures — attempts with `dispatch_warning` set or attempts
that have been pending without runs for longer than
ARENA_ATTEMPT_STALE_AFTER_S — DON'T count as submitted. A submission
that's "complete on paper" because the sweep crashed mid-way and left
ghost attempts would otherwise sneak onto the leaderboard with bogus
pass@k numbers."""
import datetime as dt

from sqlmodel import Session, select

from app import main as _main
from app.db import engine
from app.models import Attempt, Run, Submission
from conftest import HARNESS


def test_attempt_with_runs_counts_as_submitted():
    """Sanity: the happy path is unaffected by the new filter."""
    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.n_runs > 0)).first()
        if a is None:
            return                    # fixture w/o judged data — nothing to check
        stats = _main._attempt_stats_by_sub(s, [a.submission_id])
        e = stats[a.submission_id]
        assert a.problem_id in e["judged_problem_ids"]


def test_dispatch_warning_attempt_excluded_from_judged(client, monkeypatch):
    """An Attempt with `dispatch_warning` set has no real verdict and
    must NOT count toward judged_problem_ids."""
    from conftest import ADMIN, GOOD_CODE, TRACE
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "1")
    from app import dispatch
    monkeypatch.setattr(dispatch, "_check_leetcode_auth",
                        lambda: (False, "dead"))
    body = {
        "harness_slug": HARNESS,
        "model_name": "warn-only-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]

    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        assert a.dispatch_warning is not None         # backend setup correct
        assert a.n_runs == 0
        stats = _main._attempt_stats_by_sub(s, [sid])[sid]
        # Present in the "everything we touched" set but NOT the judged set.
        assert a.problem_id in stats["problem_ids"]
        assert a.problem_id not in stats["judged_problem_ids"]
        assert stats["n_attempts"] == 1
        assert stats["n_judged_attempts"] == 0


def test_stale_pending_attempt_excluded_from_judged(client):
    """An Attempt with n_runs=0, no dispatch_warning, and logged_at older
    than the stale threshold counts as STALE and is excluded — the
    dispatch worker started but never finished, so the submission isn't
    actually complete even though the row exists."""
    from conftest import ADMIN, GOOD_CODE, TRACE
    body = {
        "harness_slug": HARNESS,
        "model_name": "stale-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 1234567890",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": GOOD_CODE,
                      "inference_trace": TRACE}],
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    sid = r.json()["submission_id"]

    # Force this attempt into the "stalled" state: clear its runs + set
    # logged_at way in the past. In production, this models a container
    # bounce that left a pending attempt orphaned.
    far_past = dt.datetime.utcnow() - dt.timedelta(hours=1)
    with Session(engine) as s:
        a = s.exec(select(Attempt).where(Attempt.submission_id == sid)).first()
        # Wipe any runs that landed via the BG dispatch (fake mode writes some).
        runs = s.exec(select(Run).where(Run.attempt_id == a.id)).all()
        for r in runs: s.delete(r)
        a.n_runs = 0
        a.dispatch_warning = None
        a.logged_at = far_past
        s.add(a); s.commit()

        stats = _main._attempt_stats_by_sub(s, [sid])[sid]
        assert a.problem_id in stats["problem_ids"]
        # Pending-and-old → falls out of judged.
        assert a.problem_id not in stats["judged_problem_ids"]


def test_dispatch_warning_breaks_completeness(client, monkeypatch):
    """A would-be 'complete' submission whose attempts all hit
    dispatch_warning must NOT pass the eligibility gate. Belt-and-braces:
    even with ARENA_LEADERBOARD_GATE on, dispatch_warning'd attempts can't
    fake their way past the universe check."""
    from conftest import ADMIN, GOOD_CODE, TRACE
    monkeypatch.setenv("ARENA_LEADERBOARD_GATE", "on")
    monkeypatch.setattr(_main, "LEADERBOARD_GATE_ON", True)
    monkeypatch.setenv("ARENA_DISPATCH_ENABLED", "1")
    from app import dispatch
    monkeypatch.setattr(dispatch, "_check_leetcode_auth",
                        lambda: (False, "dead"))
    # Submit attempts for every canonical problem. Without
    # dispatch_warning these would count as "complete".
    with Session(engine) as s:
        from app.models import Problem
        canonical_slugs = [p.title_slug for p in
                           s.exec(select(Problem).where(
                               Problem.original_problem_id.is_(None))).all()][:5]
    body = {
        "harness_slug": HARNESS,
        "model_name": "ghost-test", "model_version": "v1",
        "language": "python3",
        "prompt_template": "solve cleanly 9999999999",
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": s, "code": GOOD_CODE,
                      "inference_trace": TRACE} for s in canonical_slugs],
    }
    client.post("/api/submissions", headers=ADMIN, json=body)
    # The eligibility check (computed inside /api/rankings) excludes any
    # submission whose judged_problem_ids don't cover all canonicals — so
    # our 5-attempt all-dispatch-warning submission must NOT appear.
    listed = {r["model_name"] for r in client.get("/api/rankings").json()}
    assert "ghost-test" not in listed
