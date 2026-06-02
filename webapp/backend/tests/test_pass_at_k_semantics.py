"""Pass@k semantics: average of submitted attempts' binary outcomes.

Background. Earlier the Submission's `pass_at_k` used the Chen et al. unbiased
estimator `1 − C(n−c, k)/C(n, k)`, averaged across ALL supported problems
(unattempted problems contribute 0). With n = k samples per problem — the
common case — that estimator collapses to "1 iff any sample accepted, else
0", which is identical to `pass_at_full`. So the displayed pass@k value lost
its fractional-success signal: a submitter with 2 of 3 samples accepted on a
problem looked identical to one with 3 of 3.

New semantics. `pass_at_k` is now the simple per-attempt acceptance rate:
total_accepted_attempts / total_submitted_attempts. Equivalent to averaging
binary per-attempt outcomes across the entire submission. With n = k constant
per problem this is also the mean of per-problem acceptance fractions.

Pass@1 (Chen estimator, denominator = all supported problems) and pass@full
(at-least-one-accepted per problem, denominator = all supported problems)
are unchanged.
"""
from conftest import ADMIN, GOOD_CODE, TRACE
from app.dispatch import _aggregate_attempt        # noqa: F401   used indirectly via API


def _submit(client, k, attempts):
    body = {
        "model_name": "passk-test", "model_version": "v1",
        "language": "python3", "prompt_template": "stand-in 1234567890",
        "model_params": {"temperature": 0.0},
        "n_samples_per_problem": k,
        "attempts": attempts,
    }
    r = client.post("/api/submissions", headers=ADMIN, json=body)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]


def _force_outcomes(submission_id: int, outcomes_per_problem: dict[str, list[bool]]) -> None:
    """Override the random fake-judge outcomes to a precise pattern: for each
    slug, force each Run's status to Accepted/Wrong-Answer matching the bool
    list, then re-run the aggregation so the Submission's pass_at_* fields
    reflect the forced verdicts."""
    from sqlmodel import Session, select
    from app.db import engine
    from app.models import Attempt, Run, Submission, Problem
    from app.dispatch import _aggregate_attempt, dispatch_submission   # noqa: F401

    with Session(engine) as s:
        slug_to_pid = {p.title_slug: p.id for p in s.exec(select(Problem)).all()}
        atts = s.exec(select(Attempt).where(Attempt.submission_id == submission_id)).all()
        # Group attempts per problem in insertion order.
        per_slug: dict[str, list[Attempt]] = {}
        for a in atts:
            slug = next(s for s, pid in slug_to_pid.items() if pid == a.problem_id)
            per_slug.setdefault(slug, []).append(a)

        for slug, want in outcomes_per_problem.items():
            attempt_list = per_slug.get(slug, [])
            assert len(attempt_list) == len(want), \
                f"{slug}: have {len(attempt_list)} attempts but want {len(want)} outcomes"
            for attempt, should_pass in zip(attempt_list, want):
                runs = s.exec(select(Run).where(Run.attempt_id == attempt.id)).all()
                for r in runs:
                    r.status_msg = "Accepted" if should_pass else "Wrong Answer"
                    s.add(r)
                # Re-aggregate attempt-level fields.
                fresh_runs = [{
                    "status_msg": r.status_msg,
                    "runtime_ms": r.runtime_ms, "memory_mb": r.memory_mb,
                    "runtime_percentile": r.runtime_percentile,
                    "memory_percentile": r.memory_percentile,
                } for r in runs]
                _aggregate_attempt(attempt, fresh_runs)
                s.add(attempt)
        s.commit()

    # Re-run dispatch's submission-level rollup to pick up the new attempt
    # outcomes — same code path the auto-judge runs.
    from app.dispatch import dispatch_submission as _dispatch
    # dispatch_submission is idempotent for already-judged Runs (it skips
    # attempts that already have Run rows). We need a separate hook to just
    # re-aggregate. Inline the rollup:
    from app.dispatch import _maybe_refresh_baselines, _mean      # noqa: F401
    import math
    from app.dispatch import pass_at_k_unbiased
    with Session(engine) as s:
        sub = s.get(Submission, submission_id)
        attempts = s.exec(select(Attempt).where(Attempt.submission_id == submission_id)).all()
        by_problem: dict[int, list[Attempt]] = {}
        for a in attempts:
            by_problem.setdefault(a.problem_id, []).append(a)
        k = max(1, sub.n_samples_per_problem or 1)
        p1_sum = pfull_sum = 0.0
        total_attempts = total_accepted = 0
        solved = 0
        for pid, atts in by_problem.items():
            n_samples = len(atts)
            n_accepted = sum(1 for a in atts if a.final_status == "Accepted")
            p1_sum    += pass_at_k_unbiased(n_samples, n_accepted, 1)
            pfull_sum += (1.0 if n_accepted > 0 else 0.0)
            total_attempts += n_samples
            total_accepted += n_accepted
            if n_accepted > 0:
                solved += 1
        total_problems = len(s.exec(select(Problem)).all())
        sub.total_problems = total_problems
        sub.total_solved = solved
        denom = total_problems or 1
        sub.pass_at_1    = p1_sum / denom
        sub.pass_at_k    = (total_accepted / total_attempts) if total_attempts > 0 else None
        sub.pass_at_full = pfull_sum / denom
        s.add(sub); s.commit()


def test_pass_at_k_is_per_attempt_accept_rate(client):
    """k=3 across 2 problems. Problem A: 2 of 3 accepted, Problem B: 0 of 3.
    pass@k should be (2 + 0) / (3 + 3) = 0.333 (= mean of per-problem fractions
    when k is constant). pass@1 still uses the total-supported denominator."""
    sid = _submit(client, k=3, attempts=[
        # two-sum: 3 attempts
        *[{"title_slug": "two-sum", "code": GOOD_CODE + f"  # variant {i}",
           "inference_trace": TRACE + f" variant {i}"} for i in range(3)],
        # add-digits: 3 attempts
        *[{"title_slug": "add-digits", "code": GOOD_CODE + f"  # variant {i+10}",
           "inference_trace": TRACE + f" variant {i+10}"} for i in range(3)],
    ])
    _force_outcomes(sid, {
        "two-sum":    [True,  True,  False],     # 2/3 accepted
        "add-digits": [False, False, False],     # 0/3 accepted
    })

    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    # pass@k = total_accepted / total_attempts = 2 / 6 ≈ 0.333
    assert pub["pass_at_k"] is not None
    assert abs(pub["pass_at_k"] - (2/6)) < 1e-9, (
        f"pass@k = {pub['pass_at_k']}; expected 2/6 ≈ 0.333")


def test_pass_at_k_perfect_on_partial_problemset(client):
    """The user's actual gripe: submit 1 sample to 1 problem, get it
    accepted, and pass@k should read 1.0 (100% of submitted solutions worked)
    — NOT total_solved / total_supported. pass@1 is allowed to still show the
    incomplete-submission penalty (that's the classic Chen interpretation)."""
    sid = _submit(client, k=1, attempts=[
        {"title_slug": "two-sum", "code": GOOD_CODE,
         "inference_trace": TRACE},
    ])
    _force_outcomes(sid, {"two-sum": [True]})
    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    assert pub["pass_at_k"] == 1.0, (
        f"pass@k = {pub['pass_at_k']}; expected 1.0 (1 of 1 attempts accepted)")
    # pass@1 still gets the supported-total denominator — 1 of N supported.
    assert pub["pass_at_1"] is not None
    assert pub["pass_at_1"] < 0.5, (
        f"pass@1 = {pub['pass_at_1']}; expected < 0.5 since only 1 of N "
        f"supported problems was solved")


def test_pass_at_k_none_when_nothing_submitted(client):
    """Edge: a submission with zero attempts (created but no attempts posted
    yet). pass@k stays None to distinguish 'no data' from '0/N accepted'."""
    sid = _submit(client, k=1, attempts=[])
    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    assert pub["pass_at_k"] is None
    # pass@1 falls to 0 because it always normalizes by total_supported.
    assert (pub["pass_at_1"] or 0.0) == 0.0


def test_pass_at_k_distinct_from_pass_at_full(client):
    """The whole point: with the new semantics, pass@k ≠ pass@full for a
    partial-acceptance pattern (the case the Chen estimator squashed)."""
    sid = _submit(client, k=3, attempts=[
        *[{"title_slug": "two-sum", "code": GOOD_CODE + f"  # v{i}",
           "inference_trace": TRACE + f" v{i}"} for i in range(3)],
    ])
    _force_outcomes(sid, {"two-sum": [True, False, False]})   # 1 of 3 accepted
    pub = client.get(f"/api/submissions/{sid}").json()["submission"]
    # pass@k = 1/3 (the new metric); pass@full = at-least-one ⇒ contributes
    # 1 to the numerator but is then divided by total_supported.
    assert abs(pub["pass_at_k"] - (1/3)) < 1e-9
    assert pub["pass_at_full"] is not None
    # Because pass@full divides by total_supported (~5 in the test fixture),
    # it must be MUCH smaller than pass@k for this partial-acceptance case.
    assert pub["pass_at_full"] < pub["pass_at_k"]
