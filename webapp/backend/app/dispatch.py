"""LeetCode dispatch worker.

Each Attempt is submitted ARENA_DISPATCH_REPEATS times (default 5). LeetCode's
runtime/memory numbers are noisy per run, so we keep every run and reduce them:

  per attempt:  best = min ms / min MB / max percentile
                avg  = arithmetic mean over the accepted runs
  per submission: mean of the per-attempt avg/best across accepted problems

Gated behind ARENA_DISPATCH_ENABLED=1. Otherwise we synthesize N plausible runs
so the pipeline can be exercised end-to-end.

Rate limiting:
- leetbot.py paces its own --repeat submissions (its --delay, ~12s, jittered).
- Between different problems we additionally sleep ARENA_DISPATCH_INTER_DELAY.
- On a 429 we exponentially back off up to ARENA_DISPATCH_MAX_RETRIES.
"""
from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta
from math import comb
from pathlib import Path

from sqlmodel import Session, select

from . import leetcode_dist
from .db import engine
from .models import (
    Attempt, Baseline, BaselineRefreshState, Problem, Run, Submission,
    SubmissionStatus,
)

LEETBOT = Path(__file__).parent.parent.parent.parent / "leetbot.py"

# Seconds to wait between dispatching attempts for DIFFERENT problems.
# Empirically, LeetCode's anti-abuse heuristic fires on rapid breadth-first
# switching between many problems (a scraper/bot fingerprint) while staying
# permissive about repeat submissions to the same problem. 12s was the prior
# default and reliably tripped the heuristic on multi-problem submissions —
# 60s paces problem-hops at a slow-human cadence (read statement → submit)
# and lets us run full 99-problem benchmark sweeps in ~100 min without
# blanket 429s. Override via ARENA_DISPATCH_INTER_DELAY when needed (set to
# ~120 for fresh accounts that LeetCode is still scrutinizing harder).
INTER_DELAY = float(os.environ.get("ARENA_DISPATCH_INTER_DELAY", "60"))
MAX_RETRIES = int(os.environ.get("ARENA_DISPATCH_MAX_RETRIES", "4"))
# Raised from 600 → 1200 to accommodate the TLE-retry worst case: a
# genuinely-TLE solution can drive REPEATS × (1 + TLE_RETRIES) submissions,
# each carrying judge time + retry/repeat delays. Normal cases finish in
# well under a minute; this is just the ceiling before we declare the
# leetbot subprocess hung.
SUBPROCESS_TIMEOUT = int(os.environ.get("ARENA_DISPATCH_SUBPROC_TIMEOUT", "1200"))
REPEATS = max(1, int(os.environ.get("ARENA_DISPATCH_REPEATS", "5")))
# On a Time Limit Exceeded verdict, leetbot re-submits up to this many extra
# times before accepting the TLE — TLE is judge-load-flaky and a borderline
# solution often passes on retry. Applied per repeat (so the worst case is
# REPEATS × (1 + TLE_RETRIES) LeetCode submissions for a genuinely-TLE
# solution). Bumped SUBPROCESS_TIMEOUT may be needed if both are large.
TLE_RETRIES = int(os.environ.get("ARENA_DISPATCH_TLE_RETRIES", "3"))


def _parse_leetbot_stdout(stdout: str) -> dict | None:
    s = stdout.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    i = 0
    while i < len(s):
        if s[i] == "{":
            try:
                obj, _ = decoder.raw_decode(s, i)
                return obj
            except json.JSONDecodeError:
                pass
        i += 1
    return None


def _is_rate_limited(proc: subprocess.CompletedProcess) -> bool:
    blob = (proc.stdout + proc.stderr).lower()
    return "rate limit" in blob or "429" in blob


_AUTH_FIX_HINT = (
    "LeetCode session expired — refresh cookies in .env (LEETCODE_COOKIE_HEADER "
    "or LEETCODE_SESSION+LEETCODE_CSRF_TOKEN) and re-run `leetbot.py --whoami`"
)


def _classify_subproc_failure(
    proc: subprocess.CompletedProcess,
) -> tuple[str, str] | None:
    """Inspect a leetbot subprocess that produced no parseable runs. Return
    (status_msg, note) for the recognised failure modes; None if we couldn't
    classify and the caller should fall back to a generic parse-error."""
    blob = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    if ("403 forbidden" in blob or "401 unauthorized" in blob
            or '"issignedin": false' in blob):
        return "Auth failed", _AUTH_FIX_HINT
    if "csrf" in blob and ("invalid" in blob or "expired" in blob or "missing" in blob):
        return "Auth failed", "CSRF token rejected — refresh cookies in .env"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        return "Dispatch error", f"leetbot exited {proc.returncode}: {tail[0][:200]}"
    return None


def _failure_runs(repeats: int, status_msg: str, note: str,
                  proc: subprocess.CompletedProcess | None = None) -> list[dict]:
    """Build `repeats` identical failure dicts so the per-attempt aggregate
    sees the same shape as a successful dispatch (N runs, none accepted)."""
    base = {
        "status_msg": status_msg, "note": note,
        "runtime_ms": None, "memory_mb": None,
        "runtime_percentile": None, "memory_percentile": None,
        "total_correct": 0, "total_testcases": 0,
    }
    if proc is not None:
        base["raw_stdout"] = (proc.stdout or "")[-800:]
        base["raw_stderr"] = (proc.stderr or "")[-800:]
    return [dict(base) for _ in range(repeats)]


def _check_leetcode_auth() -> tuple[bool, str]:
    """One-shot `leetbot.py --whoami` pre-flight. Returns (is_signed_in, msg)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(LEETBOT), "--whoami"],
            capture_output=True, text=True, timeout=20, cwd=str(LEETBOT.parent),
        )
    except Exception as e:  # noqa: BLE001
        return False, f"whoami subprocess failed: {e}"
    if proc.returncode != 0:
        return False, f"whoami exited {proc.returncode}: {(proc.stderr or '')[-200:]}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, f"whoami returned non-JSON: {(proc.stdout or '')[-200:]}"
    if data.get("isSignedIn"):
        return True, f"signed in as {data.get('username') or '?'}"
    return False, _AUTH_FIX_HINT


def _normalize_runs(parsed: dict | None) -> list[dict]:
    """leetbot prints a single run dict (repeat=1) or {"runs":[...]} (repeat>1)."""
    if parsed is None:
        return []
    if isinstance(parsed.get("runs"), list):
        return parsed["runs"]
    return [parsed]


def _real_dispatch_repeated(problem: Problem, language: str, code: str, repeats: int) -> list[dict]:
    """Submit `code` `repeats` times via leetbot, return the list of run dicts."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        backoff = 5.0
        for attempt_n in range(MAX_RETRIES + 1):
            try:
                proc = subprocess.run(
                    [
                        sys.executable, str(LEETBOT),
                        problem.title_slug, language, path,
                        "--repeat", str(repeats),
                        "--tle-retries", str(TLE_RETRIES),
                    ],
                    capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
                    cwd=str(LEETBOT.parent),
                )
            except subprocess.TimeoutExpired:
                return _failure_runs(repeats, "Dispatch timeout",
                                     f"leetbot exceeded {SUBPROCESS_TIMEOUT}s")
            if _is_rate_limited(proc) and attempt_n < MAX_RETRIES:
                sleep = backoff * (2 ** attempt_n) * (1.0 + random.uniform(-0.2, 0.2))
                print(f"[dispatch] rate limited; backing off {sleep:.1f}s", file=sys.stderr)
                time.sleep(sleep)
                continue
            runs = _normalize_runs(_parse_leetbot_stdout(proc.stdout))
            if not runs:
                # Classify the failure (auth, csrf, non-zero exit, …); fall back
                # to a generic parse error if nothing matches.
                classified = _classify_subproc_failure(proc)
                if classified is not None:
                    status_msg, note = classified
                    return _failure_runs(repeats, status_msg, note, proc)
                return _failure_runs(repeats, "Dispatch parse error",
                                     "leetbot produced no JSON we could parse",
                                     proc)
            return runs
        return _failure_runs(repeats, "Rate limited",
                             f"hit LeetCode rate limit after {MAX_RETRIES} retries")
    finally:
        Path(path).unlink(missing_ok=True)


def _fake_dispatch_repeated(repeats: int, language: str, code: str) -> list[dict]:
    """Synthesize N runs of one attempt.

    NOTE: this is NOT a real judge — it does not run the code and cannot know
    whether it actually solves the problem. It only deterministically fails
    code that isn't even a plausible attempt (stub/placeholder/garbage); for
    anything that passes that gate it fabricates a plausible verdict. Real
    correctness requires ARENA_DISPATCH_ENABLED=1 (dispatch to LeetCode).

    Test hook: a `## fake_lang: <name>` comment in `code` makes the synthesized
    runs report `lang=<name>` instead of `lang=<language>`. The
    language-conformance guard downstream (dispatch_submission's lang
    override) then trips, so test_dispatch.test_language_mismatch_marks_run
    can exercise that path without touching LeetCode.
    """
    import re
    from .validation import validate_code
    m = re.search(r"##\s*fake_lang:\s*(\S+)", code or "")
    judged_lang = m.group(1) if m else language

    reason = validate_code(language, code)
    if reason:
        return [{
            "status_msg": "Compile Error", "runtime_ms": None, "memory_mb": None,
            "runtime_percentile": None, "memory_percentile": None,
            "total_correct": 0, "total_testcases": 30,
            "lang": judged_lang,
            "note": f"rejected by sanity check: {reason}",
        } for _ in range(repeats)]
    accepted = random.random() < 0.65
    if not accepted:
        status = random.choice(["Wrong Answer", "Time Limit Exceeded", "Runtime Error"])
        return [{
            "status_msg": status, "runtime_ms": None, "memory_mb": None,
            "runtime_percentile": None, "memory_percentile": None,
            "total_correct": random.randint(0, 29), "total_testcases": 30,
            "lang": judged_lang,
        } for _ in range(repeats)]
    base_rt = random.uniform(5, 180)
    base_mem = random.uniform(14, 22)
    base_rt_p = random.uniform(20, 99)
    base_mem_p = random.uniform(15, 99)
    out = []
    for _ in range(repeats):
        rt = max(0.0, base_rt * random.uniform(0.85, 1.2))
        mem = max(13.0, base_mem * random.uniform(0.97, 1.04))
        out.append({
            "submission_id": str(random.randint(10**9, 10**10)),
            "status_msg": "Accepted",
            "runtime_ms": round(rt, 2),
            "memory_mb": round(mem, 3),
            "runtime_percentile": round(min(100.0, max(0.0, base_rt_p * random.uniform(0.95, 1.05))), 2),
            "memory_percentile": round(min(100.0, max(0.0, base_mem_p * random.uniform(0.95, 1.05))), 2),
            "total_correct": 30, "total_testcases": 30,
            "lang": judged_lang,
        })
    return out


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def pass_at_k_unbiased(n: int, c: int, k: int) -> float:
    """Chen et al. pass@k estimator: 1 - C(n-c, k) / C(n, k). Returns 0 when
    the sample budget wasn't met (n < k) or when no sample was accepted."""
    if n <= 0 or k <= 0 or n < k or c <= 0:
        return 0.0
    if c >= n:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def _aggregate_attempt(a: Attempt, runs: list[dict]) -> None:
    """Fill the per-attempt avg/best columns from its N run dicts."""
    statuses = [r.get("status_msg", "Unknown") for r in runs]
    accepted = [r for r in runs if r.get("status_msg") == "Accepted"]
    a.n_runs = len(runs)
    a.n_accepted_runs = len(accepted)
    # Majority rule: a correct solution is Accepted on (almost) every run; a
    # flaky/borderline one is not. Metrics are computed over accepted runs only.
    is_accepted = len(accepted) >= math.ceil(len(runs) / 2) if runs else False
    if is_accepted:
        a.final_status = "Accepted"
    else:
        non = [s for s in statuses if s != "Accepted"] or statuses
        a.final_status = Counter(non).most_common(1)[0][0] if non else "Unknown"

    rt = [r["runtime_ms"] for r in accepted if r.get("runtime_ms") is not None]
    mem = [r["memory_mb"] for r in accepted if r.get("memory_mb") is not None]
    rtp = [r["runtime_percentile"] for r in accepted if r.get("runtime_percentile") is not None]
    memp = [r["memory_percentile"] for r in accepted if r.get("memory_percentile") is not None]

    a.avg_runtime_ms = _mean(rt)
    a.best_runtime_ms = min(rt) if rt else None
    a.avg_memory_mb = _mean(mem)
    a.best_memory_mb = min(mem) if mem else None
    a.avg_runtime_percentile = _mean(rtp)
    a.best_runtime_percentile = max(rtp) if rtp else None
    a.avg_memory_percentile = _mean(memp)
    a.best_memory_percentile = max(memp) if memp else None


def dispatch_submission(submission_id: int) -> None:
    """Idempotent: dispatches every attempt that has no runs yet, N times each,
    then recomputes per-attempt and submission-level aggregates."""
    real = os.environ.get("ARENA_DISPATCH_ENABLED") == "1"
    with Session(engine) as session:
        sub = session.get(Submission, submission_id)
        if not sub:
            return
        sub.status = SubmissionStatus.dispatching
        session.add(sub)
        session.commit()

        attempts = session.exec(
            select(Attempt).where(Attempt.submission_id == submission_id)
        ).all()
        pending = [
            a for a in attempts
            if not session.exec(select(Run).where(Run.attempt_id == a.id)).first()
        ]

        # Pre-flight: in real mode, check LeetCode auth ONCE before burning
        # through every pending attempt with the same 403. If auth is dead we
        # synthesise N "Auth failed" runs per attempt and skip dispatch entirely.
        auth_error: str | None = None
        if real and pending:
            ok, msg = _check_leetcode_auth()
            if not ok:
                auth_error = msg
                print(f"[dispatch] LeetCode auth check failed: {msg}",
                      file=sys.stderr)

        for idx, a in enumerate(pending):
            problem = session.get(Problem, a.problem_id)
            try:
                if a.prefailed_reason:
                    # Client already flagged this response as malformed (wrong
                    # language extracted, no code, etc.). We record one synthetic
                    # failure run with the reason — no LeetCode quota burned,
                    # no inter-problem delay charged, and the raw model output
                    # stays in attempt.trace for analysis.
                    runs = _failure_runs(
                        1, a.prefailed_reason,
                        "client-detected malformed response; "
                        "raw model output preserved in attempt.trace",
                    )
                elif real:
                    if auth_error:
                        runs = _failure_runs(REPEATS, "Auth failed", auth_error)
                    else:
                        if idx > 0:
                            time.sleep(INTER_DELAY * (1.0 + random.uniform(-0.2, 0.2)))
                        runs = _real_dispatch_repeated(problem, sub.language, a.code, REPEATS)
                else:
                    runs = _fake_dispatch_repeated(REPEATS, sub.language, a.code)
            except Exception as e:  # noqa: BLE001
                runs = _failure_runs(REPEATS, "Dispatch error", str(e))

            # Language-conformance guard: applied PRE-aggregation so a
            # smuggled-Java-as-python3 run counts as failed for pass@k and
            # contributes nothing to runtime / memory baselines. We mutate the
            # `runs` dict in place so both the Run-table write below AND
            # `_aggregate_attempt` see the overridden status_msg. See the
            # commit for the rectangle-overlap incident that motivated this.
            for result in runs:
                judged_lang = result.get("lang")
                if judged_lang and judged_lang != sub.language:
                    # Preserve the original verdict on the result so error logs
                    # / debug tooling can see what LC really said before our
                    # override.
                    result["original_status_msg"] = result.get("status_msg")
                    result["status_msg"] = "Language mismatch"

            for i, result in enumerate(runs, start=1):
                status_msg = result.get("status_msg", "Unknown")
                # leetbot stuffs LeetCode's verbatim /check/ payload under
                # `leetcode_full_response`. Pull it out for its own column so
                # the UI can render it on non-Accepted runs even when LC
                # ships a field shape the parser doesn't enumerate. None in
                # fake-mode / pre-flight-failure / language-mismatch
                # synthetic results — those paths never talk to LC.
                lc_full = result.get("leetcode_full_response")
                session.add(Run(
                    attempt_id=a.id,
                    run_index=i,
                    submission_id_external=result.get("submission_id"),
                    status_msg=status_msg,
                    runtime_ms=result.get("runtime_ms"),
                    memory_mb=result.get("memory_mb"),
                    runtime_percentile=result.get("runtime_percentile"),
                    memory_percentile=result.get("memory_percentile"),
                    total_correct=result.get("total_correct"),
                    total_testcases=result.get("total_testcases"),
                    raw_response=result,
                    leetcode_full_response=lc_full,
                ))
            _aggregate_attempt(a, runs)
            session.add(a)
        session.commit()

        # Recompute every attempt's aggregate (covers re-runs / appended attempts).
        for a in attempts:
            if a.n_runs == 0:
                runs_for_a = session.exec(select(Run).where(Run.attempt_id == a.id)).all()
                if runs_for_a:
                    _aggregate_attempt(a, [r.model_dump() for r in runs_for_a])
                    session.add(a)

        # Per-problem rollup: each problem contributes at most ONE solve to the
        # submission totals, even if the user appended several attempts for it.
        # The chosen attempt = the one with the highest accepted-runs reliability,
        # tie-broken by lowest avg_runtime_ms. Attempts for other problems still
        # appear individually on the attempt-results view; this dedup only
        # affects submission-level totals and ranking metrics.
        by_problem: dict[int, list[Attempt]] = {}
        for a in attempts:
            by_problem.setdefault(a.problem_id, []).append(a)

        solved = 0
        avg_rt, best_rt, avg_mem, best_mem = [], [], [], []
        avg_rtp, avg_memp = [], []
        t_h_avg, t_h_exp, m_h_avg, m_h_exp = [], [], [], []
        # Per-submission pass metrics:
        #   pass@1    — Chen unbiased estimator with k=1, i.e. c/n per problem,
        #               averaged across ALL supported problems (unattempted
        #               problems contribute 0). The classic single-shot rate.
        #   pass@k    — simple fraction of accepted submitted samples. Computed
        #               as total_accepted_attempts / total_submitted_attempts
        #               (= the average of binary per-attempt outcomes). With
        #               n == k samples per problem this is also equivalent to
        #               the mean of per-problem acceptance fractions.
        #               REPLACES the prior Chen estimator pass@k which, when
        #               n = k, collapses to pass@full (1 iff any accepted) and
        #               showed identical numbers — losing the fractional signal
        #               we actually care about ("2 of 3 samples worked").
        #   pass@full — at-least-one-accepted across the k samples for a
        #               problem, averaged across all supported problems.
        k = max(1, sub.n_samples_per_problem or 1)
        p1_sum = pfull_sum = 0.0
        total_attempts = 0       # samples actually submitted across all problems
        total_accepted = 0       # accepted samples across all problems
        for pid, atts in by_problem.items():
            n_samples = len(atts)
            n_accepted = sum(1 for a in atts if a.final_status == "Accepted")
            p1_sum    += pass_at_k_unbiased(n_samples, n_accepted, 1)
            pfull_sum += (1.0 if n_accepted > 0 else 0.0)
            total_attempts += n_samples
            total_accepted += n_accepted

            accepted_atts = [a for a in atts if a.final_status == "Accepted"]
            if not accepted_atts:
                continue
            best = max(accepted_atts, key=lambda a: (
                (a.n_accepted_runs / a.n_runs) if a.n_runs else 0.0,
                -(a.avg_runtime_ms if a.avg_runtime_ms is not None else 1e18),
            ))
            solved += 1
            if best.avg_runtime_ms is not None:    avg_rt.append(best.avg_runtime_ms)
            if best.best_runtime_ms is not None:   best_rt.append(best.best_runtime_ms)
            if best.avg_memory_mb is not None:     avg_mem.append(best.avg_memory_mb)
            if best.best_memory_mb is not None:    best_mem.append(best.best_memory_mb)
            if best.avg_runtime_percentile is not None:  avg_rtp.append(best.avg_runtime_percentile)
            if best.avg_memory_percentile is not None:   avg_memp.append(best.avg_memory_percentile)

            baseline = session.exec(
                select(Baseline).where(
                    Baseline.problem_id == pid,
                    Baseline.language == sub.language,
                )
            ).first()
            if baseline:
                if best.avg_runtime_ms and best.avg_runtime_ms > 0:
                    t_h_avg.append(baseline.runtime_ms_avg / best.avg_runtime_ms)
                    t_h_exp.append(baseline.runtime_ms_expert / best.avg_runtime_ms)
                if best.avg_memory_mb and best.avg_memory_mb > 0:
                    m_h_avg.append(baseline.memory_mb_avg / best.avg_memory_mb)
                    m_h_exp.append(baseline.memory_mb_expert / best.avg_memory_mb)

        total_supported = session.exec(select(Problem)).all()
        sub.total_problems = len(total_supported)
        sub.total_solved = solved
        denom = sub.total_problems or 1
        sub.pass_at_1    = p1_sum / denom
        # New pass@k semantics (see comment above): mean over actually-submitted
        # attempts of the binary accept/reject outcome. None when nothing's been
        # submitted yet — distinguishes "not submitted" from "submitted 0/N
        # accepted".
        sub.pass_at_k    = (total_accepted / total_attempts) if total_attempts > 0 else None
        sub.pass_at_full = pfull_sum / denom
        sub.avg_runtime_ms = _mean(avg_rt)
        sub.best_runtime_ms = _mean(best_rt)
        sub.avg_memory_mb = _mean(avg_mem)
        sub.best_memory_mb = _mean(best_mem)
        sub.avg_runtime_percentile = _mean(avg_rtp)
        sub.avg_memory_percentile = _mean(avg_memp)
        sub.time_human_avg = _mean(t_h_avg)
        sub.time_human_expert = _mean(t_h_exp)
        sub.mem_human_avg = _mean(m_h_avg)
        sub.mem_human_expert = _mean(m_h_exp)
        sub.status = SubmissionStatus.judged
        session.add(sub)
        session.commit()

        # Guarantee a baseline exists for every (problem, language) pair this
        # submission just touched. Compute-once-if-missing: pairs that already
        # have a real `leetcode-distribution` row are skipped; pairs without
        # one get the real LeetCode distribution fetched now (anchored on this
        # pair's freshest accepted external submission id), falling back to a
        # synthetic placeholder when LeetCode is unreachable so the coefficient
        # math is never blocked by a "missing" baseline. Runs in fake mode too
        # (synthetic-only) so locally-generated pairs still get a baseline.
        _ensure_baselines_for_submission(session, sub, real=real)


# ---------- auto-refreshing baselines from real submissions -----------------

def _maybe_refresh_baselines(session: Session) -> None:
    """Run after every real submission. The global refresh below gates per
    `(problem, language)` pair: it only fetches LeetCode for pairs that are
    still synthetic OR whose `leetcode-distribution` row is older than
    `ARENA_BASELINE_STALE_DAYS`. So fresh-real pairs cost nothing; synthetic
    placeholders get replaced opportunistically on the next submission."""
    if os.environ.get("ARENA_AUTO_REFRESH_BASELINES", "1") == "0":
        return

    n_refreshed, langs_touched = _refresh_baselines_global(session)
    if n_refreshed:
        print(f"[baselines] refreshed {n_refreshed} (problem,language) baselines",
              file=sys.stderr)
    for lang in langs_touched:
        _recompute_human_coefficients(session, lang)

    state = session.get(BaselineRefreshState, 1) or BaselineRefreshState(id=1)
    state.last_refresh_at = datetime.utcnow()
    state.submissions_since_last = 0
    session.add(state); session.commit()


def _refresh_baselines_global(session: Session) -> tuple[int, set[str]]:
    """Walk every (problem, language) with an accepted Run that has a stored
    submission_id_external, pull the freshest LeetCode histogram, and upsert
    the Baseline row. Skips pairs whose existing row is already
    leetcode-distribution AND younger than ARENA_BASELINE_STALE_DAYS.

    Returns (n_upserted, set_of_languages_touched). Languages_touched is fed
    into the coefficient recomputer."""
    stale_days = float(os.environ.get("ARENA_BASELINE_STALE_DAYS", "14"))
    threshold = datetime.utcnow() - timedelta(days=stale_days)
    rate_sleep_s = float(os.environ.get("ARENA_BASELINE_REFRESH_RPS", "1.0"))

    # (pid, language) -> most-recent accepted external submission_id (the anchor).
    anchors: dict[tuple[int, str], int] = {}
    runs = session.exec(
        select(Run)
        .where(Run.status_msg == "Accepted")
        .where(Run.submission_id_external.is_not(None))
        .order_by(Run.created_at.desc())
    ).all()
    for r in runs:
        a = session.get(Attempt, r.attempt_id)
        if not a:
            continue
        s = session.get(Submission, a.submission_id)
        if not s or s.deleted_at is not None:
            continue
        key = (a.problem_id, s.language)
        if key in anchors:
            continue
        try:
            anchors[key] = int(r.submission_id_external)
        except (TypeError, ValueError):
            continue

    if not anchors:
        return 0, set()

    try:
        client = leetcode_dist.build_client()
    except leetcode_dist.AuthMissing as e:
        print(f"[baselines] auth missing — skipping refresh: {e}", file=sys.stderr)
        return 0, set()

    upserted = 0
    langs_touched: set[str] = set()
    try:
        for (pid, lang), sid in anchors.items():
            existing = session.exec(
                select(Baseline).where(
                    Baseline.problem_id == pid, Baseline.language == lang,
                )
            ).first()
            # Skip pairs that already have a fresh real-distribution baseline.
            if existing and existing.source == "leetcode-distribution" \
                and existing.fetched_at and existing.fetched_at >= threshold:
                continue

            try:
                details = leetcode_dist.fetch_distribution(sid, client=client)
            except Exception as e:                                  # noqa: BLE001
                print(f"[baselines] fetch err pid={pid} lang={lang} sid={sid}: {e}",
                      file=sys.stderr)
                details = None
            if not details:
                time.sleep(rate_sleep_s)
                continue
            try:
                d = leetcode_dist.derive(
                    details["runtimeDistribution"], details["memoryDistribution"],
                )
            except Exception:                                       # noqa: BLE001
                time.sleep(rate_sleep_s)
                continue

            if existing is None:
                existing = Baseline(
                    problem_id=pid, language=lang,
                    runtime_ms_avg=0, runtime_ms_expert=0,
                    memory_mb_avg=0, memory_mb_expert=0,
                )
            existing.runtime_ms_avg    = d["runtime_ms_avg"]
            existing.runtime_ms_expert = d["runtime_ms_expert"]
            existing.memory_mb_avg     = d["memory_mb_avg"]
            existing.memory_mb_expert  = d["memory_mb_expert"]
            existing.source = "leetcode-distribution"
            existing.fetched_at = datetime.utcnow()
            session.add(existing)
            upserted += 1
            langs_touched.add(lang)
            time.sleep(rate_sleep_s)
        session.commit()
    finally:
        client.close()

    return upserted, langs_touched


def _recompute_human_coefficients(session: Session, language: str) -> None:
    """After baselines move, recompute the four `*_human_*` coefficient
    fields on every existing judged submission in this language so the
    rankings table reflects the new cohort. Uses the same per-problem
    best-attempt selection as the live rollup, so the numbers stay
    consistent with what dispatch would have written for a fresh batch."""
    subs = session.exec(
        select(Submission)
        .where(Submission.language == language)
        .where(Submission.status == SubmissionStatus.judged)
        .where(Submission.deleted_at.is_(None))
    ).all()
    if not subs:
        return
    baselines = {
        b.problem_id: b
        for b in session.exec(
            select(Baseline).where(Baseline.language == language)
        ).all()
    }
    for s in subs:
        attempts = session.exec(
            select(Attempt).where(Attempt.submission_id == s.id)
        ).all()
        by_problem: dict[int, list[Attempt]] = {}
        for a in attempts:
            by_problem.setdefault(a.problem_id, []).append(a)
        t_h_avg, t_h_exp, m_h_avg, m_h_exp = [], [], [], []
        for pid, atts in by_problem.items():
            accepted_atts = [a for a in atts if a.final_status == "Accepted"]
            if not accepted_atts:
                continue
            best = max(accepted_atts, key=lambda a: (
                (a.n_accepted_runs / a.n_runs) if a.n_runs else 0.0,
                -(a.avg_runtime_ms if a.avg_runtime_ms is not None else 1e18),
            ))
            b = baselines.get(pid)
            if not b:
                continue
            if best.avg_runtime_ms and best.avg_runtime_ms > 0:
                t_h_avg.append(b.runtime_ms_avg / best.avg_runtime_ms)
                t_h_exp.append(b.runtime_ms_expert / best.avg_runtime_ms)
            if best.avg_memory_mb and best.avg_memory_mb > 0:
                m_h_avg.append(b.memory_mb_avg / best.avg_memory_mb)
                m_h_exp.append(b.memory_mb_expert / best.avg_memory_mb)
        s.time_human_avg    = _mean(t_h_avg)
        s.time_human_expert = _mean(t_h_exp)
        s.mem_human_avg     = _mean(m_h_avg)
        s.mem_human_expert  = _mean(m_h_exp)
        session.add(s)
    session.commit()


# ---------- per-pair baseline guarantee (compute-once-if-missing) -----------

def _latest_accepted_external(session: Session, problem_id: int,
                              language: str) -> int | None:
    """Most-recent accepted Run for (problem, language) that carries a stored
    LeetCode submission_id_external — the anchor we feed submissionDetails to
    pull that pair's real population histogram."""
    row = session.exec(
        select(Run.submission_id_external)
        .join(Attempt, Attempt.id == Run.attempt_id)
        .join(Submission, Submission.id == Attempt.submission_id)
        .where(
            Attempt.problem_id == problem_id,
            Submission.language == language,
            Submission.deleted_at.is_(None),
            Run.status_msg == "Accepted",
            Run.submission_id_external.is_not(None),
        )
        .order_by(Run.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    try:
        return int(row)
    except (TypeError, ValueError):
        return None


def _synthetic_baseline_row(problem: Problem, language: str) -> Baseline:
    """A plausible placeholder distribution derived from difficulty + a
    per-language efficiency multiplier (same model as `python -m app.baselines`).
    Tagged `source="synthetic"` so the UI labels coefficients as estimates and
    the refresher knows to upgrade it to real LeetCode data later."""
    from . import baselines as _baselines  # lazy: avoids import cost at module load
    level = problem.level if problem.level in _baselines.LEVEL_BASE_RUNTIME else "Medium"
    seed = hash((problem.id, language)) & 0xFFFFFFFF
    rt_avg, rt_exp, mem_avg, mem_exp = _baselines._baseline_for(level, language, seed)
    return Baseline(
        problem_id=problem.id, language=language,
        runtime_ms_avg=round(rt_avg, 2), runtime_ms_expert=round(rt_exp, 2),
        memory_mb_avg=round(mem_avg, 3), memory_mb_expert=round(mem_exp, 3),
        source="synthetic",
    )


def _ensure_baseline(session: Session, problem_id: int, language: str, *,
                     anchor_sid: int | None, client) -> str:
    """Guarantee a Baseline row exists for one (problem, language) pair.

    Returns the action taken:
      "kept"      — already a fresh real `leetcode-distribution` row, untouched
      "real"      — fetched/refreshed the real LeetCode histogram
      "synthetic" — seeded a synthetic placeholder (no anchor/auth/fetch)
      "skip"      — problem row not found (shouldn't happen)

    Never leaves the pair as "missing" when a synthetic fallback is possible.
    `client` may be None (auth unavailable) — we then go straight to synthetic.
    """
    existing = session.exec(
        select(Baseline).where(
            Baseline.problem_id == problem_id, Baseline.language == language,
        )
    ).first()
    # Already real → nothing to do here (staleness handled by the global sweep).
    if existing is not None and existing.source == "leetcode-distribution":
        return "kept"

    # Prefer the real LeetCode population distribution for this exact pair.
    if anchor_sid is not None and client is not None:
        details = None
        try:
            details = leetcode_dist.fetch_distribution(int(anchor_sid), client=client)
        except Exception as e:                                          # noqa: BLE001
            print(f"[baselines] fetch err pid={problem_id} lang={language} "
                  f"sid={anchor_sid}: {e}", file=sys.stderr)
        if details:
            try:
                d = leetcode_dist.derive(
                    details["runtimeDistribution"], details["memoryDistribution"],
                )
            except Exception:                                           # noqa: BLE001
                d = None
            if d:
                row = existing or Baseline(
                    problem_id=problem_id, language=language,
                    runtime_ms_avg=0, runtime_ms_expert=0,
                    memory_mb_avg=0, memory_mb_expert=0,
                )
                row.runtime_ms_avg    = d["runtime_ms_avg"]
                row.runtime_ms_expert = d["runtime_ms_expert"]
                row.memory_mb_avg     = d["memory_mb_avg"]
                row.memory_mb_expert  = d["memory_mb_expert"]
                row.source = "leetcode-distribution"
                row.fetched_at = datetime.utcnow()
                session.add(row)
                return "real"

    # Fallback: only create a synthetic placeholder when nothing exists yet.
    # An existing synthetic row is left in place for the global sweep to upgrade.
    if existing is None:
        problem = session.get(Problem, problem_id)
        if problem is None:
            return "skip"
        session.add(_synthetic_baseline_row(problem, language))
        return "synthetic"
    return "kept"


def _ensure_baselines_for_submission(session: Session, sub: Submission, *,
                                     real: bool) -> None:
    """Targeted, cheap per-submission baseline guarantee. For each (problem,
    language) pair this submission attempted, ensure a baseline exists —
    fetching real LeetCode data when an accepted anchor + auth are available,
    otherwise a synthetic placeholder. Pairs already backed by real data are
    skipped, so this is a no-op once a pair has been populated."""
    if os.environ.get("ARENA_AUTO_REFRESH_BASELINES", "1") == "0":
        return
    pids = {
        a.problem_id
        for a in session.exec(select(Attempt).where(Attempt.submission_id == sub.id)).all()
    }
    if not pids:
        return
    # Which pairs still need anything? (Skip those already real.)
    todo = [
        pid for pid in pids
        if not _has_real_baseline(session, pid, sub.language)
    ]
    if not todo:
        return

    # Build a LeetCode client once, only if real fetching is enabled + auth is
    # present. Without it we still seed synthetic placeholders below.
    client = None
    if real:
        try:
            client = leetcode_dist.build_client()
        except leetcode_dist.AuthMissing as e:
            print(f"[baselines] auth missing — synthetic placeholders only: {e}",
                  file=sys.stderr)

    # Opportunistic upgrade: when we DO have a working client, this is also
    # the cheapest moment to back-fill OTHER synthetic baselines in the same
    # language whose previous auto-refresh attempts ran while auth was down.
    # Capped to keep the per-submission hook fast; the admin "Refresh
    # baselines now" button still does the global sweep when needed.
    if client is not None:
        cap = int(os.environ.get("ARENA_BASELINE_OPPORTUNISTIC_CAP", "8"))
        extras = _other_synthetic_pids_with_anchor(session, sub.language, exclude=set(todo), limit=cap)
        todo.extend(extras)

    rate_sleep_s = float(os.environ.get("ARENA_BASELINE_REFRESH_RPS", "1.0"))
    touched = False
    try:
        for pid in todo:
            anchor = _latest_accepted_external(session, pid, sub.language) if client else None
            action = _ensure_baseline(session, pid, sub.language,
                                      anchor_sid=anchor, client=client)
            if action in ("real", "synthetic"):
                touched = True
            if action == "real":
                time.sleep(rate_sleep_s)   # be gentle on the LeetCode API
        session.commit()
    finally:
        if client is not None:
            client.close()

    if touched:
        _recompute_human_coefficients(session, sub.language)


def _other_synthetic_pids_with_anchor(
    session: Session, language: str, *, exclude: set[int], limit: int,
) -> list[int]:
    """Find synthetic baselines in `language` whose problem ALSO has an
    accepted run carrying an external sid (so a real LeetCode fetch can
    succeed). Used by the opportunistic-upgrade path on the per-submission
    hook so a steady stream of submissions self-heals the language's
    placeholders over time without needing the admin refresh button."""
    rows = session.exec(
        select(Baseline)
        .where(Baseline.language == language)
        .where(Baseline.source == "synthetic")
    ).all()
    out: list[int] = []
    for b in rows:
        if b.problem_id in exclude:
            continue
        if _latest_accepted_external(session, b.problem_id, language) is None:
            continue          # no anchor → fetch would fail anyway, skip
        out.append(b.problem_id)
        if len(out) >= limit:
            break
    return out


def _has_real_baseline(session: Session, problem_id: int, language: str) -> bool:
    b = session.exec(
        select(Baseline).where(
            Baseline.problem_id == problem_id, Baseline.language == language,
        )
    ).first()
    return b is not None and b.source == "leetcode-distribution"


# Module-level state for the long-running baseline refresh job. The admin
# "Refresh now" button POSTs and gets a 202 back IMMEDIATELY; the work
# happens in a daemon thread that updates this dict as it goes. A separate
# GET endpoint returns it so the UI can show progress + flip back to idle
# when the job finishes.
import threading                                                          # noqa: E402

_REFRESH_STATE_LOCK = threading.Lock()
_REFRESH_STATE: dict = {
    "state": "idle",          # idle | running | done | error
    "started_at": None,
    "ended_at": None,
    "processed": 0,
    "total": 0,
    "real": 0,
    "synthetic": 0,
    "kept": 0,
    "current": None,           # last (pid, lang) the worker touched
    "error": None,
    "languages_touched": [],
}


def _set_refresh(**fields) -> None:
    """Thread-safe progress update."""
    with _REFRESH_STATE_LOCK:
        _REFRESH_STATE.update(fields)


def get_refresh_progress() -> dict:
    """Read-out the background refresh state (for the admin UI to poll)."""
    with _REFRESH_STATE_LOCK:
        return dict(_REFRESH_STATE)


def start_refresh_in_background() -> dict:
    """Kick off a baseline refresh in a daemon thread. Returns the current
    state right away — the UI keeps polling until `state == 'done'` (or
    'error'). Safe to call again while a refresh is already running: the
    second call is a no-op that just returns the current state."""
    with _REFRESH_STATE_LOCK:
        if _REFRESH_STATE["state"] == "running":
            return dict(_REFRESH_STATE)
        # Reset the counters for the new run.
        _REFRESH_STATE.update({
            "state": "running",
            "started_at": datetime.utcnow().isoformat(),
            "ended_at": None,
            "processed": 0, "total": 0,
            "real": 0, "synthetic": 0, "kept": 0,
            "current": None, "error": None, "languages_touched": [],
        })
    threading.Thread(target=_refresh_worker, daemon=True,
                     name="baseline-refresh").start()
    return get_refresh_progress()


def _refresh_worker() -> None:
    """Daemon entry-point — opens its own Session (the request's session
    closes when the response returns), runs the work, stamps the final
    state. Exceptions bubble up into `state='error'` with the message."""
    try:
        with Session(engine) as session:
            summary = _ensure_all_baselines(session, on_progress=_set_refresh)
        _set_refresh(
            state="done",
            ended_at=datetime.utcnow().isoformat(),
            real=summary["real"],
            synthetic=summary["synthetic"],
            kept=summary["kept"],
            languages_touched=summary["languages_touched"],
        )
    except Exception as e:                                                # noqa: BLE001
        _set_refresh(
            state="error",
            ended_at=datetime.utcnow().isoformat(),
            error=str(e),
        )


def _ensure_all_baselines(
    session: Session,
    *,
    on_progress: "callable | None" = None,
) -> dict:
    """Backfill: walk every (problem, language) pair that has at least one
    accepted Run across all non-deleted submissions and guarantee a baseline
    for each — real where an anchor + auth allow, synthetic placeholder
    otherwise. This is what `POST /api/admin/baselines/refresh` runs, so a
    single call cleans up pre-existing non-python pairs that were left
    "missing", even when LeetCode auth is unavailable.

    Returns a summary {real, synthetic, kept, languages_touched}.
    """
    # Collect candidate pairs from accepted runs (these are the pairs whose
    # coefficients actually matter — the leaderboard only uses accepted ones).
    #
    # Anchor-selection rule: prefer ANY accepted run with a stored external
    # submission_id over the most-recent-but-anchorless one. Walking newest
    # first and bailing on `key in pairs` used to leave (lang, problem)
    # pairs locked at `None` if the latest accepted run lacked an external
    # sid — even when an older accepted run carried one. That's exactly the
    # "synthetic stays forever" symptom for mixed-dispatch histories
    # (e.g. fake-mode runs interleaved with real-mode ones).
    pairs: dict[tuple[int, str], int | None] = {}   # (pid, lang) -> anchor sid
    runs = session.exec(
        select(Run)
        .where(Run.status_msg == "Accepted")
        .order_by(Run.created_at.desc())
    ).all()
    for r in runs:
        a = session.get(Attempt, r.attempt_id)
        if not a:
            continue
        s = session.get(Submission, a.submission_id)
        if not s or s.deleted_at is not None:
            continue
        key = (a.problem_id, s.language)
        # Already have a usable sid for this pair → done with it.
        if pairs.get(key) is not None:
            continue
        sid: int | None = None
        try:
            sid = int(r.submission_id_external) if r.submission_id_external else None
        except (TypeError, ValueError):
            sid = None
        # Update the map only when we either don't yet have an entry OR we
        # found a non-None sid (upgrading the previously-recorded None).
        if key not in pairs or sid is not None:
            pairs[key] = sid

    client = None
    if pairs:
        try:
            client = leetcode_dist.build_client()
        except leetcode_dist.AuthMissing as e:
            print(f"[baselines] auth missing — synthetic placeholders only: {e}",
                  file=sys.stderr)
    rate_sleep_s = float(os.environ.get("ARENA_BASELINE_REFRESH_RPS", "1.0"))
    tally = {"real": 0, "synthetic": 0, "kept": 0, "skip": 0}
    langs_touched: set[str] = set()
    total = len(pairs)
    if on_progress is not None:
        on_progress(total=total)
    try:
        for i, ((pid, lang), sid) in enumerate(pairs.items(), start=1):
            action = _ensure_baseline(session, pid, lang,
                                      anchor_sid=sid, client=client)
            tally[action] = tally.get(action, 0) + 1
            if action in ("real", "synthetic"):
                langs_touched.add(lang)
            if on_progress is not None:
                # Stream progress so the admin UI can show "320 / 700"
                # while the work runs — otherwise the only feedback was
                # a 12-minute browser hang.
                on_progress(
                    processed=i, total=total,
                    real=tally["real"], synthetic=tally["synthetic"],
                    kept=tally["kept"],
                    current=f"{lang}/{pid}",
                )
            if action == "real":
                time.sleep(rate_sleep_s)
        session.commit()
    finally:
        if client is not None:
            client.close()

    for lang in langs_touched:
        _recompute_human_coefficients(session, lang)

    state = session.get(BaselineRefreshState, 1) or BaselineRefreshState(id=1)
    state.last_refresh_at = datetime.utcnow()
    state.submissions_since_last = 0
    session.add(state); session.commit()

    return {
        "real": tally["real"], "synthetic": tally["synthetic"],
        "kept": tally["kept"], "languages_touched": sorted(langs_touched),
    }
