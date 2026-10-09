"""FastAPI app — LLM LeetCode Arena."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import json
import os
import sys

# Load .env BEFORE anything else reads os.environ. Two locations are tried,
# nearest first: webapp/backend/.env (for backend-only knobs like
# ARENA_DISPATCH_ENABLED) and repo-root/.env (for LeetCode auth cookies).
# Existing process env vars take precedence — `load_dotenv` doesn't
# overwrite them so a shell that already exports a value still wins.
from dotenv import load_dotenv
_BACKEND_ENV = Path(__file__).parent.parent / ".env"
_REPO_ENV = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(_BACKEND_ENV)
load_dotenv(_REPO_ENV)

# Repo-root data directory (<repo>/data) — holds the precomputed analysis
# artifacts (discrimination.json, etc.) shared with the CLI scripts.
REPO_ROOT_DATA = Path(__file__).parent.parent.parent.parent / "data"

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from .auth import (
    current_admin, current_user, generate_api_key,
    hash_api_key, looks_hashed, issue_session,
    hash_password, verify_password, maybe_user,
)
from . import captcha
from . import languages as langs_mod
from .db import engine, get_session, init_db
from .dispatch import (
    dispatch_submission,
    _recompute_human_coefficients,
    _ensure_all_baselines,
    get_refresh_progress, start_refresh_in_background,
)
from .ratelimit import enforce_submission_limit
from .models import (
    Agent,
    Attempt, Baseline, BaselineRefreshState, BlogPost,
    Competition, CompetitionProblem, CorrectnessOracle, Harness, Machine, Problem,
    PromptTemplate, RegisteredModel,
    Run, Snapshot,
    Submission, SubmissionStatus, TraceLogEntry,
    User, UserRole, UserStatus,
)
from . import provenance
from . import snapshot as snapshot_mod
from .validation import validate_code
from .schemas import (
    AgentDetail, AgentIn, AgentPublic, AgentRef,
    AnalysisPoint, AnalysisResponse, LangAnalysisRow,
    DiscriminationCrossRow, DiscriminationLangSummary, DiscriminationResponse,
    AppendAttemptsIn, AttemptIn, AttemptResult, AttemptRunDetail,
    BaselineBulkIn,
    CompareByLevelRow, CompareByTagRow,
    CompareModelOnProblem, CompareProblemRow, CompareResponse, CompareSummaryRow,
    CompareWinners,
    BlogPostCard, BlogPostIn, BlogPostPublic,
    ComparePromptRow, ComparePromptsResponse, ComparePromptsWinners,
    CompetitionDetail, CompetitionIn, CompetitionProblemsIn, CompetitionPublic,
    CompetitionRef,
    HarnessDetail, HarnessIn, HarnessPublic, HarnessRef, MachineIn, MachinePublic,
    CorrectnessOracleDetail, CorrectnessOracleIn, CorrectnessOraclePublic,
    CorrectnessOracleRef,
    CostEstimatorRange, CostEstimatorStats, CostEstimatorSubmission,
    LeetCodeAccountIn, LeetCodeAccountStatus, LeetCodeAccountsResponse,
    ModelDetail, ModelDetailLanguageStat, ModelListEntry,
    PromptTemplateIn, PromptTemplatePublic,
    RegisteredModelIn, RegisteredModelOverview, RegisteredModelPublic,
    RegisteredModelPricingPatch, RunErrorLog,
    ProblemDetail, ProblemPublic,
    LoginRequest, LoginResponse,
    ProblemTopSolution, ProgressEntry, RankingEntry, RegisterRequest, RunPublic,
    SubmissionIn, SubmissionProgress, SubmissionPublic, SubmitterDetail,
)


LEADERBOARD_MIN_PASS_RATE = float(
    os.environ.get("ARENA_LEADERBOARD_MIN_PASS_RATE", "0.25")
)
# Master switch for the eligibility gate. Default: OFF — every judged,
# non-deleted submission shows on the leaderboard so early / small datasets
# aren't hidden. Set ARENA_LEADERBOARD_GATE=on (or true/1/yes) to re-enable
# the strict completeness + > 25% pass-rate filters for a production launch.
LEADERBOARD_GATE_ON = os.environ.get(
    "ARENA_LEADERBOARD_GATE", "off"
).lower() in ("1", "on", "true", "yes")
# Window after which a queued-but-never-judged attempt counts as STALE
# (the dispatch worker started but never finished — likely a container
# bounce or a crashed worker thread). Stale attempts don't contribute
# toward "submission is complete" and don't show on the leaderboard;
# the sweep client re-attempts them on the next run.
# 30-minute default: long enough that a normal LeetCode roundtrip
# (judge + percentile fetch + INTER_DELAY pacing) never trips it,
# short enough that a real outage doesn't keep ghost attempts in the
# "covered" set forever.
ATTEMPT_STALE_AFTER_S = int(
    os.environ.get("ARENA_ATTEMPT_STALE_AFTER_S", "1800")
)
# How often the background stale-marker thread wakes up to scan for
# pending attempts that have been sitting too long. Quick enough that
# the user doesn't see "queued" rows lingering for an hour after a
# sweep crashed; slow enough that the scan's cost is amortised. Each
# wakeup is one indexed SELECT + at most one UPDATE.
STALE_MARKER_INTERVAL_S = int(
    os.environ.get("ARENA_STALE_MARKER_INTERVAL_S", "300")
)
# How long a phantom-failed attempt (n_runs=0 + dispatch_warning set,
# OR final_status NULL + dispatch_warning + n_runs=0) sticks around
# before the marker pass HARD DELETES it. Two-stage cleanup so the
# operator has a window to inspect the dispatch_warning text before
# it's gone, but the attempt eventually disappears from the SPA so
# /submissions/56 doesn't show 7 ghosts indefinitely.
# 6h default: well past the longest legitimate LC roundtrip while
# still short enough that a stale sweep clears within an afternoon.
ATTEMPT_GC_AFTER_S = int(
    os.environ.get("ARENA_ATTEMPT_GC_AFTER_S", "21600")
)
# Message stamped on attempts the marker decides are stuck. Distinct
# from the runtime auth/transient warnings the dispatcher writes so
# operators can grep for it later if the auto-fail behaviour ever
# misclassifies something.
_STALE_MARKER_REASON = (
    "Auto-marked dispatch error: this attempt was queued but never "
    "received a LeetCode verdict within "
    "ARENA_ATTEMPT_STALE_AFTER_S. Likely cause: the dispatch worker "
    "was killed mid-flight (container bounce, OOM, …) or LC took too "
    "long to respond. Re-run the sweep — the attempt is still pending "
    "server-side and will retry automatically."
)


def _mark_stale_pending_attempts() -> int:
    """Stamp `dispatch_warning` on every attempt that's been queued
    without any LeetCode contact for ARENA_ATTEMPT_STALE_AFTER_S.

    Selection: `final_status IS NULL` (never got a verdict) AND
    `dispatch_warning IS NULL` AND `prefailed_reason IS NULL` AND
    `logged_at < cutoff` (or NULL — we treat unset timestamps as ancient
    so they don't sit forever). Keying on `final_status` rather than the
    `n_runs` column also catches partially-dispatched attempts (n_runs>0
    but no verdict) that a process interruption left "queued" forever.

    Returns the number of rows updated. Runs as one indexed UPDATE so
    it's safe to invoke from a background thread without holding a
    write transaction for long. Idempotent: rows already marked are
    skipped via the `dispatch_warning IS NULL` filter."""
    from .db import engine
    from .models import Attempt
    cutoff = datetime.utcnow() - timedelta(seconds=ATTEMPT_STALE_AFTER_S)
    with Session(engine) as session:
        stale = session.exec(
            select(Attempt).where(
                # `final_status IS NULL` = never finalized (the true "no
                # verdict yet" signal). Keying on the n_runs COLUMN missed
                # attempts that landed a partial run (n_runs>0) but died
                # before the final aggregate, leaving them "queued" forever.
                Attempt.final_status.is_(None),
                Attempt.dispatch_warning.is_(None),
                Attempt.prefailed_reason.is_(None),
                # Either logged_at is in the past OR it's null (legacy
                # row whose timestamp never got backfilled).
                (Attempt.logged_at < cutoff) | (Attempt.logged_at.is_(None)),
            )
        ).all()
        if not stale:
            return 0
        for a in stale:
            a.dispatch_warning = _STALE_MARKER_REASON
            session.add(a)
        session.commit()
        return len(stale)


def _gc_old_phantom_attempts() -> int:
    """Hard-delete attempts that have been phantom-failed for too long.

    Selection: `n_runs == 0` AND `dispatch_warning IS NOT NULL` AND
    `logged_at < now - ATTEMPT_GC_AFTER_S`. We require BOTH the no-runs
    state AND a non-null dispatch_warning so a still-in-flight attempt
    is never deleted out from under the dispatcher — the warning is the
    explicit "this attempt is dead" marker.

    Why hard-delete: the SPA's status logic treats these as
    "Dispatch failed" rows (after the relabel above), but they
    accumulate on /submissions/<id> as scary-looking failure rows that
    don't reflect a real verdict. The sweep's `cell_covered` set
    already excludes phantom-failed rows from "covered" status — so the
    next sweep run retries the underlying problem regardless of
    whether the phantom row still exists. Deleting them just shrinks
    the SPA's per-submission table to the rows the operator actually
    cares about.

    Also wipes the orphan Run + TraceLogEntry rows that reference the
    deleted attempts so the transparency log doesn't keep ghost
    references. Returns the count of attempt rows deleted."""
    from .db import engine
    from .models import Attempt, Run
    from .models import TraceLogEntry         # transparency log
    cutoff = datetime.utcnow() - timedelta(seconds=ATTEMPT_GC_AFTER_S)
    with Session(engine) as session:
        victims = session.exec(
            select(Attempt).where(
                ((Attempt.n_runs == 0) | (Attempt.n_runs.is_(None))),
                Attempt.dispatch_warning.is_not(None),
                # `logged_at` is the only timestamp we control: it gets
                # set when the row is first inserted. Use it as the
                # age clock. NULL means a legacy row never had it
                # backfilled — treat that as ancient.
                (Attempt.logged_at < cutoff) | (Attempt.logged_at.is_(None)),
            )
        ).all()
        if not victims:
            return 0
        victim_ids = [a.id for a in victims]
        # Defence-in-depth: don't kill an attempt that somehow
        # accumulated Run rows since the SELECT (a slow dispatcher
        # could've raced with us). The `runs` check would already have
        # filtered it (n_runs > 0), but double-check by joining on the
        # actual Run table.
        with_real_runs = set(session.exec(
            select(Run.attempt_id).where(Run.attempt_id.in_(victim_ids))
        ).all())
        safe_ids = [vid for vid in victim_ids if vid not in with_real_runs]
        if not safe_ids:
            return 0
        # Cascade-delete the transparency-log rows first so we don't
        # leave orphan log entries pointing at a vanished attempt.
        session.exec(
            TraceLogEntry.__table__.delete().where(
                TraceLogEntry.attempt_id.in_(safe_ids)
            )
        )
        # Then the attempts themselves.
        session.exec(
            Attempt.__table__.delete().where(Attempt.id.in_(safe_ids))
        )
        session.commit()
        return len(safe_ids)


def _start_stale_marker_thread() -> None:
    """Background loop that periodically runs _mark_stale_pending_attempts
    AND _gc_old_phantom_attempts.

    The two passes are intentionally separate:
      * _mark_stale_pending_attempts is fast-path: turn a "queued but
        nothing's happening" row into a "dispatch failed" row so the
        SPA + sweep see it for what it is. Runs at the short interval.
      * _gc_old_phantom_attempts is slow-path: physically delete rows
        that have been in the dispatch-failed state for ATTEMPT_GC_AFTER_S
        so they stop cluttering /submissions/<id>. Runs at the same
        interval but only acts on rows that have aged past the GC
        threshold.

    Daemon thread so a Ctrl-C / SIGTERM doesn't block exit. Errors are
    swallowed-and-logged — we never want this watchdog to crash the
    backend, the worst case is a delayed marker pass."""
    import threading
    import time as _time

    def _loop() -> None:
        # First pass after a short warmup so the import-time logger is
        # ready and the connection pool has been pre-initialised.
        _time.sleep(30)
        while True:
            try:
                n = _mark_stale_pending_attempts()
                if n:
                    print(f"[stale-marker] auto-failed {n} stuck attempt(s) "
                          f"(>= {ATTEMPT_STALE_AFTER_S}s pending without runs)",
                          file=sys.stderr)
            except Exception as e:                                 # noqa: BLE001
                print(f"[stale-marker] pass failed: {e!r}",
                      file=sys.stderr)
            try:
                deleted = _gc_old_phantom_attempts()
                if deleted:
                    print(f"[stale-marker] GC: hard-deleted {deleted} "
                          f"phantom attempt(s) older than "
                          f"{ATTEMPT_GC_AFTER_S}s",
                          file=sys.stderr)
            except Exception as e:                                 # noqa: BLE001
                print(f"[stale-marker] GC pass failed: {e!r}",
                      file=sys.stderr)
            _time.sleep(STALE_MARKER_INTERVAL_S)

    t = threading.Thread(target=_loop, name="stale-marker", daemon=True)
    t.start()


def _model_pricing_lookup(session: Session) -> dict[tuple[str, str], tuple[float | None, float | None]]:
    """Build (model_name, model_version) → (input_$/1M, output_$/1M) map
    from the RegisteredModel table. Looked up once per request that needs
    cost recomputation — the table is small (≪ 1000 rows) so a scan is
    cheap. Each call is a single SELECT.

    Falling-back behaviour for missing rows: when a submission's
    (model_name, model_version) has no matching RegisteredModel row, OR
    the row has either price field null, the lookup returns (None, None)
    and `_recompute_attempt_cost` decides whether to show the snapshot.
    Pure read; safe to call from any request thread."""
    rows = session.exec(
        select(RegisteredModel).where(
            RegisteredModel.input_price_per_1m_usd.is_not(None),
            RegisteredModel.output_price_per_1m_usd.is_not(None),
        )
    ).all()
    out: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for rm in rows:
        out[(rm.name, rm.version)] = (
            rm.input_price_per_1m_usd, rm.output_price_per_1m_usd,
        )
    return out


def _recompute_attempt_cost(
    a: Attempt, model_name: str, model_version: str,
    pricing: dict[tuple[str, str], tuple[float | None, float | None]],
) -> float | None:
    """Live recomputation of `attempt.cost_usd` from the model's CURRENT
    per-1M-token rates × stored prompt+response tokens.

    Returns None when:
      * the (model_name, model_version) has no pricing row, OR
      * the attempt is missing prompt_tokens (a pre-feature attempt; only
        the snapshot cost_usd column is meaningful for it).

    When non-None, callers prefer this value over `attempt.cost_usd` for
    display, since the rates may have been edited after the attempt was
    submitted. Match key is (name, version) to support multiple rows per
    model name (e.g. different snapshots). Falls back to (name, *) by
    trying any pricing entry that shares the name when the exact
    version isn't pinned — common when the operator's only set rates
    once per model name."""
    rates = pricing.get((model_name, model_version))
    if rates is None:
        # Fall back to "any version for this name" — handles the common case
        # where the admin set rates on the canonical row and the submission
        # carries a date-pinned version string.
        for (n, _v), r in pricing.items():
            if n == model_name:
                rates = r
                break
    if rates is None:
        return None
    in_rate, out_rate = rates
    if in_rate is None or out_rate is None:
        return None
    pt = a.prompt_tokens
    rt = a.response_tokens
    if pt is None or rt is None:
        return None
    return (pt * in_rate + rt * out_rate) / 1_000_000.0


def _attempt_stats_by_sub(
    session: Session, sub_ids: list[int],
) -> dict[int, dict]:
    """One round-trip → per-submission attempt stats.

    Returns a dict of:
      {
        sub_id: {
          "problem_ids":          set,    # every problem the submission touched
          "judged_problem_ids":   set,    # only those with a real LC verdict
          "n_attempts":           int,    # everything in the submission
          "n_judged_attempts":    int,    # attempts with real verdicts only
        }
      }

    "Judged" = the attempt has at least one Run row AND no dispatch_warning
    AND isn't stale-pending. This is the set the leaderboard / completeness
    gate should use — counting auth-failed, transient-errored, or queued-
    forever attempts as "covered" would mislabel half-broken sweeps as
    complete and let them poison rankings.

    A "stale-pending" attempt is one with n_runs == 0, no dispatch_warning,
    and a logged_at older than ARENA_ATTEMPT_STALE_AFTER_S (default 30 min).
    These represent dispatches that the worker started but never completed
    — typically because the container was bounced or the worker thread
    crashed mid-flight. They're listed in `problem_ids` (visible in detail
    views) but excluded from `judged_problem_ids` (eligibility math).
    """
    if not sub_ids:
        return {}
    stale_cutoff = (datetime.utcnow()
                    - timedelta(seconds=ATTEMPT_STALE_AFTER_S))
    out: dict[int, dict] = {}
    attempts = session.exec(
        select(Attempt).where(Attempt.submission_id.in_(sub_ids))
    ).all()
    for a in attempts:
        entry = out.setdefault(a.submission_id, {
            "problem_ids": set(),
            "judged_problem_ids": set(),
            "n_attempts": 0,
            "n_judged_attempts": 0,
        })
        entry["problem_ids"].add(a.problem_id)
        entry["n_attempts"] += 1
        has_runs = (a.n_runs or 0) > 0
        has_warning = a.dispatch_warning is not None
        # An attempt is "judged" (counted toward submission completeness)
        # ONLY when it has runs AND no dispatch_warning. Stalled-pending
        # attempts (no runs, no warning, old) also fall out — we'd rather
        # require a retry than count a ghost as submitted.
        if has_runs and not has_warning:
            entry["judged_problem_ids"].add(a.problem_id)
            entry["n_judged_attempts"] += 1
    return out


def _attempt_stats_for(stats_map: dict[int, dict], sub_id: int) -> tuple[set[int], int]:
    """Returns (judged_problem_ids, n_attempts) — the FIRST element is the
    eligibility-relevant set (real verdicts only), the second is the total
    visible attempt count for display."""
    s = stats_map.get(sub_id) or {
        "problem_ids": set(), "judged_problem_ids": set(),
        "n_attempts": 0, "n_judged_attempts": 0,
    }
    # First slot was once `problem_ids` (all). It's now `judged_problem_ids`
    # so the eligibility caller short-circuits incomplete-by-failure subs.
    return s["judged_problem_ids"], s["n_attempts"]


def _build_universe_resolver(
    session: Session, subs: list[Submission],
) -> "callable[[Submission], set[int]]":
    """Bulk-precompute the per-submission "complete" universe so the
    eligibility loop doesn't N+1 query.

    Each submission's universe = the problem set of its competition (when
    competition_id is set) or the canonical originals fallback (legacy
    rows where competition_id is null — they were created before
    submissions were scoped to a single competition).

    Returns a closure callable: `resolve(sub) -> set[int]` that the
    caller invokes once per submission.
    """
    # 1. Canonical fallback set — built once, used for every legacy sub.
    canonical = {
        p.id for p in session.exec(
            select(Problem).where(Problem.original_problem_id.is_(None))
        ).all()
    }
    # 2. Per-competition memberships for every competition referenced by
    #    the submission list — single round-trip.
    comp_ids = {s.competition_id for s in subs if s.competition_id is not None}
    comp_universes: dict[int, set[int]] = {}
    if comp_ids:
        for cp in session.exec(
            select(CompetitionProblem).where(
                CompetitionProblem.competition_id.in_(comp_ids)
            )
        ).all():
            comp_universes.setdefault(cp.competition_id, set()).add(cp.problem_id)

    def _resolve(sub: Submission) -> set[int]:
        if sub.competition_id is None:
            return canonical
        return comp_universes.get(sub.competition_id, canonical)

    return _resolve


def _eligible_for_leaderboard(
    sub: Submission, attempted: set[int], all_problem_ids: set[int],
) -> bool:
    if not LEADERBOARD_GATE_ON:
        # Gate disabled — any judged submission shows on the leaderboard.
        return sub.status == SubmissionStatus.judged
    if sub.total_problems <= 0:
        return False
    if not all_problem_ids.issubset(attempted):
        return False
    return (sub.total_solved / sub.total_problems) > LEADERBOARD_MIN_PASS_RATE


def _leaderboard_reason(
    sub: Submission, attempted: set[int], all_problem_ids: set[int],
) -> str:
    """Human-readable verdict for why a submission does / doesn't qualify for
    the leaderboard (eligible / incomplete / pass-rate-too-low / not-judged)."""
    if sub.status != SubmissionStatus.judged:
        return "not-judged"
    if not LEADERBOARD_GATE_ON:
        return "eligible"
    if sub.total_problems <= 0 or not all_problem_ids.issubset(attempted):
        return "incomplete"
    if (sub.total_solved / sub.total_problems) <= LEADERBOARD_MIN_PASS_RATE:
        return "pass-rate-too-low"
    return "eligible"


def _submitter_label(user: User | None) -> str:
    if not user:
        return "anon"
    return user.org or user.display_name


def _slug(value: str) -> str:
    return "-".join(value.lower().split())


# SECURITY: hide the OpenAPI schema and interactive docs unless explicitly
# enabled (ARENA_ENABLE_DOCS=1). They disclose the full API surface, so they
# default off in any deployment; a dev enables them locally when needed.
_enable_docs = os.environ.get("ARENA_ENABLE_DOCS", "").strip().lower() in ("1", "true", "yes")
# Single source of truth for the platform version (semver): the VERSION file at
# the webapp root. A release bumps it once; the API, the frontend footer, and
# measurement provenance report the same string, so a stale deploy is a
# one-glance diagnosis.
def _read_version() -> str:
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION")
    try:
        with open(path) as f:
            return f.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = _read_version()

app = FastAPI(
    title="PerfArena", version=APP_VERSION,
    docs_url="/docs" if _enable_docs else None,
    redoc_url="/redoc" if _enable_docs else None,
    openapi_url="/openapi.json" if _enable_docs else None,
)


@app.get("/api/version")
def api_version():
    """Deployed platform version (semver), so a client can tell which code is
    live and spot a stale frontend or backend at a glance."""
    return {"version": APP_VERSION}
# CORS origins are config-driven (comma-separated ARENA_CORS_ORIGINS); the
# default is the local Vite dev server. Production is served same-origin, so it
# needs no cross-origin allowance.
_cors_origins = [
    o.strip() for o in
    os.environ.get("ARENA_CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"], allow_headers=["*"],
)


from . import ip_ratelimit                                              # noqa: E402


@app.middleware("http")
async def _ip_rate_limit_middleware(request, call_next):
    """Per-IP GET ceiling — defence-in-depth against scrapers/storms after
    the curated-platform pivot opened every read to the community. The
    actual policy lives in `app.ip_ratelimit`; the middleware just routes
    every HTTP hit through `check_get_request` and turns its 429 into a
    JSON response with the Retry-After header set."""
    from fastapi.responses import JSONResponse
    try:
        ip_ratelimit.check_get_request(request)
    except HTTPException as e:
        return JSONResponse(
            status_code=e.status_code,
            content={"detail": e.detail},
            headers=e.headers or {},
        )
    return await call_next(request)


# --- crash diagnostics: let the backend self-report why it died -------------
# The prod box has no easy log access, and an OOM or hard fault leaves nothing
# in `docker logs`. Two mechanisms: (a) persist Python-level crashes to a file
# on the DB volume so they survive a restart, and (b) expose live RSS so an
# operator can watch memory climb toward an OOM kill through the admin API.
_PROCESS_START = datetime.now()


def _crash_log_path() -> Path:
    from .db import DB_PATH
    return DB_PATH.parent / "crash.log"


def _append_crash(kind: str, text: str) -> None:
    try:
        p = _crash_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(f"\n===== {datetime.now().isoformat()} [{kind}] =====\n{text}\n")
    except Exception:                                                  # noqa: BLE001
        pass


def _install_crash_diagnostics() -> None:
    """Route uncaught exceptions (worker threads, main thread, asyncio tasks)
    and hard faults to the crash log. OOM via SIGKILL cannot be caught here, so
    for that, watch rss_mb from GET /api/admin/diagnostics."""
    import faulthandler
    import threading
    import traceback
    try:
        faulthandler.enable(file=_crash_log_path().open("a"), all_threads=True)
    except Exception:                                                  # noqa: BLE001
        pass

    def _thread_hook(args) -> None:
        name = args.thread.name if args.thread else "?"
        _append_crash(f"thread:{name}", "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)))
    threading.excepthook = _thread_hook

    _prev_hook = sys.excepthook

    def _sys_hook(et, ev, tb) -> None:
        _append_crash("main", "".join(traceback.format_exception(et, ev, tb)))
        _prev_hook(et, ev, tb)
    sys.excepthook = _sys_hook

    def _aio_hook(loop, ctx) -> None:
        exc = ctx.get("exception")
        detail = "".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__)) if exc else ""
        _append_crash("asyncio", f"{ctx.get('message', '')}\n{detail}")
    try:
        import asyncio
        asyncio.get_running_loop().set_exception_handler(_aio_hook)
    except Exception:                                                  # noqa: BLE001
        pass


def _rss_mb() -> "float | None":
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:                                                  # noqa: BLE001
        pass
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss is kB on Linux, bytes on macOS.
        return round(ru / 1024, 1) if sys.platform.startswith("linux") \
            else round(ru / 1048576, 1)
    except Exception:                                                  # noqa: BLE001
        return None


@app.get("/api/admin/diagnostics")
def admin_diagnostics(_: User = Depends(current_admin)):
    """Remote crash and memory diagnostics for a box with no log access.
    Poll rss_mb while reproducing a crash: a steady climb that then vanishes
    (endpoint unreachable) means an OOM kill. crash_log_tail carries any
    Python-level traceback persisted across the restart."""
    import threading as _th
    tail = ""
    try:
        p = _crash_log_path()
        if p.exists():
            tail = p.read_text()[-8000:]
    except Exception:                                                  # noqa: BLE001
        pass
    return {
        "uptime_s": round((datetime.now() - _PROCESS_START).total_seconds(), 1),
        "rss_mb": _rss_mb(),
        "num_threads": _th.active_count(),
        "python": sys.version.split()[0],
        "crash_log_tail": tail,
    }


def _migrate_api_keys_to_hashed() -> None:
    """One-time, idempotent: replace any plaintext API key in the DB with its
    hash. Presenting the original key still authenticates (the token is hashed
    at lookup), so this does not lock anyone out. Safe to run on every boot."""
    from sqlmodel import Session
    with Session(engine) as s:
        n = 0
        for u in s.exec(select(User).where(User.api_key.isnot(None))).all():
            if u.api_key and not looks_hashed(u.api_key):
                u.api_key = hash_api_key(u.api_key)
                s.add(u); n += 1
        if n:
            s.commit()
            print(f"[migrate] hashed {n} plaintext API key(s) at rest",
                  file=sys.stderr)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    _migrate_api_keys_to_hashed()
    _install_crash_diagnostics()
    # WAL self-heal: if a prior boot was killed mid-transaction (OOM, kill -9,
    # docker stop -t 0, …) the WAL can be left holding tens of MB of
    # uncheckpointed frames. SQLite's automatic checkpoint kicks in on each
    # write at a fairly large threshold, so a wedged dispatcher can grow
    # the WAL well past 25 MB before normal traffic claws it back. Running
    # a one-shot TRUNCATE checkpoint here is cheap when the WAL is small
    # and reclaims the disk + clears any residual lock contention on the
    # first writer of this boot. Failures are non-fatal — every error
    # path here would just leave us back where we'd be without the
    # call, so we swallow + log instead of blocking startup.
    try:
        import sqlite3
        from .db import DB_PATH
        con = sqlite3.connect(str(DB_PATH), timeout=30)
        try:
            res = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            # res = (busy, log_frames, checkpointed). busy=0 + log_frames=0
            # is the steady-state happy path; non-zero log_frames means we
            # actually reclaimed something and is worth logging once.
            busy, frames, ckpted = res or (0, 0, 0)
            if frames or busy:
                print(f"[startup] WAL checkpoint: busy={busy} "
                      f"frames={frames} checkpointed={ckpted}",
                      file=sys.stderr)
        finally:
            con.close()
    except Exception as e:                                         # noqa: BLE001
        print(f"[startup] WAL checkpoint failed (non-fatal): {e!r}",
              file=sys.stderr)
    _seed_default_prompts()
    _seed_default_competitions()
    # Backfill RegisteredModel stubs for every (name, version) the platform
    # has ever seen in a Submission. Auto-stub-on-submission only fires for
    # NEW submissions, so anything that landed before that feature shipped
    # (e.g. the existing qwen runs) needs a one-time sweep to surface them
    # on /models. Idempotent.
    _backfill_registered_model_stubs_from_submissions()
    # First-boot admin bootstrap from env vars. Idempotent.
    _seed_bootstrap_admin()
    # One eager pass FIRST — handles any attempts that were already stale
    # from a prior boot (e.g. the container restarted while a sweep was
    # in flight). Then start the periodic loop so anything that goes
    # stale during this boot also gets cleaned up.
    try:
        n = _mark_stale_pending_attempts()
        if n:
            print(f"[startup] auto-failed {n} stale attempt(s) from prior boot",
                  file=sys.stderr)
    except Exception as e:                                         # noqa: BLE001
        print(f"[startup] stale-marker eager pass failed: {e!r}",
              file=sys.stderr)
    # Re-dispatch attempts that look in-flight but young (< ATTEMPT_STALE_
    # AFTER_S). These are the most common loss class: a BackgroundTasks
    # job got launched by the request handler and then the container
    # bounced (deploy, OOM, ^C) before the job could write Run rows. The
    # stale-marker would eventually catch them but only after 30+ min;
    # this recovery pass cuts that to "as fast as the dispatcher can
    # re-pickup". No-op when nothing's in flight.
    try:
        n = _recover_in_flight_dispatches_on_startup()
        if n:
            print(f"[startup] re-dispatched {n} in-flight submission(s) "
                  f"from prior boot", file=sys.stderr)
    except Exception as e:                                         # noqa: BLE001
        print(f"[startup] in-flight recovery failed: {e!r}",
              file=sys.stderr)
    _start_stale_marker_thread()


def _recover_in_flight_dispatches_on_startup() -> int:
    """Re-fire the BG dispatcher for any submission whose attempts look
    like they were mid-dispatch when the process last died.

    Selection: distinct submission_ids whose Attempt rows have
    `n_runs == 0` AND `dispatch_warning IS NULL` AND
    `prefailed_reason IS NULL` AND `logged_at` younger than
    ATTEMPT_STALE_AFTER_S. Older rows are the stale-marker's job, so we
    don't fight it; rows with a dispatch_warning are the GC's job.

    We re-dispatch the WHOLE submission rather than just the orphan
    attempts because `dispatch_submission` already knows how to skip
    attempts that have completed Run rows and only act on the queued
    ones. Threaded so a slow LC roundtrip doesn't block startup.
    Returns the count of submissions kicked.
    """
    import threading
    from .db import engine
    from .models import Attempt, Submission
    from .dispatch import dispatch_submission
    cutoff = datetime.utcnow() - timedelta(seconds=ATTEMPT_STALE_AFTER_S)
    with Session(engine) as session:
        rows = session.exec(
            select(Attempt.submission_id).where(
                ((Attempt.n_runs == 0) | (Attempt.n_runs.is_(None))),
                Attempt.dispatch_warning.is_(None),
                Attempt.prefailed_reason.is_(None),
                Attempt.logged_at >= cutoff,
            ).distinct()
        ).all()
        # Filter out deleted submissions — they're being purged elsewhere.
        sub_ids: list[int] = []
        for sid in rows:
            sub = session.get(Submission, sid)
            if sub is not None and sub.deleted_at is None:
                sub_ids.append(sid)
    if not sub_ids:
        return 0
    # Fire each dispatch on its own thread so startup isn't blocked. Each
    # one will obey the same per-account pacing the foreground path uses.
    for sid in sub_ids:
        threading.Thread(
            target=dispatch_submission, args=(sid,),
            name=f"recover-dispatch-{sid}", daemon=True,
        ).start()
    return len(sub_ids)


def _seed_bootstrap_admin() -> None:
    """First-boot admin bootstrap so a fresh container is reachable as
    admin without docker-exec'ing into it.

    Two paths:

    * OPT-IN (operator sets `ARENA_BOOTSTRAP_ADMIN_EMAIL`): create/reuse
      the named admin using the caller-provided credentials. Honoured
      env vars:
        ARENA_BOOTSTRAP_ADMIN_EMAIL     (required to trigger the opt-in path)
        ARENA_BOOTSTRAP_ADMIN_NAME      default "Admin"
        ARENA_BOOTSTRAP_ADMIN_API_KEY   default = freshly generated
        ARENA_BOOTSTRAP_ADMIN_PASSWORD  default = unset (API-key only login)

    * SAFETY NET (nothing set + no admin exists yet): auto-provision a
      default admin (`admin@perfarena.dev`) with a freshly-generated
      API key. Without this, a fresh container ships with zero admin
      accounts and the operator has no way to hit /api/admin/* endpoints
      — a footgun disguised as security. The generated key is printed to
      stderr so `docker logs` recovers it, and the whole path is skipped
      the moment ANY admin already exists (so restarts against a
      persistent volume never regenerate keys).

    Set `ARENA_BOOTSTRAP_DISABLE_AUTO_ADMIN=1` to disable the safety net
    for hardened deployments that provision users out-of-band and want
    zero surprises.

    Idempotent throughout: an existing admin is never touched — we don't
    downgrade, rotate keys, or overwrite passwords."""
    from sqlmodel import Session                                          # noqa: E402
    email = os.environ.get("ARENA_BOOTSTRAP_ADMIN_EMAIL")
    with Session(engine) as s:
        if email:
            # Opt-in path — precise: only touch the named row.
            if s.exec(select(User).where(User.email == email)).first():
                return
            api_key = (
                os.environ.get("ARENA_BOOTSTRAP_ADMIN_API_KEY")
                or generate_api_key()
            )
            password = os.environ.get("ARENA_BOOTSTRAP_ADMIN_PASSWORD")
            u = User(
                email=email,
                display_name=os.environ.get("ARENA_BOOTSTRAP_ADMIN_NAME", "Admin"),
                role=UserRole.admin,
                status=UserStatus.approved,
                api_key=hash_api_key(api_key),
                password_hash=hash_password(password) if password else None,
            )
            s.add(u); s.commit()
            print(f"[bootstrap] created admin user {email!r}", file=sys.stderr)
            if not os.environ.get("ARENA_BOOTSTRAP_ADMIN_API_KEY"):
                print(f"[bootstrap]   api_key = {api_key}", file=sys.stderr)
            if password:
                print(f"[bootstrap]   password login enabled (use /login)",
                      file=sys.stderr)
            return

        # Safety-net path — only runs when NO admin exists at all AND the
        # operator hasn't disabled it. Prevents fresh containers from
        # coming up locked out of their own admin surface.
        if os.environ.get("ARENA_BOOTSTRAP_DISABLE_AUTO_ADMIN", "").lower() \
                in ("1", "true", "yes", "on"):
            return
        if s.exec(select(User).where(User.role == UserRole.admin)).first():
            return
        default_email = "admin@perfarena.dev"
        api_key = generate_api_key()
        u = User(
            email=default_email,
            display_name="Admin",
            role=UserRole.admin,
            status=UserStatus.approved,
            api_key=hash_api_key(api_key),
        )
        s.add(u); s.commit()
        print(
            "[bootstrap] no admin user found — auto-provisioned one. "
            "Set ARENA_BOOTSTRAP_ADMIN_EMAIL to pin the identity, or "
            "ARENA_BOOTSTRAP_DISABLE_AUTO_ADMIN=1 to suppress this "
            "fallback entirely.",
            file=sys.stderr,
        )
        print(f"[bootstrap]   email   = {default_email}", file=sys.stderr)
        print(f"[bootstrap]   api_key = {api_key}", file=sys.stderr)
        print(
            "[bootstrap]   USE IT NOW: `docker logs <container>` will not "
            "reprint the key on future restarts.",
            file=sys.stderr,
        )


_DEFAULT_PROMPTS: list[dict] = [
    {
        "name": "standard-code-only",
        "system_prompt": (
            "You are a competitive-programming assistant. Solve the "
            "problem in {{language_display}} using LeetCode's standard "
            "`Solution` shape (the exact class and method names LeetCode "
            "expects). Think step by step, then return the final solution "
            "in a single ```{{language_fence}} code block. Don't include "
            "any other comments or text outside the code block."
        ),
        "user_prompt_template": (
            "Solve this LeetCode problem `{{slug}}` in "
            "{{language_display}}.\n\n{{statement}}"
        ),
        "language_hint": None,
        "notes": (
            "The original prompt used by submit_with_local_model.py. "
            "Targeted at single-shot pass@1 — no chain-of-thought leaked "
            "into the final answer."
        ),
    },
    {
        "name": "explain-then-code",
        "system_prompt": (
            "You are a thoughtful programming tutor. First explain the "
            "approach in 2-3 short sentences, then write the solution in "
            "{{language_display}} as a single ```{{language_fence}} code "
            "block following LeetCode's `Solution` shape. Keep the "
            "explanation outside the code block; nothing else after the "
            "code block."
        ),
        "user_prompt_template": (
            "Problem `{{slug}}`:\n\n{{statement}}\n\n"
            "Write a clean {{language_display}} solution."
        ),
        "language_hint": None,
        "notes": (
            "Encourages a short natural-language rationale before the "
            "code. Useful for measuring how reasoning affects pass@1."
        ),
    },
    {
        # Unified template used by webapp/examples/iterative_refine.py.
        # The placeholders {{optimization_directive}}, {{round_instruction}},
        # {{hints_block}}, {{rounds_history}} are filled by the client before
        # send-time. Empty strings render as empty lines (which the model
        # ignores), so a single template covers every combination of
        # (n_rounds, include_hints, optimization_target).
        "name": "experiment-system",
        "system_prompt": (
            "You are a competitive-programming assistant. Solve the problem "
            "in {{language_display}} using LeetCode's standard `Solution` "
            "shape (the exact class and method names LeetCode expects).\n\n"
            "{{optimization_directive}}\n\n"
            "{{round_instruction}}\n\n"
            "Think step by step, then return the final solution in a single "
            "```{{language_fence}} code block. Don't include any other "
            "comments or text outside the code block."
        ),
        "user_prompt_template": (
            "Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n"
            "{{statement}}\n\n"
            "{{hints_block}}\n\n"
            "{{rounds_history}}"
        ),
        "language_hint": None,
        "notes": (
            "Unified template for the iterative-refinement client driver "
            "(iterative_refine.py). The four bracketed placeholders are "
            "expanded client-side based on Submission.n_rounds, "
            "Submission.include_hints, Submission.optimization_target, "
            "and the prior round's judge feedback."
        ),
    },
    # ---- Iterative-refinement family ---------------------------------------
    #
    # Four prompts that EXPLICITLY foreground the "try, fail, try again"
    # contract in the system prompt — rather than the experiment-system
    # pair, where iterative behaviour lives in optional placeholders that
    # may render empty for n_rounds=1 runs.
    #
    # The 2×2 design is an ablation grid:
    #
    #                | no shape           | with shape
    #   ------------ | ------------------ | -----------------------------
    #   no history   | iterative-refine   | iterative-refine-with-shape
    #   with history | iterative-refine-  | iterative-refine-with-history-
    #                |   with-history     |   and-shape
    #
    # The "no history" pair tells the model it has N rounds available but
    # withholds the prior round's code + judge feedback — testing whether
    # bare round-awareness changes behaviour (some models reason about the
    # remaining budget; others ignore it). The "with history" pair feeds
    # the prior round's code + LC verdict in, the classic agentic loop.
    # The "shape" axis adds the mandatory class/method signature, same
    # discipline as experiment-system-with-shape.
    #
    # iterative_refine.py uses any of these — it sets {{round_instruction}}
    # from the round counter, {{rounds_history}} from prior-attempt verdicts,
    # and {{code_template}} from the language-specific starter snippet.
    {
        "name": "iterative-refine",
        "system_prompt": (
            "You are a competitive-programming assistant doing ITERATIVE "
            "REFINEMENT. You have up to a small number of attempts at this "
            "problem in {{language_display}}, and after each failed attempt "
            "you will be asked to try again. Your goal is to produce an "
            "accepted solution as early as possible, but if your first try "
            "is wrong you will get another chance to fix it.\n\n"
            "Use LeetCode's standard `Solution` shape (the exact class and "
            "method names LeetCode expects).\n\n"
            "{{optimization_directive}}\n\n"
            "{{round_instruction}}\n\n"
            "Think step by step, then return the final solution in a single "
            "```{{language_fence}} code block. Don't include any other "
            "comments or text outside the code block."
        ),
        "user_prompt_template": (
            "Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n"
            "{{statement}}\n\n"
            "{{hints_block}}"
        ),
        "language_hint": None,
        "notes": (
            "Iterative refinement — round awareness without history. The "
            "system prompt makes it explicit that the model has multiple "
            "attempts, but the prior round's code + judge feedback are NOT "
            "fed back in. Tests whether bare round-awareness changes "
            "behaviour. Counterpart of iterative-refine-with-history."
        ),
    },
    {
        "name": "iterative-refine-with-history",
        "system_prompt": (
            "You are a competitive-programming assistant doing ITERATIVE "
            "REFINEMENT. You have up to a small number of attempts at this "
            "problem in {{language_display}}. After each failed attempt you "
            "will see WHAT YOU TRIED AND HOW IT FAILED (the code, the "
            "verdict from LeetCode, the failing test case if there was one). "
            "Use that feedback: read the failure, find the bug, fix it. Do "
            "not silently re-submit the same approach.\n\n"
            "Use LeetCode's standard `Solution` shape (the exact class and "
            "method names LeetCode expects).\n\n"
            "{{optimization_directive}}\n\n"
            "{{round_instruction}}\n\n"
            "Think step by step about what went wrong (if anything went "
            "wrong), then return the final solution in a single "
            "```{{language_fence}} code block. Don't include any other "
            "comments or text outside the code block."
        ),
        "user_prompt_template": (
            "Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n"
            "{{statement}}\n\n"
            "{{hints_block}}\n\n"
            "{{rounds_history}}"
        ),
        "language_hint": None,
        "notes": (
            "Iterative refinement WITH feedback. Each round sees the prior "
            "round's code + LC verdict via {{rounds_history}}. This is the "
            "classic agentic-debugging loop — measures the model's ability "
            "to read a failure and fix it, not just sample diversity."
        ),
    },
    {
        "name": "iterative-refine-with-shape",
        "system_prompt": (
            "You are a competitive-programming assistant doing ITERATIVE "
            "REFINEMENT. You have up to a small number of attempts at this "
            "problem in {{language_display}}, and after each failed attempt "
            "you will be asked to try again. Your goal is to produce an "
            "accepted solution as early as possible.\n\n"
            "MANDATORY SHAPE: your final code MUST match the class and method "
            "signature shown in the starter snippet in the user prompt. "
            "Don't rename the method, don't change argument names or types, "
            "don't wrap the Solution class in another class. Shape errors "
            "waste rounds — keep the shape stable, change only the body.\n\n"
            "{{optimization_directive}}\n\n"
            "{{round_instruction}}\n\n"
            "Think step by step, then return the final solution in a single "
            "```{{language_fence}} code block. Don't include any other "
            "comments or text outside the code block."
        ),
        "user_prompt_template": (
            "Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n"
            "{{statement}}\n\n"
            "{{hints_block}}\n\n"
            "STARTER CODE (your solution must conform to this exact shape — "
            "implement the method body):\n"
            "```{{language_fence}}\n{{code_template}}\n```"
        ),
        "language_hint": None,
        "notes": (
            "Iterative refinement WITH mandatory shape, NO failure history. "
            "Ablation pair: isolates the effect of the shape discipline "
            "from the effect of feedback. Requires "
            "`python -m app.fetch_code_snippets` to have populated the "
            "language-specific starter snippets on Problem.code_snippets."
        ),
    },
    {
        "name": "iterative-refine-with-history-and-shape",
        "system_prompt": (
            "You are a competitive-programming assistant doing ITERATIVE "
            "REFINEMENT. You have up to a small number of attempts at this "
            "problem in {{language_display}}. After each failed attempt you "
            "will see WHAT YOU TRIED AND HOW IT FAILED (the code, the "
            "verdict from LeetCode, the failing test case if there was one). "
            "Use that feedback: read the failure, find the bug, fix it. Do "
            "not silently re-submit the same approach.\n\n"
            "MANDATORY SHAPE: your final code MUST match the class and method "
            "signature shown in the starter snippet in the user prompt. "
            "Don't rename the method, don't change argument names or types, "
            "don't wrap the Solution class in another class. Shape errors "
            "waste rounds — keep the shape stable across rounds, change "
            "only the body.\n\n"
            "{{optimization_directive}}\n\n"
            "{{round_instruction}}\n\n"
            "Think step by step about what went wrong (if anything went "
            "wrong), then return the final solution in a single "
            "```{{language_fence}} code block. Don't include any other "
            "comments or text outside the code block."
        ),
        "user_prompt_template": (
            "Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n"
            "{{statement}}\n\n"
            "{{hints_block}}\n\n"
            "STARTER CODE (your solution must conform to this exact shape — "
            "implement the method body):\n"
            "```{{language_fence}}\n{{code_template}}\n```\n\n"
            "{{rounds_history}}"
        ),
        "language_hint": None,
        "notes": (
            "Iterative refinement with EVERYTHING ON: failure history fed "
            "back AND mandatory shape discipline. The strongest configuration "
            "of the iterative family — closest to a production agentic "
            "coder loop. Ablation-grid counterpart: iterative-refine."
        ),
    },
    {
        # Same as `experiment-system` PLUS a "must match this code shape"
        # discipline + an extra {{code_template}} placeholder the client
        # fills with the language-specific starter snippet from LeetCode
        # (Problem.code_snippets[<language>]).
        #
        # Purpose: ensures the model returns code conforming to the EXACT
        # class + method signature LeetCode expects, eliminating "method not
        # found" judge errors that have nothing to do with algorithmic skill.
        "name": "experiment-system-with-shape",
        "system_prompt": (
            "You are a competitive-programming assistant. Solve the problem "
            "in {{language_display}}.\n\n"
            "MANDATORY SHAPE: your final code MUST match the class and method "
            "signature shown in the starter snippet below. Don't rename the "
            "method, don't change argument names or types, don't wrap the "
            "Solution class in another class.\n\n"
            "{{optimization_directive}}\n\n"
            "{{round_instruction}}\n\n"
            "Think step by step, then return the final solution in a single "
            "```{{language_fence}} code block. Don't include any other "
            "comments or text outside the code block."
        ),
        "user_prompt_template": (
            "Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n"
            "{{statement}}\n\n"
            "{{hints_block}}\n\n"
            "STARTER CODE (your solution must conform to this exact shape — "
            "implement the method body):\n"
            "```{{language_fence}}\n{{code_template}}\n```\n\n"
            "{{rounds_history}}"
        ),
        "language_hint": None,
        "notes": (
            "Same as experiment-system, but adds a MANDATORY SHAPE directive "
            "in the system prompt and a {{code_template}} placeholder in the "
            "user prompt — the client expands it with the language-specific "
            "starter snippet from Problem.code_snippets. Use when comparing "
            "with/without-shape ablations; pre-requires "
            "`app.fetch_code_snippets`."
        ),
    },
    {
        "name": "experiment-system-with-shape-and-flags",
        "system_prompt": (
            "You are a competitive-programming assistant. Solve the problem "
            "in {{language_display}}.\n\n"
            "MANDATORY SHAPE: your final code MUST match the class and method "
            "signature shown in the starter snippet below. Don't rename the "
            "method, don't change argument names or types, don't wrap the "
            "Solution class in another class.\n\n"
            "{{optimization_directive}}\n\n"
            "{{round_instruction}}\n\n"
            "Think step by step, then return the final solution in a single "
            "```{{language_fence}} code block. Don't include any other "
            "comments or text outside the code block.\n\n"
            "COMPILATION FLAGS: after the code block, on its own final line, "
            "output the exact compiler flags you want used to build your "
            "solution, in this format (nothing else on the line):\n"
            "PERFARENA-FLAGS: <flags>\n"
            "Give raw flags valid for {{language_display}}'s compiler and "
            "include any language essentials (e.g. the standard version). "
            "Example for C++: `PERFARENA-FLAGS: -O3 -march=native -std=c++17`. "
            "If the language is interpreted (no compiler), output "
            "`PERFARENA-FLAGS:` with nothing after it."
        ),
        "user_prompt_template": (
            "Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n"
            "{{statement}}\n\n"
            "{{hints_block}}\n\n"
            "STARTER CODE (your solution must conform to this exact shape — "
            "implement the method body):\n"
            "```{{language_fence}}\n{{code_template}}\n```\n\n"
            "{{rounds_history}}"
        ),
        "language_hint": None,
        "notes": (
            "experiment-system-with-shape PLUS a directive for the model to "
            "choose its own compilation flags, emitted as a `PERFARENA-FLAGS: "
            "<flags>` marker line the client parses (extract_compile_flags). "
            "The chosen flags become the run's compile_flags (per-attempt, "
            "recorded in provenance / MeasurementProfile); invalid flags fall "
            "back to the language default on the runner and are flagged."
        ),
    },
]


def _seed_default_competitions() -> None:
    """Make sure the canonical LeetCode-99 competition exists. Idempotent and
    additive: if the slug is already in the table we don't touch it (so any
    admin edits stick); if missing we create it AND link every current Problem
    row to it. New Problem rows added later land outside the competition until
    an admin adds them via PUT /api/admin/competitions/{id}/problems."""
    from sqlmodel import Session
    with Session(engine) as session:
        existing = session.exec(
            select(Competition).where(Competition.slug == "leetcode-99-perf")
        ).first()
        if existing is not None:
            return
        comp = Competition(
            slug="leetcode-99-perf",
            name="PerfArena's 99 LeetCode problems",
            description=(
                "The curated set this platform was built around: the 99 "
                "longest-runtime, discriminative-distribution LeetCode "
                "problems (33 per difficulty) selected via the mode-share and "
                "runtime-spread filters documented in `docs/problem-selection.md`."
            ),
            kind="leetcode",
            status="active",
        )
        session.add(comp); session.commit(); session.refresh(comp)
        for p in session.exec(select(Problem)).all():
            session.add(CompetitionProblem(competition_id=comp.id, problem_id=p.id))
        session.commit()


def _seed_default_prompts() -> None:
    """Insert any default prompt that isn't already in the table, keyed by
    name. Idempotent and additive: editing or deleting a seeded prompt is
    safe (it won't reappear on next boot if its name is still in the DB);
    adding a new entry to _DEFAULT_PROMPTS gets it picked up automatically
    on the next start without re-seeding the ones already present."""
    from sqlmodel import Session
    with Session(engine) as session:
        existing = {p.name for p in session.exec(select(PromptTemplate)).all()}
        added = 0
        for spec in _DEFAULT_PROMPTS:
            if spec["name"] in existing:
                continue
            session.add(PromptTemplate(**spec))
            added += 1
        if added:
            session.commit()


# ---------- Public ----------------------------------------------------------

@app.get("/api/problems", response_model=list[ProblemPublic])
def list_problems(
    level: str | None = None,
    tag: str | None = None,
    competition: str | None = None,
    session: Session = Depends(get_session),
):
    """List problems with their competition memberships attached.

    Filters:
      * `level=Easy|Medium|Hard`  — single-difficulty filter.
      * `tag=substring`           — case-insensitive substring match on tags.
      * `competition=<slug>`      — restrict to problems whose
                                    CompetitionProblem join references this slug.
    """
    # Competition filter is structural — resolve it to a problem-id set first
    # so the main query can intersect with it cheaply.
    comp_problem_ids: set[int] | None = None
    if competition:
        comp = session.exec(
            select(Competition).where(Competition.slug == competition)
        ).first()
        if comp is None:
            raise HTTPException(404, f"competition '{competition}' not found")
        comp_problem_ids = {
            cp.problem_id for cp in session.exec(
                select(CompetitionProblem).where(
                    CompetitionProblem.competition_id == comp.id
                )
            ).all()
        }
        if not comp_problem_ids:
            return []

    q = select(Problem)
    if level:
        q = q.where(Problem.level == level)
    if comp_problem_ids is not None:
        q = q.where(Problem.id.in_(comp_problem_ids))
    rows = session.exec(q).all()
    if tag:
        rows = [r for r in rows if tag.lower() in r.tags.lower()]

    # Bulk-load every (problem_id → [CompetitionRef, …]) mapping in one
    # round-trip so the response includes membership without N+1 queries.
    # We only need slug+name per link → cheap.
    problem_ids = [r.id for r in rows]
    memberships: dict[int, list[CompetitionRef]] = {pid: [] for pid in problem_ids}
    if problem_ids:
        link_rows = session.exec(
            select(CompetitionProblem, Competition)
            .where(CompetitionProblem.competition_id == Competition.id)
            .where(CompetitionProblem.problem_id.in_(problem_ids))
        ).all()
        for cp, comp in link_rows:
            memberships.setdefault(cp.problem_id, []).append(
                CompetitionRef(slug=comp.slug, name=comp.name)
            )
        # Stable ordering: by competition name. Keeps the badge order
        # deterministic across requests for the same problem.
        for pid in memberships:
            memberships[pid].sort(key=lambda c: c.name.lower())

    return [
        ProblemPublic(
            id=r.id, title_slug=r.title_slug, title=r.title,
            level=r.level, tags=[t.strip() for t in r.tags.split(",") if t.strip()],
            competitions=memberships.get(r.id, []),
        )
        for r in rows
    ]


@app.get("/api/rankings", response_model=list[RankingEntry])
def rankings(
    language: str | None = None,
    level: str | None = None,
    k: int | None = Query(None, ge=1, le=200,
                          description="filter by n_samples_per_problem"),
    prompt_id: int | None = Query(None, description="restrict to one PromptTemplate id"),
    competition: str | None = Query(
        None,
        description="restrict the leaderboard to submissions touching the "
                    "problems in this competition (slug). For the default "
                    "'leetcode-99-perf' that contains every problem, this is "
                    "a no-op; for narrower competitions (HumanEval, MBPP, …) "
                    "it scopes the leaderboard to that competition's set.",
    ),
    harness: str | None = Query(
        None,
        description="restrict the leaderboard to submissions scored by this "
                    "harness (slug). Resolution: submission.harness_id → "
                    "competition.harness_id → drop. Legacy rows all resolve "
                    "to `leetcode` via the startup backfill.",
    ),
    correctness_oracle: str | None = Query(
        None,
        description="restrict the leaderboard to submissions judged by this "
                    "correctness oracle (slug). Decoupled from `harness` "
                    "above so a client can filter by 'who said this was "
                    "correct' independently of 'who measured how fast it "
                    "was'. Legacy rows all resolve to `leetcode`.",
    ),
    sort: str = Query(
        "pass_at_1",
        regex="^(pass_at_1|pass_at_k|pass_at_full|avg_runtime_percentile|avg_memory_percentile|time_human_avg|time_human_expert|mem_human_avg|mem_human_expert)$",
    ),
    session: Session = Depends(get_session),
):
    q = (
        select(Submission)
        .where(Submission.status == SubmissionStatus.judged)
        .where(Submission.deleted_at.is_(None))
    )
    if language:
        q = q.where(Submission.language == language)
    if k is not None:
        q = q.where(Submission.n_samples_per_problem == k)
    if prompt_id is not None:
        q = q.where(Submission.prompt_id == prompt_id)
    subs = session.exec(q).all()

    # Competition scope: only keep submissions whose own `competition_id`
    # matches the filter. The old logic kept any submission that had any
    # attempt overlapping with the competition's problem set, which meant
    # a `leetcode-99-perf` submission with 99 attempts would leak into
    # the `leetcode-99-hard-perf` ranking just because it covered the
    # ~9 Hard problems too — its pass@1, runtime, etc. were computed
    # against its OWN 99-problem universe though, so the displayed
    # numbers were apples-to-oranges with submissions actually scoped
    # to the Hard set.
    #
    # Legacy submissions (`competition_id IS NULL`) only show up when
    # the filter is the canonical default competition (which contains
    # every problem) — they get the canonical-99 universe via
    # `_build_universe_resolver` further down, so their metrics line up.
    if competition:
        comp = session.exec(
            select(Competition).where(Competition.slug == competition)
        ).first()
        if comp is None:
            raise HTTPException(404, f"competition '{competition}' not found")
        is_canonical_default = (competition == "leetcode-99-perf")
        subs = [
            s for s in subs
            if s.competition_id == comp.id
            or (is_canonical_default and s.competition_id is None)
        ]

    # Harness scope: resolve the effective harness per submission (own
    # override → competition default) and keep only those matching. All-
    # competitions preload so the resolver doesn't N+1.
    if harness:
        target = session.exec(
            select(Harness).where(Harness.slug == harness)
        ).first()
        if target is None:
            raise HTTPException(404, f"harness '{harness}' not found")
        _comps_all = {
            c.id: c for c in session.exec(select(Competition)).all()
        }
        subs = [
            s for s in subs
            if _resolve_submission_harness_id(s, _comps_all) == target.id
        ]

    # Correctness-oracle scope — same resolution shape as harness above.
    if correctness_oracle:
        target_co = session.exec(
            select(CorrectnessOracle).where(
                CorrectnessOracle.slug == correctness_oracle
            )
        ).first()
        if target_co is None:
            raise HTTPException(
                404, f"correctness oracle '{correctness_oracle}' not found",
            )
        _comps_all = {
            c.id: c for c in session.exec(select(Competition)).all()
        }
        subs = [
            s for s in subs
            if _resolve_submission_correctness_oracle_id(s, _comps_all)
                == target_co.id
        ]

    # Eligibility gate: only complete (all problems attempted) submissions with
    # > 25% accepted solutions appear on the leaderboard.
    #
    # "Complete" means "covers every problem in the relevant universe". The
    # universe is per-submission: a submission stores its competition_id,
    # so its universe is THAT competition's problems — independent of
    # any URL-level competition filter the caller passed in. Legacy
    # submissions with competition_id NULL fall back to the canonical-99.
    resolve_universe = _build_universe_resolver(session, subs)
    stats_by_sub = _attempt_stats_by_sub(session, [s.id for s in subs])
    subs = [
        s for s in subs
        if _eligible_for_leaderboard(
            s, _attempt_stats_for(stats_by_sub, s.id)[0], resolve_universe(s),
        )
    ]

    if level:
        keep = []
        for s in subs:
            attempts = session.exec(select(Attempt).where(Attempt.submission_id == s.id)).all()
            ps = {a.problem_id for a in attempts}
            problems = session.exec(select(Problem).where(Problem.id.in_(ps))).all()
            slugs = {p.id for p in problems if p.level == level}
            if not slugs:
                continue
            runs = session.exec(
                select(Run).where(Run.attempt_id.in_([a.id for a in attempts if a.problem_id in slugs]))
            ).all()
            solved = sum(1 for r in runs if r.status_msg == "Accepted")
            total = len(slugs)
            if total == 0:
                continue
            s = Submission(**{**s.model_dump(), "pass_at_1": solved / total, "total_solved": solved, "total_problems": total})
            keep.append(s)
        subs = keep

    def key(s: Submission):
        if sort == "pass_at_1":
            return (-(s.pass_at_1 or 0), -(s.avg_runtime_percentile or 0))
        if sort == "avg_runtime_percentile":
            return (-(s.avg_runtime_percentile or 0),)
        if sort == "avg_memory_percentile":
            return (-(s.avg_memory_percentile or 0),)
        return (-(getattr(s, sort) or 0),)

    subs.sort(key=key)
    out: list[RankingEntry] = []
    prompt_name_by_id: dict[int, str] = {
        pt.id: pt.name for pt in session.exec(select(PromptTemplate)).all()
    }
    # Single round-trip lookup of every competition referenced by these
    # submissions so we don't N+1 query per row.
    comp_by_id: dict[int, Competition] = {
        c.id: c for c in session.exec(
            select(Competition).where(Competition.id.in_(
                {s.competition_id for s in subs if s.competition_id is not None}
            ))
        ).all()
    } if subs else {}
    # Harness lookup for the effective-harness resolution below — one
    # round-trip covers all harnesses (tiny table, usually 1–3 rows).
    harness_by_id: dict[int, Harness] = {
        h.id: h for h in session.exec(select(Harness)).all()
    } if subs else {}
    # Correctness-oracle lookup for the twin resolution — same shape.
    correctness_by_id: dict[int, CorrectnessOracle] = {
        o.id: o for o in session.exec(select(CorrectnessOracle)).all()
    } if subs else {}
    # Agent (producer) lookup + the "direct" default, prefetched once so each
    # row can render its producer pill without an N+1.
    agent_by_id: dict[int, Agent] = {
        a.id: a for a in session.exec(select(Agent)).all()
    } if subs else {}
    _direct_aid = next(
        (a.id for a in agent_by_id.values() if a.slug == "direct"), None,
    )
    # Machine lookup (B4) — prefetch once so the leaderboard can show which
    # machine each entry was measured on without an N+1 query per row.
    machine_by_id: dict[int, Machine] = {
        m.id: m for m in session.exec(select(Machine)).all()
    } if subs else {}
    _all_comps_for_harness = {
        c.id: c for c in session.exec(select(Competition)).all()
    } if subs else {}
    # Pull per-attempt cost+token series in ONE pass keyed by submission
    # id so we can compute the per-row total and median without an N+1.
    # Includes prompt_tokens so we can live-recompute cost when the model
    # has /models pricing set (otherwise we fall back to attempt.cost_usd).
    _pricing = _model_pricing_lookup(session)
    sub_attempts: dict[int, list[Attempt]] = {}
    if subs:
        for a in session.exec(
            select(Attempt).where(Attempt.submission_id.in_({s.id for s in subs}))
        ).all():
            sub_attempts.setdefault(a.submission_id, []).append(a)
    for i, s in enumerate(subs, start=1):
        u = session.get(User, s.user_id)
        comp = comp_by_id.get(s.competition_id) if s.competition_id else None
        hid = _resolve_submission_harness_id(s, _all_comps_for_harness)
        harness_row = harness_by_id.get(hid) if hid else None
        cid_correct = _resolve_submission_correctness_oracle_id(
            s, _all_comps_for_harness,
        )
        correctness_row = (
            correctness_by_id.get(cid_correct) if cid_correct else None
        )
        aid = _resolve_submission_agent_id(s, _direct_aid)
        agent_row = agent_by_id.get(aid) if aid else None
        atts = sub_attempts.get(s.id, [])
        # Per-attempt cost: live recompute when possible, else snapshot.
        per_cost: list[float] = []
        for a in atts:
            live = _recompute_attempt_cost(a, s.model_name, s.model_version, _pricing)
            x = live if live is not None else a.cost_usd
            if x is not None:
                per_cost.append(x)
        per_tokens = [a.response_tokens for a in atts if a.response_tokens is not None]
        per_wall = [a.wall_clock_ms for a in atts if a.wall_clock_ms is not None]
        per_energy = [a.generation_energy_j for a in atts if a.generation_energy_j is not None]
        total_cost = sum(per_cost) if per_cost else None
        total_tokens = sum(per_tokens) if per_tokens else s.total_response_tokens
        total_wall = sum(per_wall) if per_wall else s.total_generation_ms
        total_energy = sum(per_energy) if per_energy else s.total_generation_energy_j
        out.append(RankingEntry(
            rank=i, model_name=s.model_name, model_version=s.model_version,
            language=s.language,
            pass_at_1=s.pass_at_1 or 0.0,
            avg_runtime_ms=s.avg_runtime_ms,
            avg_memory_mb=s.avg_memory_mb,
            best_runtime_ms=s.best_runtime_ms,
            best_memory_mb=s.best_memory_mb,
            avg_runtime_percentile=s.avg_runtime_percentile,
            avg_memory_percentile=s.avg_memory_percentile,
            avg_runtime_energy_j=s.avg_runtime_energy_j,
            best_runtime_energy_j=s.best_runtime_energy_j,
            avg_runtime_energy_percentile=s.avg_runtime_energy_percentile,
            best_runtime_energy_percentile=s.best_runtime_energy_percentile,
            machine_name=(machine_by_id.get(s.machine_id).name
                          if s.machine_id and machine_by_id.get(s.machine_id)
                          else None),
            energy_tool=s.energy_tool,
            time_human_avg=s.time_human_avg,
            time_human_expert=s.time_human_expert,
            mem_human_avg=s.mem_human_avg,
            mem_human_expert=s.mem_human_expert,
            total_solved=s.total_solved, total_problems=s.total_problems,
            n_attempts=_attempt_stats_for(stats_by_sub, s.id)[1],
            n_samples_per_problem=s.n_samples_per_problem or 1,
            pass_at_k=s.pass_at_k,
            pass_at_full=s.pass_at_full,
            submission_id=s.id,
            submitter=_submitter_label(u),
            submitter_slug=_slug(_submitter_label(u)),
            prompt_id=s.prompt_id,
            prompt_name=prompt_name_by_id.get(s.prompt_id) if s.prompt_id else None,
            competition_slug=comp.slug if comp else None,
            competition_name=comp.name if comp else None,
            harness_slug=harness_row.slug if harness_row else None,
            harness_name=harness_row.name if harness_row else None,
            correctness_oracle_slug=correctness_row.slug if correctness_row else None,
            correctness_oracle_name=correctness_row.name if correctness_row else None,
            agent_slug=agent_row.slug if agent_row else None,
            agent_name=agent_row.name if agent_row else None,
            total_response_tokens=total_tokens,
            median_response_tokens_per_attempt=_median_or_none(per_tokens),
            total_cost_usd=total_cost,
            median_cost_usd_per_attempt=_median_or_none(per_cost),
            total_generation_ms=int(total_wall) if total_wall is not None else None,
            median_generation_ms_per_attempt=_median_or_none(per_wall),
            total_generation_energy_j=total_energy,
            median_generation_energy_j_per_attempt=_median_or_none(per_energy),
        ))
    return out


@app.get("/api/submissions", response_model=list[SubmissionPublic])
def list_submissions(session: Session = Depends(get_session)):
    # Sort by last activity (newest attempt), falling back to created_at
    # for submissions that don't have any attempts yet. We don't have an
    # indexed last_attempt_at column on Submission, so we sort in Python
    # after _submission_public() computes it. Cheap for the dataset
    # sizes we run at (≪ 10k submissions); revisit if that changes.
    subs = session.exec(
        select(Submission)
        .where(Submission.deleted_at.is_(None))
    ).all()
    # One pass over the light attempt columns for ALL submissions, plus the
    # problem ids and pricing once per request, instead of a full Attempt and
    # Problem load per submission (trace/code blobs made the list take seconds).
    atts_by_sub: dict[int, list] = {s.id: [] for s in subs}
    for a in session.exec(select(*_SUBMISSION_ATTEMPT_COLS)).all():
        if a.submission_id in atts_by_sub:
            atts_by_sub[a.submission_id].append(a)
    all_pids = set(session.exec(select(Problem.id)).all())
    pricing = _model_pricing_lookup(session)
    rows = [_submission_public(s, session, atts=atts_by_sub[s.id],
                               all_pids=all_pids, pricing=pricing) for s in subs]
    rows.sort(
        key=lambda r: r.last_attempt_at or r.created_at,
        reverse=True,
    )
    return rows


# ---------- Registered models (the curated catalog) -------------------------

@app.get("/api/registered-models", response_model=list[RegisteredModelOverview])
def list_registered_models(
    include_retired: bool = False,
    session: Session = Depends(get_session),
):
    """List every model the platform knows about, with per-model activity
    stats (submission counts, languages, freshness, best pass@1). Includes
    BOTH admin-curated rows AND auto-stubbed rows (status='unverified') from
    first-submission auto-discovery. Retired rows are hidden unless
    `include_retired=true`."""
    q = select(RegisteredModel).order_by(RegisteredModel.added_at.desc())
    if not include_retired:
        # Default: active + unverified (everything except retired).
        q = q.where(RegisteredModel.status != "retired")
    models = session.exec(q).all()

    # One pass over non-deleted submissions → stats keyed by (name, version).
    # Cheap for the scale we run at (≪ 10k submissions, ≪ 100 models) and
    # avoids N+1 queries on every page load.
    subs = session.exec(
        select(Submission).where(Submission.deleted_at.is_(None))
    ).all()
    by_key: dict[tuple[str, str], list[Submission]] = {}
    for s in subs:
        by_key.setdefault((s.model_name, s.model_version), []).append(s)

    out: list[RegisteredModelOverview] = []
    for rm in models:
        matching = by_key.get((rm.name, rm.version), [])
        judged = [s for s in matching if s.status == SubmissionStatus.judged]
        best_p1 = max((s.pass_at_1 for s in judged if s.pass_at_1 is not None),
                      default=None)
        out.append(RegisteredModelOverview(
            id=rm.id, name=rm.name, version=rm.version, provider=rm.provider,
            endpoint=rm.endpoint, params=rm.params, notes=rm.notes,
            description=rm.description, model_type=rm.model_type,
            family=rm.family, provenance=rm.provenance,
            status=rm.status, added_at=rm.added_at,
            input_price_per_1m_usd=rm.input_price_per_1m_usd,
            output_price_per_1m_usd=rm.output_price_per_1m_usd,
            n_submissions=len(matching),
            n_judged=len(judged),
            last_submitted_at=max((s.created_at for s in matching), default=None),
            languages=sorted({s.language for s in matching}),
            best_pass_at_1=best_p1,
        ))
    return out


@app.get("/api/registered-models/{rm_id}", response_model=RegisteredModelPublic)
def get_registered_model(rm_id: int, session: Session = Depends(get_session)):
    rm = session.get(RegisteredModel, rm_id)
    if not rm:
        raise HTTPException(404, "registered model not found")
    return rm


@app.post("/api/admin/registered-models",
          response_model=RegisteredModelPublic,
          status_code=status.HTTP_201_CREATED)
def add_registered_model(
    payload: RegisteredModelIn,
    admin: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    rm = RegisteredModel(**payload.model_dump(), added_by_user_id=admin.id)
    session.add(rm); session.commit(); session.refresh(rm)
    return rm


@app.put("/api/admin/registered-models/{rm_id}",
         response_model=RegisteredModelPublic)
def edit_registered_model(
    rm_id: int, payload: RegisteredModelIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    rm = session.get(RegisteredModel, rm_id)
    if not rm:
        raise HTTPException(404, "registered model not found")
    for k, v in payload.model_dump().items():
        setattr(rm, k, v)
    session.add(rm); session.commit(); session.refresh(rm)
    return rm


@app.delete("/api/admin/registered-models/{rm_id}",
            status_code=status.HTTP_204_NO_CONTENT)
def delete_registered_model(
    rm_id: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    rm = session.get(RegisteredModel, rm_id)
    if not rm:
        raise HTTPException(404, "registered model not found")
    session.delete(rm); session.commit()
    return None


# ---- Per-model pricing -----------------------------------------------------
#
# Inline-edit endpoint for the /models page. We keep this distinct from the
# generic PUT above so the SPA can patch just the two cost cells without
# round-tripping every other registered-model field — that would let an
# operator who clicks "save price" silently revert a description someone
# else just edited.

@app.patch("/api/admin/registered-models/{rm_id}/pricing",
           response_model=RegisteredModelPublic)
def update_registered_model_pricing(
    rm_id: int, payload: RegisteredModelPricingPatch,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Update ONLY the per-1M-token rates for one registered model. The
    SPA calls this on every blur of the two cost cells; null values are
    persisted (and clear any previously-set rate, falling cost display
    back to per-attempt snapshots).
    """
    rm = session.get(RegisteredModel, rm_id)
    if not rm:
        raise HTTPException(404, "registered model not found")
    rm.input_price_per_1m_usd = payload.input_price_per_1m_usd
    rm.output_price_per_1m_usd = payload.output_price_per_1m_usd
    session.add(rm); session.commit(); session.refresh(rm)
    return rm


# Default per-model pricing seeded from the same table the sweep client
# uses. Operator can fill the rates in for every known model in one call
# rather than copying numbers cell-by-cell. The table here mirrors
# `webapp/examples/submit_with_local_model.py::OPENAI_PRICING_USD_PER_1M`
# — when prices change, edit both (the client table drives at-submit
# snapshots, this one drives the SPA-side recomputation).
_DEFAULT_MODEL_PRICING_USD_PER_1M: dict[str, dict[str, float]] = {
    "gpt-5":             {"in": 1.25, "out": 10.00},
    "gpt-5-mini":        {"in": 0.25, "out": 2.00},
    "gpt-5.4-mini":      {"in": 0.25, "out": 2.00},
    "gpt-5-nano":        {"in": 0.05, "out": 0.40},
    "gpt-5-chat":        {"in": 1.25, "out": 10.00},
    "gpt-5-chat-latest": {"in": 1.25, "out": 10.00},
    "gpt-4.1":           {"in": 2.00, "out": 8.00},
    "gpt-4.1-mini":      {"in": 0.40, "out": 1.60},
    "gpt-4.1-nano":      {"in": 0.10, "out": 0.40},
    "gpt-4o":            {"in": 2.50, "out": 10.00},
    "gpt-4o-mini":       {"in": 0.15, "out": 0.60},
    "o4-mini":           {"in": 1.10, "out": 4.40},
    "o3":                {"in": 2.00, "out": 8.00},
    "o3-mini":           {"in": 1.10, "out": 4.40},
}


@app.post("/api/admin/registered-models/seed-pricing")
def seed_registered_model_pricing(
    overwrite: bool = False,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Populate `input_price_per_1m_usd` / `output_price_per_1m_usd` on
    every RegisteredModel row whose `name` matches an entry in the
    built-in defaults table (the same one the matrix_sweep client uses
    to snapshot costs at submit time).

    By default we ONLY fill rows whose pricing fields are null — that
    way an operator who set custom rates on, say, gpt-4o-mini doesn't
    get them clobbered on a re-seed. Pass `?overwrite=true` to force
    re-application of the defaults across every matching row.

    Returns a small summary so the SPA can show "filled N rows; skipped
    M (already had custom rates); 0 matched by name in defaults table".
    """
    rms = session.exec(select(RegisteredModel)).all()
    filled = 0
    skipped_has_rates = 0
    skipped_no_default = 0
    for rm in rms:
        defaults = _DEFAULT_MODEL_PRICING_USD_PER_1M.get(rm.name)
        if defaults is None:
            skipped_no_default += 1
            continue
        already_set = (
            rm.input_price_per_1m_usd is not None
            or rm.output_price_per_1m_usd is not None
        )
        if already_set and not overwrite:
            skipped_has_rates += 1
            continue
        rm.input_price_per_1m_usd = defaults["in"]
        rm.output_price_per_1m_usd = defaults["out"]
        session.add(rm)
        filled += 1
    session.commit()
    return {
        "filled": filled,
        "skipped_has_rates": skipped_has_rates,
        "skipped_no_default": skipped_no_default,
        "overwrite_mode": overwrite,
        "total_registered_models": len(rms),
    }


# ---------- Prompt templates ------------------------------------------------

@app.get("/api/prompts", response_model=list[PromptTemplatePublic])
def list_prompts(
    include_retired: bool = False,
    session: Session = Depends(get_session),
):
    q = select(PromptTemplate).order_by(PromptTemplate.created_at.desc())
    if not include_retired:
        q = q.where(PromptTemplate.status == "active")
    return session.exec(q).all()


@app.get("/api/prompts/{pt_id}", response_model=PromptTemplatePublic)
def get_prompt(pt_id: int, session: Session = Depends(get_session)):
    pt = session.get(PromptTemplate, pt_id)
    if not pt:
        raise HTTPException(404, "prompt not found")
    return pt


@app.post("/api/admin/prompts",
          response_model=PromptTemplatePublic,
          status_code=status.HTTP_201_CREATED)
def add_prompt(
    payload: PromptTemplateIn,
    admin: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    pt = PromptTemplate(**payload.model_dump(), created_by_user_id=admin.id)
    session.add(pt); session.commit(); session.refresh(pt)
    return pt


@app.put("/api/admin/prompts/{pt_id}", response_model=PromptTemplatePublic)
def edit_prompt(
    pt_id: int, payload: PromptTemplateIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    pt = session.get(PromptTemplate, pt_id)
    if not pt:
        raise HTTPException(404, "prompt not found")
    for k, v in payload.model_dump().items():
        setattr(pt, k, v)
    session.add(pt); session.commit(); session.refresh(pt)
    return pt


@app.delete("/api/admin/prompts/{pt_id}",
            status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt(
    pt_id: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    pt = session.get(PromptTemplate, pt_id)
    if not pt:
        raise HTTPException(404, "prompt not found")
    session.delete(pt); session.commit()
    return None


# ---------- Competitions ----------------------------------------------------
#
# A Competition groups a set of Problems under one evaluation regime. The 99
# LeetCode problems are seeded on startup as the default competition; admins
# can add more (HumanEval, MBPP, a custom set) via the admin endpoints. Each
# (problem, language) Submission keeps referencing problems by slug — there
# is no FK from Submission to Competition — so a problem in multiple
# competitions contributes to each one's leaderboard simultaneously.


def _competition_stats(
    session: Session, comp_problem_ids: set[int],
) -> tuple[int, int, list[str], list[Submission]]:
    """Cheap rollup over the problems in a competition. Returns
    (n_submissions, n_models, languages_sorted, judged_subs)."""
    if not comp_problem_ids:
        return 0, 0, [], []
    # All non-deleted attempts on the competition's problems → their parent
    # submissions count as "competing in this competition".
    sub_ids = {
        a.submission_id for a in session.exec(
            select(Attempt).where(Attempt.problem_id.in_(comp_problem_ids))   # type: ignore[arg-type]
        ).all()
    }
    if not sub_ids:
        return 0, 0, [], []
    subs = session.exec(
        select(Submission)
        .where(Submission.id.in_(sub_ids))                                    # type: ignore[arg-type]
        .where(Submission.deleted_at.is_(None))
    ).all()
    models = {(s.model_name, s.model_version) for s in subs}
    judged = [s for s in subs if s.status == SubmissionStatus.judged]
    return (len(subs), len(models),
            sorted({s.language for s in subs}), judged)


# ---------- Cost estimator -------------------------------------------------
#
# "How much would it cost me to run THIS model across PerfArena's problem set?"
# The naive answer is `avg_tokens_per_attempt × n_attempts × $/1M-tokens`, but
# that fakes a precision that historical data doesn't actually have — token
# counts vary wildly between problems, prompts, and the model's verbosity
# of the day. So instead of a single point estimate we surface the OBSERVED
# RANGE of full-sweep token totals: every already-completed submission that
# covers the problem set in scope contributes one (total_in, total_out)
# datapoint, and the user gets a [min … max] envelope to multiply by their
# own $-per-1M rate.
#
# We parse the per-attempt `provider_meta` out of trace JSON at query time:
#
#   * OpenAI:  trace.provider_meta.usage.{prompt_tokens, completion_tokens}
#   * Ollama:  trace.provider_meta.{prompt_eval_count, eval_count}
#
# No schema migration — the data has been landing in the trace column since
# day one of the OpenAI provider work. Cost stays cheap (one Python decode
# per attempt; <1 ms in practice) because the calculator's scope is bounded
# to full sweeps, of which there are tens at most per filter combo.


def _extract_attempt_tokens(trace: dict | None) -> tuple[int | None, int | None]:
    """Return (input_tokens, output_tokens) from an attempt's trace dict,
    handling the two on-the-wire shapes we ingest. Either value is None
    when the corresponding count isn't present (e.g. an Ollama attempt
    where the model didn't report prompt_eval_count)."""
    if not isinstance(trace, dict):
        return None, None
    meta = trace.get("provider_meta")
    if not isinstance(meta, dict):
        return None, None
    # OpenAI shape — .usage is the canonical sub-object on a Chat Completions
    # response and survives the round-trip into our trace dict untouched.
    usage = meta.get("usage")
    if isinstance(usage, dict):
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if pt is not None or ct is not None:
            return (int(pt) if pt is not None else None,
                    int(ct) if ct is not None else None)
    # Ollama shape — flat counts directly on provider_meta.
    pe = meta.get("prompt_eval_count")
    ev = meta.get("eval_count")
    if pe is not None or ev is not None:
        return (int(pe) if pe is not None else None,
                int(ev) if ev is not None else None)
    return None, None


@app.get("/api/cost-estimator-stats", response_model=CostEstimatorStats)
def cost_estimator_stats(
    prompt_id: int | None = None,
    language: str | None = None,
    model_name: str | None = None,
    competition: str | None = None,
    harness: str | None = None,
    session: Session = Depends(get_session),
):
    """Token footprints of every full-sweep submission matching the filters,
    plus the universes for each filter's dropdown. The frontend's calculator
    multiplies the returned min/max by the user-supplied $-per-1M rates to
    produce a cost envelope.

    "Full sweep" = a submission whose attempts cover every problem in its
    competition (or in the platform if it isn't tied to one). Partial
    sweeps are listed but flagged `is_full_sweep=false` and excluded from
    the min/max envelope — comparing a 30-problem sweep to a 99-problem
    sweep would skew the range without telling the user anything useful."""
    # --- Universes for the dropdowns. Computed BEFORE we apply filters so
    # the user can navigate the picker from any filter state. ---
    all_subs = session.exec(
        select(Submission).where(Submission.deleted_at.is_(None))
    ).all()
    prompt_id_set = {s.prompt_id for s in all_subs if s.prompt_id is not None}
    prompt_objs = session.exec(
        select(PromptTemplate).where(PromptTemplate.id.in_(prompt_id_set))
    ).all() if prompt_id_set else []
    available_prompts = sorted(
        [{"id": p.id, "name": p.name} for p in prompt_objs],
        key=lambda r: r["name"].lower(),
    )
    available_languages = sorted({s.language for s in all_subs if s.language})
    available_models = sorted({s.model_name for s in all_subs if s.model_name})
    # All non-retired competitions are pickable. We don't gate by "appears
    # in submissions" because someone may want to estimate cost for a NEW
    # competition before any sweep has run against it.
    comp_rows = session.exec(
        select(Competition).where(Competition.status != "retired")
    ).all()
    available_competitions = sorted(
        [{"slug": c.slug, "name": c.name} for c in comp_rows],
        key=lambda r: r["name"].lower(),
    )
    # Same for harnesses — every active harness is pickable so an operator
    # can estimate cost for a NEW harness before any sweep uses it.
    harness_rows = session.exec(
        select(Harness).where(Harness.status != "retired")
    ).all()
    available_harnesses = sorted(
        [{"slug": h.slug, "name": h.name} for h in harness_rows],
        key=lambda r: r["name"].lower(),
    )

    # --- Apply the filters. ---
    q = select(Submission).where(Submission.deleted_at.is_(None))
    if prompt_id is not None:
        q = q.where(Submission.prompt_id == prompt_id)
    if language:
        q = q.where(Submission.language == language)
    if model_name:
        q = q.where(Submission.model_name == model_name)
    comp_obj: Competition | None = None
    comp_problem_ids: set[int] | None = None
    if competition:
        comp_obj = session.exec(
            select(Competition).where(Competition.slug == competition)
        ).first()
        if comp_obj is None:
            raise HTTPException(404, f"competition '{competition}' not found")
        comp_problem_ids = {
            cp.problem_id for cp in session.exec(
                select(CompetitionProblem).where(
                    CompetitionProblem.competition_id == comp_obj.id
                )
            ).all()
        }
    subs = session.exec(q).all()

    # Harness scope: resolve the effective harness per submission (own
    # override → competition default) and drop rows whose harness doesn't
    # match. Loaded here so we can also stamp harness_slug/name onto every
    # row we return.
    _all_comps_for_harness = {
        c.id: c for c in session.exec(select(Competition)).all()
    }
    if harness:
        target_h = session.exec(
            select(Harness).where(Harness.slug == harness)
        ).first()
        if target_h is None:
            raise HTTPException(404, f"harness '{harness}' not found")
        subs = [
            s for s in subs
            if _resolve_submission_harness_id(s, _all_comps_for_harness) == target_h.id
        ]
    # Full lookup so we can annotate every returned row with its harness,
    # regardless of whether a filter is set.
    harness_by_id = {h.id: h for h in session.exec(select(Harness)).all()}

    # Problem-count denominator — defines what "full sweep" means in scope.
    # Without a competition filter we use the total platform problem count
    # (the seed competition links every problem, so this matches the
    # leetcode-99-perf size in practice).
    if comp_problem_ids is not None:
        n_in_scope = len(comp_problem_ids)
    else:
        # Count once, no need to load every row.
        n_in_scope = len(session.exec(select(Problem.id)).all())

    # Pre-fetch prompt names for the rows we'll emit.
    sub_prompt_ids = {s.prompt_id for s in subs if s.prompt_id is not None}
    prompt_name_by_id = {
        p.id: p.name for p in session.exec(
            select(PromptTemplate).where(PromptTemplate.id.in_(sub_prompt_ids))
        ).all()
    } if sub_prompt_ids else {}

    # Each submission's competition memberships (light-weight; via the join
    # table). We pick the first matching competition for display — most
    # submissions belong to one.
    sub_ids = [s.id for s in subs]
    sub_competitions: dict[int, tuple[str, str]] = {}
    if sub_ids:
        # Reach competitions via the problems the submission's attempts hit.
        # That's the only link between Submission and Competition today.
        attempt_problems = session.exec(
            select(Attempt.submission_id, Attempt.problem_id)
            .where(Attempt.submission_id.in_(sub_ids))
        ).all()
        # problem_id -> first (slug, name) it competes under
        per_problem_comp: dict[int, tuple[str, str]] = {}
        for cp, comp in session.exec(
            select(CompetitionProblem, Competition)
            .where(CompetitionProblem.competition_id == Competition.id)
        ).all():
            per_problem_comp.setdefault(cp.problem_id, (comp.slug, comp.name))
        for sid, pid in attempt_problems:
            if sid in sub_competitions:
                continue
            hit = per_problem_comp.get(pid)
            if hit:
                sub_competitions[sid] = hit

    rows: list[CostEstimatorSubmission] = []
    full_input_totals: list[int] = []
    full_output_totals: list[int] = []
    full_energy_totals: list[int] = []
    for s in subs:
        attempts = session.exec(
            select(Attempt).where(Attempt.submission_id == s.id)
        ).all()
        if not attempts:
            continue
        # Restrict to attempts hitting the competition's problem set when a
        # competition filter is active — protects against a cross-competition
        # submission inflating "n_problems_attempted".
        if comp_problem_ids is not None:
            attempts = [a for a in attempts if a.problem_id in comp_problem_ids]
        if not attempts:
            continue

        total_in: int | None = None
        total_out: int | None = None
        n_with_tokens = 0
        total_energy: float | None = None
        n_with_energy = 0
        for a in attempts:
            in_tok, out_tok = _extract_attempt_tokens(a.trace)
            if in_tok is not None or out_tok is not None:
                n_with_tokens += 1
            if in_tok is not None:
                total_in = (total_in or 0) + in_tok
            if out_tok is not None:
                total_out = (total_out or 0) + out_tok
            if a.generation_energy_j is not None:
                total_energy = (total_energy or 0.0) + a.generation_energy_j
                n_with_energy += 1

        n_attempted = len({a.problem_id for a in attempts})
        is_full = n_in_scope > 0 and n_attempted >= n_in_scope

        comp_hit = sub_competitions.get(s.id)
        _hid = _resolve_submission_harness_id(s, _all_comps_for_harness)
        h_row = harness_by_id.get(_hid) if _hid else None
        rows.append(CostEstimatorSubmission(
            submission_id=s.id,
            model_name=s.model_name,
            model_version=s.model_version,
            language=s.language,
            prompt_id=s.prompt_id,
            prompt_name=prompt_name_by_id.get(s.prompt_id) if s.prompt_id else None,
            competition_slug=comp_hit[0] if comp_hit else None,
            competition_name=comp_hit[1] if comp_hit else None,
            harness_slug=h_row.slug if h_row else None,
            harness_name=h_row.name if h_row else None,
            n_attempts=len(attempts),
            n_problems_attempted=n_attempted,
            n_problems_in_scope=n_in_scope,
            is_full_sweep=is_full,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            n_attempts_with_token_data=n_with_tokens,
            total_generation_energy_j=total_energy,
            n_attempts_with_energy_data=n_with_energy,
        ))
        # Only full sweeps with at least one token-bearing attempt go into
        # the envelope — half-instrumented partial sweeps would lie.
        if is_full and n_with_tokens > 0:
            if total_in is not None:
                full_input_totals.append(total_in)
            if total_out is not None:
                full_output_totals.append(total_out)
        if is_full and total_energy is not None:
            full_energy_totals.append(int(round(total_energy)))

    def _range(xs: list[int]) -> CostEstimatorRange:
        if not xs:
            return CostEstimatorRange(min=None, max=None, median=None, n=0)
        s = sorted(xs)
        mid = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) // 2
        return CostEstimatorRange(min=s[0], max=s[-1], median=mid, n=len(s))

    # Order rows newest-first by submission id so the table reads top-down
    # as recent → older — consistent with the rest of the platform.
    rows.sort(key=lambda r: r.submission_id, reverse=True)

    return CostEstimatorStats(
        applied={
            "prompt_id": prompt_id,
            "language": language,
            "model_name": model_name,
            "competition": competition,
            "harness": harness,
        },
        available_prompts=available_prompts,
        available_languages=available_languages,
        available_models=available_models,
        available_competitions=available_competitions,
        available_harnesses=available_harnesses,
        submissions=rows,
        input_tokens=_range(full_input_totals),
        output_tokens=_range(full_output_totals),
        generation_energy_j=_range(full_energy_totals),
    )


@app.get("/api/competitions", response_model=list[CompetitionPublic])
def list_competitions(
    include_retired: bool = False,
    session: Session = Depends(get_session),
):
    """List every competition the platform tracks, with activity stats per
    row (n_problems, n_submissions, n_models). Retired rows hidden unless
    `include_retired=true`."""
    q = select(Competition).order_by(Competition.created_at.desc())
    if not include_retired:
        q = q.where(Competition.status == "active")
    comps = session.exec(q).all()

    # Precompute membership in one pass.
    members = session.exec(select(CompetitionProblem)).all()
    by_comp: dict[int, set[int]] = {}
    for m in members:
        by_comp.setdefault(m.competition_id, set()).add(m.problem_id)

    out: list[CompetitionPublic] = []
    for c in comps:
        pids = by_comp.get(c.id, set())
        n_subs, n_models, _langs, _judged = _competition_stats(session, pids)
        out.append(CompetitionPublic(
            id=c.id, slug=c.slug, name=c.name,
            description=c.description, kind=c.kind, status=c.status,
            created_at=c.created_at,
            n_problems=len(pids),
            n_submissions=n_subs,
            n_models=n_models,
            harnesses_with_data=_competition_harnesses_with_data(session, c),
            compatible_harness_slugs=c.compatible_harness_slugs or [],
        ))
    return out


@app.get("/api/competitions/{slug}", response_model=CompetitionDetail)
def get_competition(
    slug: str,
    harness_slug: str | None = Query(
        default=None,
        description="Scope the leaderboard (`top_models`) to submissions "
                    "measured by this harness. Omit to include every "
                    "harness's data pooled together. Available harness "
                    "slugs for this competition are in "
                    "`harnesses_with_data`.",
    ),
    session: Session = Depends(get_session),
):
    c = session.exec(
        select(Competition).where(Competition.slug == slug)
    ).first()
    if c is None:
        raise HTTPException(404, "competition not found")
    member_pids = {
        m.problem_id for m in session.exec(
            select(CompetitionProblem).where(CompetitionProblem.competition_id == c.id)
        ).all()
    }
    problems = session.exec(
        select(Problem).where(Problem.id.in_(member_pids))                    # type: ignore[arg-type]
        .order_by(Problem.level, Problem.title)
    ).all() if member_pids else []

    # Resolve the optional harness scope BEFORE computing stats so the
    # numbers (`n_submissions`, `n_models`, `languages`, leaderboard)
    # all reflect the same slice — an operator viewing the arena-local
    # tab must see arena-local's own model/language count, not the
    # pooled totals.
    scope_harness_id: int | None = None
    if harness_slug:
        h = session.exec(
            select(Harness).where(Harness.slug == harness_slug)
        ).first()
        if h is None:
            raise HTTPException(400, f"unknown harness slug {harness_slug!r}")
        scope_harness_id = h.id

    n_subs, n_models, langs, judged = _competition_stats(session, member_pids)
    if scope_harness_id is not None:
        judged = [s for s in judged if s.harness_id == scope_harness_id]
        # Recompute the scoped rollups so the top strip matches the
        # tab. Languages come from the scoped submissions' language
        # column; model count from distinct (name, version) pairs.
        n_subs = len(judged)
        n_models = len({(s.model_name, s.model_version) for s in judged})
        langs = sorted({s.language for s in judged if s.language})

    # Per-model leaderboard SCOPED to this competition.
    #
    # Previously this picked each submission's own `pass_at_1` directly
    # — which was wrong when the submission was created against a DIFFERENT
    # competition (e.g. the canonical `leetcode-99-perf` 99-problem set)
    # but happens to overlap on a few problems with the one being viewed
    # (`leetcode-99-hard-perf`). The submission's own pass_at_1 reflects
    # ITS competition's universe (the full 99), not this competition's
    # smaller subset, so the displayed number was misleading.
    #
    # Now we recompute every metric from scratch over the per-(model,
    # version) attempts that touch THIS competition's problem set. Each
    # problem contributes ONE data point (best accepted attempt, same
    # picker the rest of the SPA uses); pass_at_1 is then accepted /
    # total-in-competition.
    n_comp_problems = max(len(member_pids), 1)
    # Pull every attempt on the competition's problems in one query so
    # we don't N+1 per submission.
    comp_attempts_by_sub: dict[int, list[Attempt]] = {}
    if member_pids and judged:
        judged_ids = {s.id for s in judged}
        for a in session.exec(
            select(Attempt).where(
                Attempt.submission_id.in_(judged_ids),
                Attempt.problem_id.in_(member_pids),
            )
        ).all():
            comp_attempts_by_sub.setdefault(a.submission_id, []).append(a)

    by_model: dict[tuple[str, str], list[Submission]] = {}
    for s in judged:
        by_model.setdefault((s.model_name, s.model_version), []).append(s)
    top: list[dict] = []
    for (name, ver), subs in by_model.items():
        # Per-problem best attempt across this model's submissions on
        # the competition's problems. A model that submitted twice for
        # the same problem only contributes its WINNING attempt to the
        # competition metrics — same `accepted-runs-share, lowest avg
        # runtime` picker `_compute_winners` uses elsewhere.
        per_problem: dict[int, Attempt] = {}
        for s in subs:
            for a in comp_attempts_by_sub.get(s.id, []):
                cur = per_problem.get(a.problem_id)
                # Picker: highest accepted share, then lowest avg runtime.
                a_share = ((a.n_accepted_runs or 0) / a.n_runs) if a.n_runs else 0
                if cur is None:
                    per_problem[a.problem_id] = a
                else:
                    cur_share = ((cur.n_accepted_runs or 0) / cur.n_runs) if cur.n_runs else 0
                    if a_share > cur_share:
                        per_problem[a.problem_id] = a
                    elif a_share == cur_share:
                        a_rt = a.avg_runtime_ms if a.avg_runtime_ms is not None else float("inf")
                        cur_rt = cur.avg_runtime_ms if cur.avg_runtime_ms is not None else float("inf")
                        if a_rt < cur_rt:
                            per_problem[a.problem_id] = a
        if not per_problem:
            continue
        accepted_atts = [
            a for a in per_problem.values()
            if a.final_status == "Accepted"
        ]
        n_acc = len(accepted_atts)
        n_attempted = len(per_problem)
        # pass@1 is the universal "fraction of the competition solved".
        # pass@k is "of the problems attempted, what share got an Accept"
        # — same definitions as everywhere else in the SPA.
        pass_at_1 = n_acc / n_comp_problems
        pass_at_k = (n_acc / n_attempted) if n_attempted else 0.0
        rt = [a.avg_runtime_ms for a in accepted_atts if a.avg_runtime_ms is not None]
        mem = [a.avg_memory_mb for a in accepted_atts if a.avg_memory_mb is not None]
        rtp = [a.avg_runtime_percentile for a in accepted_atts if a.avg_runtime_percentile is not None]
        memp = [a.avg_memory_percentile for a in accepted_atts if a.avg_memory_percentile is not None]
        top.append({
            "model_name": name,
            "model_version": ver,
            "best_pass_at_1": pass_at_1,
            "pass_at_k": pass_at_k,
            "n_submissions": len(subs),
            "n_solved": n_acc,
            "n_attempted": n_attempted,
            "n_problems": n_comp_problems,
            "avg_runtime_ms": _mean_or_none(rt),
            "avg_memory_mb": _mean_or_none(mem),
            "avg_runtime_percentile": _mean_or_none(rtp),
            "avg_memory_percentile": _mean_or_none(memp),
        })
    # Primary sort: pass@1 desc. Tiebreak: runtime-percentile desc, then
    # memory-percentile desc — same "more is better" semantics as /rankings.
    top.sort(key=lambda r: (
        -(r["best_pass_at_1"] or 0),
        -(r.get("avg_runtime_percentile") or 0),
        -(r.get("avg_memory_percentile") or 0),
    ))

    return CompetitionDetail(
        id=c.id, slug=c.slug, name=c.name,
        description=c.description, kind=c.kind, status=c.status,
        created_at=c.created_at,
        n_problems=len(member_pids),
        n_submissions=n_subs,
        n_models=n_models,
        harnesses_with_data=_competition_harnesses_with_data(session, c),
        compatible_harness_slugs=c.compatible_harness_slugs or [],
        problems=[
            ProblemPublic(
                id=p.id, title_slug=p.title_slug, title=p.title,
                level=p.level, tags=[t.strip() for t in p.tags.split(",") if t.strip()],
            )
            for p in problems
        ],
        languages=langs,
        top_models=top[:5],
    )


def _validate_compat_slugs(
    session: Session, slugs: list[str],
) -> list[str]:
    """Verify every slug in `slugs` resolves to an existing Harness row.
    De-dupes, drops empties, preserves order. Raises 400 on unknown
    slugs so a typo on the admin form fails at edit-time rather than
    at first-submit time."""
    seen: set[str] = set()
    ordered: list[str] = []
    for s in (slugs or []):
        s = (s or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        ordered.append(s)
    if not ordered:
        return []
    existing = session.exec(
        select(Harness.slug).where(Harness.slug.in_(ordered))       # type: ignore[arg-type]
    ).all()
    existing_set = set(existing)
    unknown = [s for s in ordered if s not in existing_set]
    if unknown:
        raise HTTPException(
            400, f"unknown harness slug(s) in compatible_harness_slugs: {unknown}"
        )
    return ordered


def _competition_harnesses_with_data(
    session: Session, c: Competition,
) -> list[str]:
    """Slugs of every harness that actually has Submission rows for this
    competition. Drives the SPA's per-harness leaderboard tabs — a tab
    appears iff there's data behind it. Sorted alphabetically for a
    stable UI order."""
    if c.id is None:
        return []
    slugs = session.exec(
        select(Harness.slug)
        .join(Submission, Submission.harness_id == Harness.id)
        .where(Submission.competition_id == c.id)
        .distinct()
    ).all()
    return sorted(slugs)


@app.post("/api/admin/competitions",
          response_model=CompetitionPublic,
          status_code=status.HTTP_201_CREATED)
def add_competition(
    payload: CompetitionIn,
    admin: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    if session.exec(select(Competition).where(Competition.slug == payload.slug)).first():
        raise HTTPException(409, f"slug '{payload.slug}' already in use")
    # Validate every entry in the compat allow-list resolves to a real
    # harness slug before persisting, so a typo surfaces at edit time.
    compat = _validate_compat_slugs(session, payload.compatible_harness_slugs)
    data = payload.model_dump()
    data["compatible_harness_slugs"] = compat
    c = Competition(**data, created_by_user_id=admin.id)
    session.add(c); session.commit(); session.refresh(c)
    return CompetitionPublic(
        id=c.id, slug=c.slug, name=c.name, description=c.description,
        kind=c.kind, status=c.status, created_at=c.created_at,
        n_problems=0, n_submissions=0, n_models=0,
        harnesses_with_data=[],
        compatible_harness_slugs=compat,
    )


@app.put("/api/admin/competitions/{cid}", response_model=CompetitionPublic)
def edit_competition(
    cid: int, payload: CompetitionIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    c = session.get(Competition, cid)
    if c is None:
        raise HTTPException(404, "competition not found")
    if payload.slug != c.slug:
        clash = session.exec(
            select(Competition).where(Competition.slug == payload.slug)
        ).first()
        if clash and clash.id != cid:
            raise HTTPException(409, f"slug '{payload.slug}' already in use")
    compat = _validate_compat_slugs(session, payload.compatible_harness_slugs)
    data = payload.model_dump()
    data["compatible_harness_slugs"] = compat
    for k, v in data.items():
        setattr(c, k, v)
    session.add(c); session.commit(); session.refresh(c)
    member_pids = {
        m.problem_id for m in session.exec(
            select(CompetitionProblem).where(CompetitionProblem.competition_id == c.id)
        ).all()
    }
    n_subs, n_models, _l, _j = _competition_stats(session, member_pids)
    return CompetitionPublic(
        id=c.id, slug=c.slug, name=c.name, description=c.description,
        kind=c.kind, status=c.status, created_at=c.created_at,
        n_problems=len(member_pids), n_submissions=n_subs, n_models=n_models,
        harnesses_with_data=_competition_harnesses_with_data(session, c),
        compatible_harness_slugs=compat,
    )


@app.delete("/api/admin/competitions/{cid}",
            status_code=status.HTTP_204_NO_CONTENT)
def delete_competition(
    cid: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    c = session.get(Competition, cid)
    if c is None:
        raise HTTPException(404, "competition not found")
    # Clear membership rows so the FK doesn't dangle. Submissions are not
    # FK'd to competitions, so they are unaffected.
    for m in session.exec(
        select(CompetitionProblem).where(CompetitionProblem.competition_id == cid)
    ).all():
        session.delete(m)
    session.delete(c); session.commit()
    return None


@app.put("/api/admin/competitions/{cid}/problems",
         response_model=CompetitionPublic)
def set_competition_problems(
    cid: int, payload: CompetitionProblemsIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Replace this competition's problem membership with the given slug
    list. Unknown slugs are reported, not silently dropped."""
    c = session.get(Competition, cid)
    if c is None:
        raise HTTPException(404, "competition not found")
    slug_to_pid = {
        p.title_slug: p.id for p in session.exec(select(Problem)).all()
    }
    bad = [s for s in payload.problem_slugs if s not in slug_to_pid]
    if bad:
        raise HTTPException(400, {"detail": "unknown slugs", "errors": bad[:10]})
    # Wipe + reinsert. Tiny tables; not worth a diff.
    for m in session.exec(
        select(CompetitionProblem).where(CompetitionProblem.competition_id == cid)
    ).all():
        session.delete(m)
    for s in payload.problem_slugs:
        session.add(CompetitionProblem(competition_id=cid, problem_id=slug_to_pid[s]))
    session.commit()
    n_subs, n_models, _l, _j = _competition_stats(session, set(slug_to_pid[s] for s in payload.problem_slugs))
    return CompetitionPublic(
        id=c.id, slug=c.slug, name=c.name, description=c.description,
        kind=c.kind, status=c.status, created_at=c.created_at,
        n_problems=len(payload.problem_slugs),
        n_submissions=n_subs, n_models=n_models,
        harnesses_with_data=_competition_harnesses_with_data(session, c),
        compatible_harness_slugs=c.compatible_harness_slugs or [],
    )


# ---------- Harnesses ------------------------------------------------------
#
# A Harness is the perf oracle that scored a submission's runs — historically
# LeetCode's online judge, now pluggable so we can add an in-house harness
# without conflating outputs. Binding is layered:
#   * Competition.harness_id — default oracle for the contest
#   * Submission.harness_id  — per-submission override
#   * Run.harness_id         — records what actually judged this run
# Legacy rows were backfilled to the seeded `leetcode` harness at startup
# (see db._seed_default_harness), so "submissions on the leetcode harness"
# always includes pre-abstraction data.


def _resolve_submission_harness_id(sub: Submission,
                                   comp_by_id: dict[int, Competition]) -> int | None:
    """Effective harness for a submission: explicit override → competition
    default → NULL. Uses a preloaded competition map to avoid N+1 lookups
    when we walk many submissions at once."""
    if sub.harness_id is not None:
        return sub.harness_id
    if sub.competition_id is not None:
        c = comp_by_id.get(sub.competition_id)
        if c is not None:
            return c.harness_id
    return None


def _direct_agent_id(session: Session) -> int | None:
    """Id of the seeded "direct" agent — the producer default. None only if
    the seed has not run yet (first-boot race); callers then treat the
    producer as unknown."""
    row = session.exec(select(Agent.id).where(Agent.slug == "direct")).first()
    return int(row) if row is not None else None


def _resolve_submission_agent_id(sub: Submission,
                                 direct_agent_id: int | None) -> int | None:
    """Effective producer for a submission: its own agent_id, falling back to
    the seeded "direct" agent. There is no competition-level default on the
    producer axis (unlike harness), so this is just a NULL guard for a row
    created between the ADD COLUMN and the seed backfill."""
    return sub.agent_id if sub.agent_id is not None else direct_agent_id


def _harness_stats(
    session: Session, harness_id: int,
) -> tuple[int, int, int, list[str], list[Submission]]:
    """Cheap rollup over one harness. Returns (n_competitions, n_submissions,
    n_runs, languages_sorted, judged_subs). Submissions count via effective
    harness (competition default + submission override); n_runs counts Run
    rows tagged with this harness_id directly."""
    n_comps = len(session.exec(
        select(Competition).where(Competition.harness_id == harness_id)
    ).all())
    # Count in Python to match the rest of this file (no `func` import); the
    # /api/harnesses index card only calls this once per harness, and the run
    # table caps out in the low tens of thousands, so scanning is fine.
    n_runs = len(session.exec(
        select(Run.id).where(Run.harness_id == harness_id)
    ).all())
    # Compute effective-harness for every non-deleted submission — direct
    # override OR competition default.
    comps = {c.id: c for c in session.exec(select(Competition)).all()}
    subs = session.exec(
        select(Submission).where(Submission.deleted_at.is_(None))
    ).all()
    in_scope = [s for s in subs
                if _resolve_submission_harness_id(s, comps) == harness_id]
    judged = [s for s in in_scope if s.status == SubmissionStatus.judged]
    return (n_comps, len(in_scope), n_runs,
            sorted({s.language for s in in_scope}),
            judged)


@app.get("/api/machines", response_model=list[MachinePublic])
def list_machines(session: Session = Depends(get_session)):
    """List every measurement machine (the hardware a submission ran on), with a
    submission-count activity stat. Public — machines carry no secrets (unlike
    harness config), only hardware/OS/energy-tool provenance."""
    machines = session.exec(select(Machine).order_by(Machine.name)).all()
    # One grouped count instead of N queries.
    counts: dict[int, int] = {}
    for mid in session.exec(
        select(Submission.machine_id).where(Submission.machine_id.isnot(None))
    ).all():
        counts[mid] = counts.get(mid, 0) + 1
    return [
        MachinePublic(
            id=m.id, name=m.name, hostname=m.hostname, cpu_model=m.cpu_model,
            cores=m.cores, energy_source=m.energy_source, os=m.os, notes=m.notes,
            created_at=m.created_at, n_submissions=counts.get(m.id, 0),
        )
        for m in machines
    ]


@app.put("/api/admin/machines/{mid}", response_model=MachinePublic)
def edit_machine(
    mid: int, payload: MachineIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Admin: edit a machine's provenance metadata. Rows are auto-created by the
    runner with only a hostname; this lets an admin fill in the real CPU model,
    core count, energy tool, OS, and notes. Derived fields (id, created_at,
    n_submissions) aren't settable."""
    m = session.get(Machine, mid)
    if m is None:
        raise HTTPException(404, "machine not found")
    for k, v in payload.model_dump().items():
        setattr(m, k, v)
    session.add(m); session.commit(); session.refresh(m)
    n_subs = len(session.exec(
        select(Submission.id).where(Submission.machine_id == m.id)
    ).all())
    return MachinePublic(
        id=m.id, name=m.name, hostname=m.hostname, cpu_model=m.cpu_model,
        cores=m.cores, energy_source=m.energy_source, os=m.os, notes=m.notes,
        created_at=m.created_at, n_submissions=n_subs,
    )


# Config keys that carry secrets (runner auth tokens, credentials). Their VALUES
# are redacted for non-admins so a harness page never leaks the runner's token —
# the harness GET endpoints are public (no auth required to view a harness).
_SECRET_CONFIG_KEYS = {"auth_token", "token", "api_key", "apikey",
                       "password", "secret", "credential", "credentials"}


def _public_harness_config(config: dict | None, viewer: "User | None") -> dict | None:
    """Return the harness config safe to show `viewer`. Admins get it verbatim
    (they manage it via the edit form); everyone else gets secret VALUES masked
    with '***' (the key stays so the shape is visible, the secret is not)."""
    if not config or (viewer is not None and viewer.role == UserRole.admin):
        return config
    return {k: ("***" if k.lower() in _SECRET_CONFIG_KEYS else v)
            for k, v in config.items()}


def _agent_stats(
    session: Session, agent_id: int, direct_agent_id: int | None,
) -> tuple[int, int, list[str], list[Submission]]:
    """Rollup over one agent (producer). Returns (n_submissions, n_models,
    languages_sorted, judged_subs). A submission's effective agent is its own
    agent_id, falling back to the "direct" default, so the counts match what
    the leaderboard groups on."""
    subs = session.exec(
        select(Submission).where(Submission.deleted_at.is_(None))
    ).all()
    in_scope = [
        s for s in subs
        if _resolve_submission_agent_id(s, direct_agent_id) == agent_id
    ]
    n_models = len({s.model_name for s in in_scope})
    langs = sorted({s.language for s in in_scope})
    judged = [s for s in in_scope if s.status == SubmissionStatus.judged]
    return (len(in_scope), n_models, langs, judged)


@app.get("/api/agents", response_model=list[AgentPublic])
def list_agents(
    include_retired: bool = False,
    session: Session = Depends(get_session),
):
    """List every agent (producer) the platform tracks, with activity stats
    per row. Retired rows hidden unless `include_retired=true`. The seeded
    "direct" agent is always present — it is the producer of every one-shot,
    non-agentic submission."""
    direct_aid = _direct_agent_id(session)
    q = select(Agent).order_by(Agent.created_at.desc())
    if not include_retired:
        q = q.where(Agent.status == "active")
    out: list[AgentPublic] = []
    for a in session.exec(q).all():
        n_subs, n_models, _langs, _judged = _agent_stats(session, a.id, direct_aid)
        out.append(AgentPublic(
            id=a.id, slug=a.slug, name=a.name, description=a.description,
            kind=a.kind, version=a.version, status=a.status,
            created_at=a.created_at, config=a.config,
            n_submissions=n_subs, n_models=n_models,
        ))
    return out


@app.get("/api/agents/{slug}", response_model=AgentDetail)
def get_agent(slug: str, session: Session = Depends(get_session)):
    a = session.exec(select(Agent).where(Agent.slug == slug)).first()
    if a is None:
        raise HTTPException(404, "agent not found")
    direct_aid = _direct_agent_id(session)
    n_subs, n_models, langs, judged = _agent_stats(session, a.id, direct_aid)
    # Per-model rollup scoped to this agent's judged submissions — the same
    # shape HarnessDetail uses so the SPA can render a matching table.
    by_model: dict[tuple[str, str], list[Submission]] = {}
    for s in judged:
        by_model.setdefault((s.model_name, s.model_version), []).append(s)
    top: list[dict] = []
    for (name, ver), subs in by_model.items():
        pass1 = [s.pass_at_1 for s in subs if s.pass_at_1 is not None]
        rt = [s.avg_runtime_ms for s in subs if s.avg_runtime_ms is not None]
        mem = [s.avg_memory_mb for s in subs if s.avg_memory_mb is not None]
        top.append({
            "model_name": name,
            "model_version": ver,
            "best_pass_at_1": max(pass1) if pass1 else 0.0,
            "n_submissions": len(subs),
            "avg_runtime_ms": _mean_or_none(rt),
            "avg_memory_mb": _mean_or_none(mem),
        })
    top.sort(key=lambda r: -(r["best_pass_at_1"] or 0))
    return AgentDetail(
        id=a.id, slug=a.slug, name=a.name, description=a.description,
        kind=a.kind, version=a.version, status=a.status,
        created_at=a.created_at, config=a.config,
        n_submissions=n_subs, n_models=n_models,
        languages=langs, top_models=top[:5],
    )


@app.post("/api/admin/agents",
          response_model=AgentPublic,
          status_code=status.HTTP_201_CREATED)
def add_agent(
    payload: AgentIn,
    admin: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    if session.exec(select(Agent).where(Agent.slug == payload.slug)).first():
        raise HTTPException(409, f"slug '{payload.slug}' already in use")
    a = Agent(**payload.model_dump(), created_by_user_id=admin.id)
    session.add(a); session.commit(); session.refresh(a)
    return AgentPublic(
        id=a.id, slug=a.slug, name=a.name, description=a.description,
        kind=a.kind, version=a.version, status=a.status,
        created_at=a.created_at, config=a.config,
        n_submissions=0, n_models=0,
    )


@app.put("/api/admin/agents/{aid}", response_model=AgentPublic)
def edit_agent(
    aid: int, payload: AgentIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    a = session.get(Agent, aid)
    if a is None:
        raise HTTPException(404, "agent not found")
    if payload.slug != a.slug:
        clash = session.exec(select(Agent).where(Agent.slug == payload.slug)).first()
        if clash and clash.id != aid:
            raise HTTPException(409, f"slug '{payload.slug}' already in use")
    # The seeded "direct" agent is the backfill default for every legacy row;
    # renaming its slug would orphan that attribution, so pin it.
    if a.slug == "direct" and payload.slug != "direct":
        raise HTTPException(400, "the built-in 'direct' agent slug cannot be changed")
    for k, v in payload.model_dump().items():
        setattr(a, k, v)
    session.add(a); session.commit(); session.refresh(a)
    direct_aid = _direct_agent_id(session)
    n_subs, n_models, _l, _j = _agent_stats(session, a.id, direct_aid)
    return AgentPublic(
        id=a.id, slug=a.slug, name=a.name, description=a.description,
        kind=a.kind, version=a.version, status=a.status,
        created_at=a.created_at, config=a.config,
        n_submissions=n_subs, n_models=n_models,
    )


@app.get("/api/harnesses", response_model=list[HarnessPublic])
def list_harnesses(
    include_retired: bool = False,
    viewer: "User | None" = Depends(maybe_user),
    session: Session = Depends(get_session),
):
    """List every harness / perf oracle the platform tracks, with activity
    stats per row. Retired rows hidden unless `include_retired=true`."""
    q = select(Harness).order_by(Harness.created_at.desc())
    if not include_retired:
        q = q.where(Harness.status == "active")
    hs = session.exec(q).all()
    out: list[HarnessPublic] = []
    for h in hs:
        n_comps, n_subs, n_runs, _l, _j = _harness_stats(session, h.id)
        out.append(HarnessPublic(
            id=h.id, slug=h.slug, name=h.name,
            description=h.description, kind=h.kind, status=h.status,
            created_at=h.created_at, config=_public_harness_config(h.config, viewer),
            n_competitions=n_comps, n_submissions=n_subs, n_runs=n_runs,
        ))
    return out


@app.get("/api/harnesses/{slug}", response_model=HarnessDetail)
def get_harness(slug: str,
                viewer: "User | None" = Depends(maybe_user),
                session: Session = Depends(get_session)):
    h = session.exec(select(Harness).where(Harness.slug == slug)).first()
    if h is None:
        raise HTTPException(404, "harness not found")
    n_comps, n_subs, n_runs, langs, judged = _harness_stats(session, h.id)

    comps = session.exec(
        select(Competition).where(Competition.harness_id == h.id)
        .order_by(Competition.name)
    ).all()

    # Per-model leaderboard scoped to submissions on this harness. Uses the
    # submission's own pass_at_1 (the harness applies uniformly across every
    # problem the submission touched, so no per-competition rescoring like
    # get_competition needs to do).
    by_model: dict[tuple[str, str], list[Submission]] = {}
    for s in judged:
        by_model.setdefault((s.model_name, s.model_version), []).append(s)
    top: list[dict] = []
    for (name, ver), subs in by_model.items():
        pass1 = [s.pass_at_1 for s in subs if s.pass_at_1 is not None]
        passk = [s.pass_at_k for s in subs if s.pass_at_k is not None]
        rt = [s.avg_runtime_ms for s in subs if s.avg_runtime_ms is not None]
        mem = [s.avg_memory_mb for s in subs if s.avg_memory_mb is not None]
        rtp = [s.avg_runtime_percentile for s in subs
               if s.avg_runtime_percentile is not None]
        memp = [s.avg_memory_percentile for s in subs
                if s.avg_memory_percentile is not None]
        top.append({
            "model_name": name,
            "model_version": ver,
            "best_pass_at_1": max(pass1) if pass1 else 0.0,
            "pass_at_k": max(passk) if passk else None,
            "n_submissions": len(subs),
            "avg_runtime_ms": _mean_or_none(rt),
            "avg_memory_mb": _mean_or_none(mem),
            "avg_runtime_percentile": _mean_or_none(rtp),
            "avg_memory_percentile": _mean_or_none(memp),
        })
    top.sort(key=lambda r: (
        -(r["best_pass_at_1"] or 0),
        -(r.get("avg_runtime_percentile") or 0),
        -(r.get("avg_memory_percentile") or 0),
    ))

    return HarnessDetail(
        id=h.id, slug=h.slug, name=h.name,
        description=h.description, kind=h.kind, status=h.status,
        created_at=h.created_at, config=_public_harness_config(h.config, viewer),
        n_competitions=n_comps, n_submissions=n_subs, n_runs=n_runs,
        competitions=[CompetitionRef(slug=c.slug, name=c.name) for c in comps],
        languages=langs,
        top_models=top[:5],
    )


@app.post("/api/admin/harnesses",
          response_model=HarnessPublic,
          status_code=status.HTTP_201_CREATED)
def add_harness(
    payload: HarnessIn,
    admin: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    if session.exec(select(Harness).where(Harness.slug == payload.slug)).first():
        raise HTTPException(409, f"slug '{payload.slug}' already in use")
    h = Harness(**payload.model_dump(), created_by_user_id=admin.id)
    session.add(h); session.commit(); session.refresh(h)
    return HarnessPublic(
        id=h.id, slug=h.slug, name=h.name, description=h.description,
        kind=h.kind, status=h.status, created_at=h.created_at, config=h.config,
        n_competitions=0, n_submissions=0, n_runs=0,
    )


@app.put("/api/admin/harnesses/{hid}", response_model=HarnessPublic)
def edit_harness(
    hid: int, payload: HarnessIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    h = session.get(Harness, hid)
    if h is None:
        raise HTTPException(404, "harness not found")
    if payload.slug != h.slug:
        clash = session.exec(select(Harness).where(Harness.slug == payload.slug)).first()
        if clash and clash.id != hid:
            raise HTTPException(409, f"slug '{payload.slug}' already in use")
    for k, v in payload.model_dump().items():
        setattr(h, k, v)
    session.add(h); session.commit(); session.refresh(h)
    n_comps, n_subs, n_runs, _l, _j = _harness_stats(session, h.id)
    return HarnessPublic(
        id=h.id, slug=h.slug, name=h.name, description=h.description,
        kind=h.kind, status=h.status, created_at=h.created_at, config=h.config,
        n_competitions=n_comps, n_submissions=n_subs, n_runs=n_runs,
    )


@app.post("/api/admin/harnesses/{hid}/probe")
def probe_harness(
    hid: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Ping the remote runner's /healthz. Only meaningful for arena-local
    / custom harnesses that carry a config.endpoint. LeetCode-kind
    harnesses have their own auth check on the dispatcher's pre-flight
    (via app.dispatch._check_leetcode_auth) and return `not_applicable`.
    """
    h = session.get(Harness, hid)
    if h is None:
        raise HTTPException(404, "harness not found")
    if h.kind == "leetcode":
        return {
            "ok": False,
            "not_applicable": True,
            "message": (
                "LeetCode harnesses don't have a runner endpoint — "
                "credential pool health lives on /admin/leetcode-accounts."
            ),
        }
    if not (h.config or {}).get("endpoint"):
        return {
            "ok": False,
            "message": (
                "config.endpoint isn't set on this harness — nothing to probe. "
                "Edit the harness and set { \"endpoint\": \"http://...\" } first."
            ),
        }
    from . import arena_local_runner as _arena
    ok, info = _arena.probe_healthz(h)
    return {"ok": ok, "message": info}


@app.get("/api/admin/harnesses/health")
def admin_harnesses_health(
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Probe EVERY harness's runner /healthz so an operator (and the Variables
    tab) sees at a glance which harnesses are reachable. LeetCode harnesses have
    no runner endpoint and report status 'n/a'."""
    from . import arena_local_runner as _arena
    out = []
    for h in session.exec(select(Harness).order_by(Harness.slug)).all():
        cfg = h.config or {}
        row = {"id": h.id, "slug": h.slug, "name": h.name, "kind": h.kind,
               "endpoint": cfg.get("endpoint")}
        if h.kind == "leetcode":
            row.update(ok=None, status="n/a",
                       message="LeetCode harness has no runner endpoint")
        elif not cfg.get("endpoint"):
            row.update(ok=False, status="unconfigured",
                       message="config.endpoint not set")
        else:
            try:
                ok, info = _arena.probe_healthz(h)
            except Exception as e:  # noqa: BLE001
                ok, info = False, f"probe error: {type(e).__name__}: {e}"
            row.update(ok=bool(ok), status=("up" if ok else "down"),
                       message=str(info)[:300])
        out.append(row)
    return {"harnesses": out}


@app.get("/api/admin/integrity/speed-outliers")
def admin_speed_outliers(
    factor: float = 8.0,
    min_peers: int = 3,
    min_median_ms: float = 20.0,
    min_runtime_ms: float = 1.0,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Accepted attempts that ran >= `factor`x faster than the median of the
    other accepted solutions for the same (problem, language, harness) — a review
    signal for solutions that skipped the benchmark's intended work (e.g. a
    closed-form CLBG checksum instead of actually allocating). Needs >=
    `min_peers` peers and a peer median of >= `min_median_ms` to form a baseline,
    and ignores attempts below `min_runtime_ms` (below measurement resolution, so
    per-op times rounding to ~0 don't flood the list). Not an auto-reject: a
    genuine optimization can also land here, so each row is for a human to judge."""
    from . import integrity
    rows = integrity.speed_outliers(
        session, factor=factor, min_peers=min_peers,
        min_median_ms=min_median_ms, min_runtime_ms=min_runtime_ms)
    return {"factor": factor, "min_peers": min_peers,
            "min_median_ms": min_median_ms, "min_runtime_ms": min_runtime_ms,
            "count": len(rows), "flagged": rows}


@app.get("/api/admin/settings")
def admin_get_settings(_: User = Depends(current_admin)):
    """Runtime-editable settings: registry + current effective value + source."""
    from . import app_settings
    return {"settings": app_settings.all_effective()}


@app.put("/api/admin/settings")
def admin_put_settings(
    updates: list[dict],
    user: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Set or clear runtime overrides. Body: [{"key": ..., "value": ...}]. A
    null/missing value clears the override (reverts to env/default)."""
    from . import app_settings
    results = []
    for u in updates:
        key = u.get("key")
        try:
            eff = app_settings.set_value(session, key, u.get("value"),
                                         user_id=user.id)
            results.append({"key": key, "ok": True, "value": eff})
        except (ValueError, KeyError) as e:
            results.append({"key": key, "ok": False, "error": str(e)})
    return {"results": results}


@app.delete("/api/admin/harnesses/{hid}",
            status_code=status.HTTP_204_NO_CONTENT)
def delete_harness(
    hid: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    h = session.get(Harness, hid)
    if h is None:
        raise HTTPException(404, "harness not found")
    # Refuse to delete a harness that still has rows pointing at it — we
    # don't want silent NULLs cascading through Competition / Submission /
    # Run and orphaning existing leaderboard scopes. Operator can null out
    # or reassign first.
    n_comps, n_subs, n_runs, _l, _j = _harness_stats(session, hid)
    if n_comps or n_subs or n_runs:
        raise HTTPException(409, {
            "detail": "harness is in use — reassign before deleting",
            "n_competitions": n_comps,
            "n_submissions": n_subs,
            "n_runs": n_runs,
        })
    session.delete(h); session.commit()
    return None


# ---------- Correctness oracles --------------------------------------------
#
# The correctness half of the two-oracle model (see models.CorrectnessOracle
# / models.Harness). Same 5-endpoint shape as harnesses; the layered binding
# uses the parallel `correctness_oracle_id` FK on Competition / Submission /
# Run. Legacy rows are backfilled to the seeded `leetcode` correctness
# oracle at startup so filtering by `?correctness_oracle=leetcode` includes
# every pre-abstraction submission.


def _resolve_submission_correctness_oracle_id(
    sub: Submission, comp_by_id: dict[int, Competition],
) -> int | None:
    """Effective correctness oracle for a submission: explicit override →
    competition default → NULL. Mirror of _resolve_submission_harness_id
    for the correctness half."""
    if sub.correctness_oracle_id is not None:
        return sub.correctness_oracle_id
    if sub.competition_id is not None:
        c = comp_by_id.get(sub.competition_id)
        if c is not None:
            return c.correctness_oracle_id
    return None


def _correctness_oracle_stats(
    session: Session, oracle_id: int,
) -> tuple[int, int, int, list[str], list[Submission]]:
    """Same rollup shape as _harness_stats — (n_competitions, n_submissions,
    n_runs, languages_sorted, judged_subs) for one correctness oracle."""
    n_comps = len(session.exec(
        select(Competition).where(Competition.correctness_oracle_id == oracle_id)
    ).all())
    n_runs = len(session.exec(
        select(Run.id).where(Run.correctness_oracle_id == oracle_id)
    ).all())
    comps = {c.id: c for c in session.exec(select(Competition)).all()}
    subs = session.exec(
        select(Submission).where(Submission.deleted_at.is_(None))
    ).all()
    in_scope = [s for s in subs
                if _resolve_submission_correctness_oracle_id(s, comps) == oracle_id]
    judged = [s for s in in_scope if s.status == SubmissionStatus.judged]
    return (n_comps, len(in_scope), n_runs,
            sorted({s.language for s in in_scope}),
            judged)


@app.get("/api/correctness-oracles", response_model=list[CorrectnessOraclePublic])
def list_correctness_oracles(
    include_retired: bool = False,
    viewer: "User | None" = Depends(maybe_user),
    session: Session = Depends(get_session),
):
    """List every correctness oracle the platform tracks, with activity
    stats per row. Retired rows hidden unless `include_retired=true`."""
    q = select(CorrectnessOracle).order_by(CorrectnessOracle.created_at.desc())
    if not include_retired:
        q = q.where(CorrectnessOracle.status == "active")
    rows = session.exec(q).all()
    out: list[CorrectnessOraclePublic] = []
    for o in rows:
        n_comps, n_subs, n_runs, _l, _j = _correctness_oracle_stats(session, o.id)
        out.append(CorrectnessOraclePublic(
            id=o.id, slug=o.slug, name=o.name,
            description=o.description, kind=o.kind, status=o.status,
            created_at=o.created_at, config=_public_harness_config(o.config, viewer),
            n_competitions=n_comps, n_submissions=n_subs, n_runs=n_runs,
        ))
    return out


@app.get("/api/correctness-oracles/{slug}", response_model=CorrectnessOracleDetail)
def get_correctness_oracle(slug: str,
                           viewer: "User | None" = Depends(maybe_user),
                           session: Session = Depends(get_session)):
    o = session.exec(
        select(CorrectnessOracle).where(CorrectnessOracle.slug == slug)
    ).first()
    if o is None:
        raise HTTPException(404, "correctness oracle not found")
    n_comps, n_subs, n_runs, langs, judged = _correctness_oracle_stats(session, o.id)
    comps = session.exec(
        select(Competition).where(Competition.correctness_oracle_id == o.id)
        .order_by(Competition.name)
    ).all()

    # Per-model leaderboard over submissions judged by this oracle.
    by_model: dict[tuple[str, str], list[Submission]] = {}
    for s in judged:
        by_model.setdefault((s.model_name, s.model_version), []).append(s)
    top: list[dict] = []
    for (name, ver), subs in by_model.items():
        pass1 = [s.pass_at_1 for s in subs if s.pass_at_1 is not None]
        passk = [s.pass_at_k for s in subs if s.pass_at_k is not None]
        rt = [s.avg_runtime_ms for s in subs if s.avg_runtime_ms is not None]
        mem = [s.avg_memory_mb for s in subs if s.avg_memory_mb is not None]
        rtp = [s.avg_runtime_percentile for s in subs
               if s.avg_runtime_percentile is not None]
        memp = [s.avg_memory_percentile for s in subs
                if s.avg_memory_percentile is not None]
        top.append({
            "model_name": name,
            "model_version": ver,
            "best_pass_at_1": max(pass1) if pass1 else 0.0,
            "pass_at_k": max(passk) if passk else None,
            "n_submissions": len(subs),
            "avg_runtime_ms": _mean_or_none(rt),
            "avg_memory_mb": _mean_or_none(mem),
            "avg_runtime_percentile": _mean_or_none(rtp),
            "avg_memory_percentile": _mean_or_none(memp),
        })
    top.sort(key=lambda r: (
        -(r["best_pass_at_1"] or 0),
        -(r.get("avg_runtime_percentile") or 0),
        -(r.get("avg_memory_percentile") or 0),
    ))

    return CorrectnessOracleDetail(
        id=o.id, slug=o.slug, name=o.name,
        description=o.description, kind=o.kind, status=o.status,
        created_at=o.created_at, config=_public_harness_config(o.config, viewer),
        n_competitions=n_comps, n_submissions=n_subs, n_runs=n_runs,
        competitions=[CompetitionRef(slug=c.slug, name=c.name) for c in comps],
        languages=langs,
        top_models=top[:5],
    )


@app.post("/api/admin/correctness-oracles",
          response_model=CorrectnessOraclePublic,
          status_code=status.HTTP_201_CREATED)
def add_correctness_oracle(
    payload: CorrectnessOracleIn,
    admin: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    if session.exec(select(CorrectnessOracle).where(
            CorrectnessOracle.slug == payload.slug)).first():
        raise HTTPException(409, f"slug '{payload.slug}' already in use")
    o = CorrectnessOracle(**payload.model_dump(), created_by_user_id=admin.id)
    session.add(o); session.commit(); session.refresh(o)
    return CorrectnessOraclePublic(
        id=o.id, slug=o.slug, name=o.name, description=o.description,
        kind=o.kind, status=o.status, created_at=o.created_at, config=o.config,
        n_competitions=0, n_submissions=0, n_runs=0,
    )


@app.put("/api/admin/correctness-oracles/{oid}",
         response_model=CorrectnessOraclePublic)
def edit_correctness_oracle(
    oid: int, payload: CorrectnessOracleIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    o = session.get(CorrectnessOracle, oid)
    if o is None:
        raise HTTPException(404, "correctness oracle not found")
    if payload.slug != o.slug:
        clash = session.exec(
            select(CorrectnessOracle).where(CorrectnessOracle.slug == payload.slug)
        ).first()
        if clash and clash.id != oid:
            raise HTTPException(409, f"slug '{payload.slug}' already in use")
    for k, v in payload.model_dump().items():
        setattr(o, k, v)
    session.add(o); session.commit(); session.refresh(o)
    n_comps, n_subs, n_runs, _l, _j = _correctness_oracle_stats(session, o.id)
    return CorrectnessOraclePublic(
        id=o.id, slug=o.slug, name=o.name, description=o.description,
        kind=o.kind, status=o.status, created_at=o.created_at, config=o.config,
        n_competitions=n_comps, n_submissions=n_subs, n_runs=n_runs,
    )


@app.delete("/api/admin/correctness-oracles/{oid}",
            status_code=status.HTTP_204_NO_CONTENT)
def delete_correctness_oracle(
    oid: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    o = session.get(CorrectnessOracle, oid)
    if o is None:
        raise HTTPException(404, "correctness oracle not found")
    # Same safety net as delete_harness — refuse when rows still reference
    # this oracle so an admin doesn't accidentally orphan submission /
    # run rows into a dangling FK.
    n_comps, n_subs, n_runs, _l, _j = _correctness_oracle_stats(session, oid)
    if n_comps or n_subs or n_runs:
        raise HTTPException(409, {
            "detail": "correctness oracle is in use — reassign before deleting",
            "n_competitions": n_comps,
            "n_submissions": n_subs,
            "n_runs": n_runs,
        })
    session.delete(o); session.commit()
    return None


# ---------- Blog ------------------------------------------------------------
#
# Lightweight content surface — admins author markdown posts; the /blog page
# renders them as cards. Public listings include only `status=='published'`
# rows; drafts and retired rows are admin-only via the dedicated lookup.

@app.get("/api/blog", response_model=list[BlogPostCard])
def list_blog_posts(session: Session = Depends(get_session)):
    """Public blog index — published posts only, freshest first. Admins use
    `/api/admin/blog` (below) to see drafts + retired."""
    q = (select(BlogPost)
         .where(BlogPost.status == "published")
         .order_by(BlogPost.published_at.desc(), BlogPost.created_at.desc()))
    return session.exec(q).all()


@app.get("/api/blog/{slug}", response_model=BlogPostPublic)
def get_blog_post(slug: str, session: Session = Depends(get_session)):
    p = session.exec(select(BlogPost).where(BlogPost.slug == slug)).first()
    if p is None or p.status != "published":
        raise HTTPException(404, "blog post not found")
    return p


@app.get("/api/admin/blog", response_model=list[BlogPostPublic])
def admin_list_blog(
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Admin view — all posts regardless of status. Used by the admin form
    to find drafts/retired posts the public list hides."""
    return session.exec(
        select(BlogPost).order_by(BlogPost.created_at.desc())
    ).all()


@app.post("/api/admin/blog",
          response_model=BlogPostPublic,
          status_code=status.HTTP_201_CREATED)
def add_blog_post(
    payload: BlogPostIn,
    admin: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    if session.exec(select(BlogPost).where(BlogPost.slug == payload.slug)).first():
        raise HTTPException(409, f"slug '{payload.slug}' already in use")
    now = datetime.utcnow()
    p = BlogPost(
        **payload.model_dump(),
        created_by_user_id=admin.id,
        published_at=now if payload.status == "published" else None,
        updated_at=now,
    )
    session.add(p); session.commit(); session.refresh(p)
    return p


@app.put("/api/admin/blog/{pid}", response_model=BlogPostPublic)
def edit_blog_post(
    pid: int, payload: BlogPostIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    p = session.get(BlogPost, pid)
    if p is None:
        raise HTTPException(404, "blog post not found")
    if payload.slug != p.slug:
        clash = session.exec(
            select(BlogPost).where(BlogPost.slug == payload.slug)
        ).first()
        if clash and clash.id != pid:
            raise HTTPException(409, f"slug '{payload.slug}' already in use")
    was_published = p.status == "published"
    for k, v in payload.model_dump().items():
        setattr(p, k, v)
    # Stamp published_at the first time it flips to 'published'; don't touch
    # the timestamp on subsequent edits so the original post date sticks.
    if p.status == "published" and not was_published and p.published_at is None:
        p.published_at = datetime.utcnow()
    p.updated_at = datetime.utcnow()
    session.add(p); session.commit(); session.refresh(p)
    return p


@app.delete("/api/admin/blog/{pid}",
            status_code=status.HTTP_204_NO_CONTENT)
def delete_blog_post(
    pid: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    p = session.get(BlogPost, pid)
    if p is None:
        raise HTTPException(404, "blog post not found")
    session.delete(p); session.commit()
    return None


@app.get("/api/models", response_model=list[ModelListEntry])
def list_models(session: Session = Depends(get_session)):
    """Distinct model names that have any non-deleted submission. Used by the
    comparison-page picker; ordered by submission count desc."""
    by_name: dict[str, dict] = {}
    for s in session.exec(
        select(Submission).where(Submission.deleted_at.is_(None))
    ).all():
        e = by_name.setdefault(s.model_name, {
            "name": s.model_name, "n_submissions": 0, "languages": set(),
        })
        e["n_submissions"] += 1
        e["languages"].add(s.language)
    return [
        ModelListEntry(
            name=e["name"], n_submissions=e["n_submissions"],
            languages=sorted(e["languages"]),
        )
        for e in sorted(by_name.values(), key=lambda x: (-x["n_submissions"], x["name"]))
    ]


def _competition_problem_slugs(session: Session, comp_slug: str) -> set[str] | None:
    """Resolve a competition slug to the title_slugs of its problems.

    Returns None when the slug matches no competition (callers treat that as
    "no competition scope"); an empty set when the competition exists but has
    no problems linked. Used to scope the compare endpoints to one problem set.
    """
    comp = session.exec(
        select(Competition).where(Competition.slug == comp_slug)
    ).first()
    if comp is None:
        return None
    pids = [
        cp.problem_id for cp in session.exec(
            select(CompetitionProblem).where(
                CompetitionProblem.competition_id == comp.id
            )
        ).all()
    ]
    if not pids:
        return set()
    return set(session.exec(
        select(Problem.title_slug).where(Problem.id.in_(pids))
    ).all())


# Octoverse-2025 language order (most to least used on GitHub), matching the
# paper's per-language figures. Only the six measured languages.
_EFF_LANGS = ["typescript", "python3", "java", "csharp", "php", "cpp"]
_EFF_METRICS = [("latency", "best_runtime_ms"), ("energy", "best_runtime_energy_j")]


@app.get("/api/efficiency-ranks")
def efficiency_ranks(competition: str, session: Session = Depends(get_session)):
    """Per-language model rankings by latency and energy for one competition,
    scored as the MEDIAN RATIO to the best correct solution on each problem (the
    same metric as the paper's per-language efficiency tables). This drives the
    home-page bump charts. Within each language, models are ordered lowest ratio
    (fastest / most efficient) first. Energy is omitted for a language when no
    model has an energy measurement there (LeetCode-judged rows carry none)."""
    import statistics
    from collections import defaultdict

    comp = session.exec(
        select(Competition).where(Competition.slug == competition)
    ).first()
    if comp is None:
        raise HTTPException(404, "competition not found")
    comp_pids = {
        cp.problem_id for cp in session.exec(
            select(CompetitionProblem).where(CompetitionProblem.competition_id == comp.id)
        ).all()
    }
    empty = {"competition": competition, "languages": [],
             "metrics": [m for m, _ in _EFF_METRICS], "ranks": {}}
    if not comp_pids:
        return empty

    subs = session.exec(
        select(Submission).where(
            Submission.competition_id == comp.id,
            Submission.status == SubmissionStatus.judged,
            Submission.deleted_at.is_(None),
            Submission.language.in_(_EFF_LANGS),
        )
    ).all()
    if not subs:
        return empty
    sub_meta = {s.id: (s.model_name, s.language) for s in subs}

    attempts = session.exec(
        select(Attempt).where(Attempt.submission_id.in_(list(sub_meta)))
    ).all()
    # Best ACCEPTED attempt per (language, problem, model): the fastest one, by
    # best_runtime_ms. Its energy comes from the same attempt so the two metrics
    # are read off one solution, mirroring /api/compare's per-problem best.
    best: dict[tuple, Attempt] = {}
    for a in attempts:
        if a.final_status != "Accepted" or a.problem_id not in comp_pids:
            continue
        model, lang = sub_meta[a.submission_id]
        key = (lang, a.problem_id, model)
        cur = best.get(key)
        if cur is None or (a.best_runtime_ms or 1e18) < (cur.best_runtime_ms or 1e18):
            best[key] = a

    ranks: dict[str, dict] = {}
    langs_present: set[str] = set()
    for metric, field in _EFF_METRICS:
        # (lang, pid) -> {model: value}
        by_problem: dict[tuple, dict] = defaultdict(dict)
        for (lang, pid, model), a in best.items():
            v = getattr(a, field)
            if v is not None and v > 0:
                by_problem[(lang, pid)][model] = v
        # ratio to the per-problem best, then median per (lang, model)
        lang_model_ratios: dict[str, dict] = defaultdict(lambda: defaultdict(list))
        for (lang, _pid), mv in by_problem.items():
            best_v = min(mv.values())
            if best_v <= 0:
                continue
            for model, v in mv.items():
                lang_model_ratios[lang][model].append(v / best_v)
        out: dict[str, list] = {}
        for lang, mr in lang_model_ratios.items():
            ranked = sorted(
                ((model, statistics.median(rs)) for model, rs in mr.items() if rs),
                key=lambda t: t[1],
            )
            if ranked:
                out[lang] = [{"model": m, "ratio": round(r, 3)} for m, r in ranked]
                langs_present.add(lang)
        ranks[metric] = out

    return {
        "competition": competition,
        "languages": [l for l in _EFF_LANGS if l in langs_present],
        "metrics": [m for m, _ in _EFF_METRICS],
        "ranks": ranks,
    }


@app.get("/api/compare", response_model=CompareResponse)
def compare_models(
    models: list[str] = Query(..., min_length=2,
                              description="repeat ?models=NAME for each model to compare"),
    language: str | None = None,
    prompt_id: int | None = Query(None, description="restrict to one PromptTemplate"),
    problems: str | None = Query(
        None, description="comma-separated problem slugs to restrict to"),
    competition: str | None = Query(
        None, description="restrict to a competition's problem set (slug)"),
    session: Session = Depends(get_session),
):
    """Head-to-head model comparison. Aggregates each model's judged
    submissions (optionally filtered by language, a problem subset, and/or a
    competition's problem set) into one summary row + a per-problem matrix
    (where every requested problem shows what each model achieved)."""
    wanted_slugs: set[str] | None = None
    if problems:
        wanted_slugs = {s.strip() for s in problems.split(",") if s.strip()}
    if competition:
        comp_slugs = _competition_problem_slugs(session, competition)
        if comp_slugs is not None:
            # Intersect so a competition + explicit problem subset narrows to
            # the overlap; competition alone restricts to its whole problem set.
            wanted_slugs = comp_slugs if wanted_slugs is None else (wanted_slugs & comp_slugs)

    # All judged, non-deleted submissions for the requested models.
    q = (
        select(Submission)
        .where(Submission.model_name.in_(models))
        .where(Submission.status == SubmissionStatus.judged)
        .where(Submission.deleted_at.is_(None))
    )
    if language:
        q = q.where(Submission.language == language)
    if prompt_id is not None:
        q = q.where(Submission.prompt_id == prompt_id)
    subs = session.exec(q).all()

    by_model: dict[str, list[Submission]] = {}
    for s in subs:
        by_model.setdefault(s.model_name, []).append(s)

    # Problems we'll report on the per-problem matrix: intersection of
    # `wanted_slugs` (if given) with everything any model attempted.
    all_problems = {p.id: p for p in session.exec(select(Problem)).all()}
    slug_to_id = {p.title_slug: pid for pid, p in all_problems.items()}

    summary: list[CompareSummaryRow] = []
    by_level_rows: list[CompareByLevelRow] = []
    # Per-(model, tag) → {n_attempted, n_accepted, avg_rt[], avg_mem[]}.
    # Filled inside the same per-problem loop we already walk for
    # by_level so we don't pay a second pass. Tags come from
    # Problem.tags (comma-separated string the parquet stores them in).
    by_tag_acc: dict[tuple[str, str], dict] = {}
    # Cache per (model, problem) → "best attempt" (accepted-first, then fastest).
    best_atts: dict[tuple[str, int], Attempt] = {}
    for model_name in models:
        model_subs = by_model.get(model_name, [])
        if not model_subs:
            summary.append(CompareSummaryRow(
                model_name=model_name, n_submissions=0,
                n_problems_accepted=0, n_problems_attempted=0,
                languages=[],
                pass_at_1_max=None, pass_at_k_max=None, pass_at_full_max=None,
                avg_runtime_ms=None, best_runtime_ms=None,
                avg_memory_mb=None, best_memory_mb=None,
                avg_runtime_percentile=None, avg_memory_percentile=None,
                time_human_avg=None, time_human_expert=None,
                mem_human_avg=None, mem_human_expert=None,
                leading_submission_id=None,
            ))
            continue

        attempts = session.exec(
            select(Attempt).where(Attempt.submission_id.in_([s.id for s in model_subs]))
        ).all()
        # Group by problem_id; restrict to the requested slug subset (if any).
        attempted_pids: set[int] = set()
        by_pid: dict[int, list[Attempt]] = {}
        for a in attempts:
            if wanted_slugs is not None:
                p = all_problems.get(a.problem_id)
                if p is None or p.title_slug not in wanted_slugs:
                    continue
            attempted_pids.add(a.problem_id)
            by_pid.setdefault(a.problem_id, []).append(a)

        avg_rt, best_rt, avg_mem, best_mem = [], [], [], []
        avg_rtp, avg_memp = [], []
        t_h_avg, t_h_exp, m_h_avg, m_h_exp = [], [], [], []
        n_accepted = 0
        # Per-level breakdown buckets. We tally attempted/accepted counts
        # PER LEVEL so the SPA can chart Easy/Medium/Hard side-by-side,
        # not just the all-levels aggregate. Mirrors the overall lists
        # above but keyed by Problem.level. Models that touched only some
        # levels get 0-attempted entries for the others below (the SPA
        # still needs a cell to render the bar at zero).
        BY_LEVEL = ("Easy", "Medium", "Hard")
        per_level: dict[str, dict] = {
            lvl: {
                "n_attempted": 0, "n_accepted": 0,
                "avg_rt": [], "avg_mem": [],
                "avg_rtp": [], "avg_memp": [],
                # generator-side accumulators — summed across ALL attempts
                # for the problem, then averaged across problems below.
                # Costs etc. are per-attempt LLM spend, so a model that
                # retries 3 times for one problem at level X spends 3×
                # the per-call cost on that problem. Honest to roll that
                # up rather than picking the cheapest attempt.
                "cost_per_problem": [],
                "tokens_per_problem": [],
                "wall_per_problem": [],
            }
            for lvl in BY_LEVEL
        }
        for pid, atts in by_pid.items():
            problem = all_problems.get(pid)
            lvl = (problem.level if problem else "") or ""
            bucket = per_level.get(lvl)
            if bucket is not None:
                bucket["n_attempted"] += 1
                # Sum generator-side spend across every attempt for this
                # problem, then push the per-problem total as a single
                # data point. None entries are skipped so a partially-
                # instrumented submission doesn't lie about the average.
                costs = [a.cost_usd for a in atts if a.cost_usd is not None]
                if costs:
                    bucket["cost_per_problem"].append(sum(costs))
                toks = [a.response_tokens for a in atts if a.response_tokens is not None]
                if toks:
                    bucket["tokens_per_problem"].append(sum(toks))
                wcs = [a.wall_clock_ms for a in atts if a.wall_clock_ms is not None]
                if wcs:
                    bucket["wall_per_problem"].append(sum(wcs))
            # Per-tag attempted-count tally — runs for EVERY problem this
            # model touched, accept-status independent. The accepted +
            # runtime/memory tallies happen later inside the accepted branch
            # (we don't have perf numbers for failed attempts).
            problem_tags = [
                t.strip() for t in ((problem.tags if problem else "") or "").split(",")
                if t.strip()
            ]
            for tag in problem_tags:
                cell = by_tag_acc.setdefault((model_name, tag), {
                    "n_attempted": 0, "n_accepted": 0,
                    "avg_rt": [], "avg_mem": [],
                })
                cell["n_attempted"] += 1
            accepted = [a for a in atts if a.final_status == "Accepted"]
            if accepted:
                best = max(accepted, key=lambda a: (
                    (a.n_accepted_runs / a.n_runs) if a.n_runs else 0.0,
                    -(a.avg_runtime_ms if a.avg_runtime_ms is not None else 1e18),
                ))
                n_accepted += 1
                if bucket is not None:
                    bucket["n_accepted"] += 1
            else:
                best = max(atts, key=lambda a: a.id)
            best_atts[(model_name, pid)] = best
            if best.final_status != "Accepted":
                continue
            if best.avg_runtime_ms is not None:    avg_rt.append(best.avg_runtime_ms)
            if best.best_runtime_ms is not None:   best_rt.append(best.best_runtime_ms)
            if best.avg_memory_mb is not None:     avg_mem.append(best.avg_memory_mb)
            if best.best_memory_mb is not None:    best_mem.append(best.best_memory_mb)
            if best.avg_runtime_percentile is not None: avg_rtp.append(best.avg_runtime_percentile)
            # Per-level perf accumulators — only over accepted attempts so
            # the runtime/memory averages aren't dragged by failed code.
            if bucket is not None and best.avg_runtime_ms is not None:
                bucket["avg_rt"].append(best.avg_runtime_ms)
            if bucket is not None and best.avg_memory_mb is not None:
                bucket["avg_mem"].append(best.avg_memory_mb)
            if bucket is not None and best.avg_runtime_percentile is not None:
                bucket["avg_rtp"].append(best.avg_runtime_percentile)
            if bucket is not None and best.avg_memory_percentile is not None:
                bucket["avg_memp"].append(best.avg_memory_percentile)
            if best.avg_memory_percentile is not None:  avg_memp.append(best.avg_memory_percentile)
            # Per-tag accepted + perf tallies. n_attempted was already
            # bumped earlier in the loop regardless of acceptance; here
            # we just bump n_accepted and push runtime/memory from the
            # winning attempt for each tag the problem carries.
            for tag in problem_tags:
                cell = by_tag_acc[(model_name, tag)]
                cell["n_accepted"] += 1
                if best.avg_runtime_ms is not None:
                    cell["avg_rt"].append(best.avg_runtime_ms)
                if best.avg_memory_mb is not None:
                    cell["avg_mem"].append(best.avg_memory_mb)

            sub_of_best = next((s for s in model_subs if s.id == best.submission_id), None)
            baseline = session.exec(
                select(Baseline).where(
                    Baseline.problem_id == pid,
                    Baseline.language == (sub_of_best.language if sub_of_best else None),
                )
            ).first()
            if baseline and best.avg_runtime_ms and best.avg_runtime_ms > 0:
                t_h_avg.append(baseline.runtime_ms_avg / best.avg_runtime_ms)
                t_h_exp.append(baseline.runtime_ms_expert / best.avg_runtime_ms)
            if baseline and best.avg_memory_mb and best.avg_memory_mb > 0:
                m_h_avg.append(baseline.memory_mb_avg / best.avg_memory_mb)
                m_h_exp.append(baseline.memory_mb_expert / best.avg_memory_mb)

        leading = max(model_subs,
                      key=lambda s: (s.pass_at_1 or 0.0, s.avg_runtime_percentile or 0.0))
        summary.append(CompareSummaryRow(
            model_name=model_name, n_submissions=len(model_subs),
            n_problems_accepted=n_accepted,
            n_problems_attempted=len(attempted_pids),
            languages=sorted({s.language for s in model_subs}),
            pass_at_1_max=max((s.pass_at_1 for s in model_subs if s.pass_at_1 is not None), default=None),
            pass_at_k_max=max((s.pass_at_k for s in model_subs if s.pass_at_k is not None), default=None),
            pass_at_full_max=max((s.pass_at_full for s in model_subs if s.pass_at_full is not None), default=None),
            avg_runtime_ms=_mean_or_none(avg_rt),
            best_runtime_ms=_mean_or_none(best_rt),
            avg_memory_mb=_mean_or_none(avg_mem),
            best_memory_mb=_mean_or_none(best_mem),
            avg_runtime_percentile=_mean_or_none(avg_rtp),
            avg_memory_percentile=_mean_or_none(avg_memp),
            time_human_avg=_mean_or_none(t_h_avg),
            time_human_expert=_mean_or_none(t_h_exp),
            mem_human_avg=_mean_or_none(m_h_avg),
            mem_human_expert=_mean_or_none(m_h_exp),
            leading_submission_id=leading.id if leading else None,
        ))
        # Emit one CompareByLevelRow per (model, level), in Easy→Medium→Hard
        # order so the SPA can iterate the rows directly to populate its
        # grouped bar charts.
        for lvl in BY_LEVEL:
            b = per_level[lvl]
            n_att = b["n_attempted"]
            n_acc = b["n_accepted"]
            by_level_rows.append(CompareByLevelRow(
                model_name=model_name,
                level=lvl,
                n_attempted=n_att,
                n_accepted=n_acc,
                pass_rate=(n_acc / n_att) if n_att else 0.0,
                avg_runtime_ms=_mean_or_none(b["avg_rt"]),
                avg_memory_mb=_mean_or_none(b["avg_mem"]),
                avg_runtime_percentile=_mean_or_none(b["avg_rtp"]),
                avg_memory_percentile=_mean_or_none(b["avg_memp"]),
                avg_cost_usd=_mean_or_none(b["cost_per_problem"]),
                avg_response_tokens=_mean_or_none(b["tokens_per_problem"]),
                avg_wall_clock_ms=_mean_or_none(b["wall_per_problem"]),
            ))

    # Per-problem matrix: every problem any model touched (capped to the
    # filter subset if provided).
    matrix_pids: set[int] = set()
    for (_m, pid) in best_atts:
        matrix_pids.add(pid)
    if wanted_slugs:
        matrix_pids &= {slug_to_id[s] for s in wanted_slugs if s in slug_to_id}
    matrix_pids_sorted = sorted(
        matrix_pids,
        key=lambda pid: (all_problems[pid].level, all_problems[pid].title_slug),
    )

    problems_out: list[CompareProblemRow] = []
    for pid in matrix_pids_sorted:
        p = all_problems[pid]
        per_model: dict[str, CompareModelOnProblem] = {}
        for m in models:
            best = best_atts.get((m, pid))
            if best is None:
                per_model[m] = CompareModelOnProblem(
                    accepted=False, submission_id=None, attempt_id=None,
                    language=None, status=None,
                    runtime_ms=None, memory_mb=None,
                    runtime_percentile=None, memory_percentile=None,
                )
                continue
            sub_of = session.get(Submission, best.submission_id)
            per_model[m] = CompareModelOnProblem(
                accepted=best.final_status == "Accepted",
                submission_id=best.submission_id, attempt_id=best.id,
                language=sub_of.language if sub_of else None,
                status=best.final_status or "queued",
                runtime_ms=best.avg_runtime_ms,
                memory_mb=best.avg_memory_mb,
                runtime_percentile=best.avg_runtime_percentile,
                memory_percentile=best.avg_memory_percentile,
                generation_energy_j=best.generation_energy_j,
                # Populated by execution-instrumented harnesses (arena-local
                # etc.). None for LC-only rows — the head-to-head comparison
                # gates its energy row on ANY model reporting it (see
                # shouldShowEnergyColumn on the client).
                runtime_energy_j=best.avg_runtime_energy_j,
            )
        problems_out.append(CompareProblemRow(
            problem_slug=p.title_slug, problem_title=p.title, level=p.level,
            per_model=per_model,
        ))

    # Flatten the per-tag accumulator into typed rows. One row per
    # (model, tag); models that never touched a tag get no row for it
    # — the SPA can fold the absent cells to "no data" spokes on the
    # radar. Sort by tag for stable axis order on the chart.
    by_tag_rows: list[CompareByTagRow] = []
    for (mn, tag), cell in sorted(by_tag_acc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        n_att = cell["n_attempted"]
        n_acc = cell["n_accepted"]
        by_tag_rows.append(CompareByTagRow(
            model_name=mn, tag=tag,
            n_attempted=n_att, n_accepted=n_acc,
            pass_rate=(n_acc / n_att) if n_att else 0.0,
            avg_runtime_ms=_mean_or_none(cell["avg_rt"]),
            avg_memory_mb=_mean_or_none(cell["avg_mem"]),
        ))

    return CompareResponse(
        models=models, language=language,
        problem_slugs=sorted(wanted_slugs) if wanted_slugs else None,
        summary=summary, problems=problems_out,
        winners=_compute_winners(summary, single_language=language is not None),
        by_level=by_level_rows,
        by_tag=by_tag_rows,
    )


# ---- per-submission comparison ---------------------------------------------
#
# Same response shape as /api/compare (so the SPA reuses every render
# component on its new "Compare submissions" tab), but the axis is the
# SUBMISSION id rather than the model name. Each picked submission is
# its own "row" — useful when you want to compare two snapshots of the
# same model (different prompt, different temperature, different sweep
# run) without aggregating them together.

@app.get("/api/compare/submissions", response_model=CompareResponse)
def compare_submissions(
    ids: list[int] = Query(..., min_length=2,
                           description="repeat ?ids=N for each submission to compare"),
    problems: str | None = Query(
        None, description="comma-separated problem slugs to restrict to"),
    session: Session = Depends(get_session),
):
    """Head-to-head comparison across specific submission IDs. Each
    submission contributes one row; the response shape is identical to
    /api/compare so the SPA reuses the same SummaryTable, CompareByLevel,
    and PerProblemMatrix renderers. Labels for the per-row "model" field
    are synthesised as `#<id> <model_name> · <language>` so the operator
    can tell two submissions from the same model apart."""
    wanted_slugs: set[str] | None = None
    if problems:
        wanted_slugs = {s.strip() for s in problems.split(",") if s.strip()}

    subs_by_id: dict[int, Submission] = {}
    for sid in ids:
        sub = session.get(Submission, sid)
        if sub is None or sub.deleted_at is not None:
            raise HTTPException(404, f"submission {sid} not found")
        subs_by_id[sid] = sub
    # Each submission's label is its own "model_name" in the response,
    # so the SPA can colour-code and label them without ambiguity.
    labels_in_order: list[str] = [
        f"#{sid} {subs_by_id[sid].model_name} · {subs_by_id[sid].language}"
        for sid in ids
    ]
    label_by_id: dict[int, str] = dict(zip(ids, labels_in_order))

    all_problems = {p.id: p for p in session.exec(select(Problem)).all()}
    slug_to_id = {p.title_slug: pid for pid, p in all_problems.items()}

    summary: list[CompareSummaryRow] = []
    by_level_rows: list[CompareByLevelRow] = []
    # Same shape as the /api/compare model-axis route: (label, tag) →
    # rolling tallies. Drives the radar charts in the SPA's Submissions
    # tab so a viewer can ask "which submission is strongest on Graph
    # problems?" the same way they ask the model-axis question.
    by_tag_acc: dict[tuple[str, str], dict] = {}
    best_atts: dict[tuple[str, int], Attempt] = {}

    BY_LEVEL = ("Easy", "Medium", "Hard")
    for sid in ids:
        sub = subs_by_id[sid]
        label = label_by_id[sid]
        attempts = session.exec(
            select(Attempt).where(Attempt.submission_id == sid)
        ).all()
        # Restrict to the requested slug subset (if any).
        attempted_pids: set[int] = set()
        by_pid: dict[int, list[Attempt]] = {}
        for a in attempts:
            if wanted_slugs is not None:
                p = all_problems.get(a.problem_id)
                if p is None or p.title_slug not in wanted_slugs:
                    continue
            attempted_pids.add(a.problem_id)
            by_pid.setdefault(a.problem_id, []).append(a)

        avg_rt, best_rt, avg_mem, best_mem = [], [], [], []
        avg_rtp, avg_memp = [], []
        t_h_avg, t_h_exp, m_h_avg, m_h_exp = [], [], [], []
        n_accepted = 0
        per_level: dict[str, dict] = {
            lvl: {
                "n_attempted": 0, "n_accepted": 0,
                "avg_rt": [], "avg_mem": [],
                "avg_rtp": [], "avg_memp": [],
                "cost_per_problem": [], "tokens_per_problem": [],
                "wall_per_problem": [],
            }
            for lvl in BY_LEVEL
        }
        for pid, atts in by_pid.items():
            problem = all_problems.get(pid)
            lvl = (problem.level if problem else "") or ""
            bucket = per_level.get(lvl)
            if bucket is not None:
                bucket["n_attempted"] += 1
                costs = [a.cost_usd for a in atts if a.cost_usd is not None]
                if costs:
                    bucket["cost_per_problem"].append(sum(costs))
                toks = [a.response_tokens for a in atts if a.response_tokens is not None]
                if toks:
                    bucket["tokens_per_problem"].append(sum(toks))
                wcs = [a.wall_clock_ms for a in atts if a.wall_clock_ms is not None]
                if wcs:
                    bucket["wall_per_problem"].append(sum(wcs))
            # Per-tag attempted tally — runs for every problem this
            # submission touched. We index by `label` (the submission's
            # display label) so each submission shows up as its own
            # polygon on the radar.
            problem_tags = [
                t.strip() for t in ((problem.tags if problem else "") or "").split(",")
                if t.strip()
            ]
            for tag in problem_tags:
                cell = by_tag_acc.setdefault((label, tag), {
                    "n_attempted": 0, "n_accepted": 0,
                    "avg_rt": [], "avg_mem": [],
                })
                cell["n_attempted"] += 1
            accepted = [a for a in atts if a.final_status == "Accepted"]
            if accepted:
                best = max(accepted, key=lambda a: (
                    (a.n_accepted_runs / a.n_runs) if a.n_runs else 0.0,
                    -(a.avg_runtime_ms if a.avg_runtime_ms is not None else 1e18),
                ))
                n_accepted += 1
                if bucket is not None:
                    bucket["n_accepted"] += 1
            else:
                best = max(atts, key=lambda a: a.id)
            best_atts[(label, pid)] = best
            if best.final_status != "Accepted":
                continue
            # Per-tag accepted + perf tallies. n_attempted was already
            # bumped above so we only bump n_accepted here and push the
            # winning attempt's perf numbers.
            for tag in problem_tags:
                cell = by_tag_acc[(label, tag)]
                cell["n_accepted"] += 1
                if best.avg_runtime_ms is not None:
                    cell["avg_rt"].append(best.avg_runtime_ms)
                if best.avg_memory_mb is not None:
                    cell["avg_mem"].append(best.avg_memory_mb)
            if best.avg_runtime_ms is not None:    avg_rt.append(best.avg_runtime_ms)
            if best.best_runtime_ms is not None:   best_rt.append(best.best_runtime_ms)
            if best.avg_memory_mb is not None:     avg_mem.append(best.avg_memory_mb)
            if best.best_memory_mb is not None:    best_mem.append(best.best_memory_mb)
            if best.avg_runtime_percentile is not None: avg_rtp.append(best.avg_runtime_percentile)
            if best.avg_memory_percentile is not None:  avg_memp.append(best.avg_memory_percentile)
            if bucket is not None and best.avg_runtime_ms is not None:
                bucket["avg_rt"].append(best.avg_runtime_ms)
            if bucket is not None and best.avg_memory_mb is not None:
                bucket["avg_mem"].append(best.avg_memory_mb)
            if bucket is not None and best.avg_runtime_percentile is not None:
                bucket["avg_rtp"].append(best.avg_runtime_percentile)
            if bucket is not None and best.avg_memory_percentile is not None:
                bucket["avg_memp"].append(best.avg_memory_percentile)
            baseline = session.exec(
                select(Baseline).where(
                    Baseline.problem_id == pid, Baseline.language == sub.language)
            ).first()
            if baseline and best.avg_runtime_ms and best.avg_runtime_ms > 0:
                t_h_avg.append(baseline.runtime_ms_avg / best.avg_runtime_ms)
                t_h_exp.append(baseline.runtime_ms_expert / best.avg_runtime_ms)
            if baseline and best.avg_memory_mb and best.avg_memory_mb > 0:
                m_h_avg.append(baseline.memory_mb_avg / best.avg_memory_mb)
                m_h_exp.append(baseline.memory_mb_expert / best.avg_memory_mb)

        summary.append(CompareSummaryRow(
            model_name=label, n_submissions=1,
            n_problems_accepted=n_accepted,
            n_problems_attempted=len(attempted_pids),
            languages=[sub.language],
            pass_at_1_max=sub.pass_at_1,
            pass_at_k_max=sub.pass_at_k,
            pass_at_full_max=sub.pass_at_full,
            avg_runtime_ms=_mean_or_none(avg_rt),
            best_runtime_ms=_mean_or_none(best_rt),
            avg_memory_mb=_mean_or_none(avg_mem),
            best_memory_mb=_mean_or_none(best_mem),
            avg_runtime_percentile=_mean_or_none(avg_rtp),
            avg_memory_percentile=_mean_or_none(avg_memp),
            time_human_avg=_mean_or_none(t_h_avg),
            time_human_expert=_mean_or_none(t_h_exp),
            mem_human_avg=_mean_or_none(m_h_avg),
            mem_human_expert=_mean_or_none(m_h_exp),
            leading_submission_id=sub.id,
        ))
        for lvl in BY_LEVEL:
            b = per_level[lvl]
            n_att = b["n_attempted"]
            n_acc = b["n_accepted"]
            by_level_rows.append(CompareByLevelRow(
                model_name=label, level=lvl,
                n_attempted=n_att, n_accepted=n_acc,
                pass_rate=(n_acc / n_att) if n_att else 0.0,
                avg_runtime_ms=_mean_or_none(b["avg_rt"]),
                avg_memory_mb=_mean_or_none(b["avg_mem"]),
                avg_runtime_percentile=_mean_or_none(b["avg_rtp"]),
                avg_memory_percentile=_mean_or_none(b["avg_memp"]),
                avg_cost_usd=_mean_or_none(b["cost_per_problem"]),
                avg_response_tokens=_mean_or_none(b["tokens_per_problem"]),
                avg_wall_clock_ms=_mean_or_none(b["wall_per_problem"]),
            ))

    # Per-problem matrix: every problem any submission touched.
    matrix_pids = {pid for (_label, pid) in best_atts}
    if wanted_slugs:
        matrix_pids &= {slug_to_id[s] for s in wanted_slugs if s in slug_to_id}
    matrix_pids_sorted = sorted(
        matrix_pids,
        key=lambda pid: (all_problems[pid].level, all_problems[pid].title_slug),
    )
    problems_out: list[CompareProblemRow] = []
    for pid in matrix_pids_sorted:
        p = all_problems[pid]
        per_label: dict[str, CompareModelOnProblem] = {}
        for label in labels_in_order:
            best = best_atts.get((label, pid))
            if best is None:
                per_label[label] = CompareModelOnProblem(
                    accepted=False, submission_id=None, attempt_id=None,
                    language=None, status=None,
                    runtime_ms=None, memory_mb=None,
                    runtime_percentile=None, memory_percentile=None,
                )
                continue
            sub_of = session.get(Submission, best.submission_id)
            per_label[label] = CompareModelOnProblem(
                accepted=best.final_status == "Accepted",
                submission_id=best.submission_id, attempt_id=best.id,
                language=sub_of.language if sub_of else None,
                status=best.final_status,
                runtime_ms=best.avg_runtime_ms, memory_mb=best.avg_memory_mb,
                runtime_percentile=best.avg_runtime_percentile,
                memory_percentile=best.avg_memory_percentile,
            )
        problems_out.append(CompareProblemRow(
            problem_slug=p.title_slug, problem_title=p.title, level=p.level,
            per_model=per_label,
        ))

    # Flatten the per-tag accumulator into typed rows. Same flatten
    # logic as the model-axis route (sort by tag then label so the
    # radar's axis order is stable across reloads).
    by_tag_rows: list[CompareByTagRow] = []
    for (lbl, tag), cell in sorted(by_tag_acc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        n_att = cell["n_attempted"]
        n_acc = cell["n_accepted"]
        by_tag_rows.append(CompareByTagRow(
            model_name=lbl, tag=tag,
            n_attempted=n_att, n_accepted=n_acc,
            pass_rate=(n_acc / n_att) if n_att else 0.0,
            avg_runtime_ms=_mean_or_none(cell["avg_rt"]),
            avg_memory_mb=_mean_or_none(cell["avg_mem"]),
        ))

    return CompareResponse(
        models=labels_in_order, language=None,
        problem_slugs=sorted(wanted_slugs) if wanted_slugs else None,
        summary=summary, problems=problems_out,
        winners=_compute_winners(summary, single_language=False),
        by_level=by_level_rows,
        by_tag=by_tag_rows,
    )


# ---- prompt-strategy comparison --------------------------------------------
#
# Same shape as /api/compare, but the axis is the prompt template rather than
# the model. The endpoint requires a model_name + model_version + language so
# the comparison is a clean apples-to-apples within ONE experiment cell.

@app.get("/api/compare/prompts", response_model=ComparePromptsResponse)
def compare_prompts(
    model_name: str = Query(..., description="model_name to scope to"),
    model_version: str | None = Query(None, description="model_version to scope to"),
    language: str | None = None,
    prompt_ids: list[int] = Query(..., min_length=2,
                                  description="repeat ?prompt_ids=ID for each prompt to compare"),
    problems: str | None = Query(
        None, description="comma-separated problem slugs to restrict to"),
    competition: str | None = Query(
        None, description="restrict to a competition's problem set (slug)"),
    session: Session = Depends(get_session),
):
    """Head-to-head comparison of prompt strategies within one (model, version,
    language) cell. Returns aggregated correctness + performance metrics per
    prompt id plus a per-metric winner banner — so you can read off whether
    e.g. an `explain-then-code` prompt buys correctness OR runtime over the
    plain `standard-code-only` template, holding the model fixed."""
    wanted_slugs: set[str] | None = None
    if problems:
        wanted_slugs = {s.strip() for s in problems.split(",") if s.strip()}
    if competition:
        comp_slugs = _competition_problem_slugs(session, competition)
        if comp_slugs is not None:
            wanted_slugs = comp_slugs if wanted_slugs is None else (wanted_slugs & comp_slugs)

    # Resolve prompt rows once (also enforces that every requested id exists).
    prompts_by_id: dict[int, PromptTemplate] = {}
    for pid in prompt_ids:
        pt = session.get(PromptTemplate, pid)
        if pt is None:
            raise HTTPException(400, f"prompt_id {pid} does not exist")
        prompts_by_id[pid] = pt

    q = (
        select(Submission)
        .where(Submission.model_name == model_name)
        .where(Submission.prompt_id.in_(prompt_ids))                       # type: ignore[arg-type]
        .where(Submission.status == SubmissionStatus.judged)
        .where(Submission.deleted_at.is_(None))
    )
    if model_version:
        q = q.where(Submission.model_version == model_version)
    if language:
        q = q.where(Submission.language == language)
    subs = session.exec(q).all()

    by_prompt: dict[int, list[Submission]] = {}
    for s in subs:
        by_prompt.setdefault(s.prompt_id, []).append(s)

    all_problems = {p.id: p for p in session.exec(select(Problem)).all()}

    rows: list[ComparePromptRow] = []
    for pid in prompt_ids:
        pt = prompts_by_id[pid]
        prompt_subs = by_prompt.get(pid, [])
        if not prompt_subs:
            rows.append(ComparePromptRow(
                prompt_id=pid, prompt_name=pt.name, status=pt.status,
                n_submissions=0,
                n_problems_accepted=0, n_problems_attempted=0,
                pass_at_1_max=None, pass_at_k_max=None, pass_at_full_max=None,
                avg_runtime_ms=None, best_runtime_ms=None,
                avg_memory_mb=None, best_memory_mb=None,
                avg_runtime_percentile=None, avg_memory_percentile=None,
                time_human_avg=None, time_human_expert=None,
                mem_human_avg=None, mem_human_expert=None,
                leading_submission_id=None,
            ))
            continue

        attempts = session.exec(
            select(Attempt).where(Attempt.submission_id.in_([s.id for s in prompt_subs]))   # type: ignore[arg-type]
        ).all()
        # Group by problem; restrict to the requested slug subset (if any).
        attempted_pids: set[int] = set()
        by_pid: dict[int, list[Attempt]] = {}
        for a in attempts:
            if wanted_slugs is not None:
                p = all_problems.get(a.problem_id)
                if p is None or p.title_slug not in wanted_slugs:
                    continue
            attempted_pids.add(a.problem_id)
            by_pid.setdefault(a.problem_id, []).append(a)

        avg_rt, best_rt, avg_mem, best_mem = [], [], [], []
        avg_rtp, avg_memp = [], []
        t_h_avg, t_h_exp, m_h_avg, m_h_exp = [], [], [], []
        n_accepted = 0
        for problem_id, atts in by_pid.items():
            accepted = [a for a in atts if a.final_status == "Accepted"]
            if accepted:
                best = max(accepted, key=lambda a: (
                    (a.n_accepted_runs / a.n_runs) if a.n_runs else 0.0,
                    -(a.avg_runtime_ms if a.avg_runtime_ms is not None else 1e18),
                ))
                n_accepted += 1
            else:
                continue            # only successful attempts contribute to perf aggregates
            if best.avg_runtime_ms is not None:    avg_rt.append(best.avg_runtime_ms)
            if best.best_runtime_ms is not None:   best_rt.append(best.best_runtime_ms)
            if best.avg_memory_mb is not None:     avg_mem.append(best.avg_memory_mb)
            if best.best_memory_mb is not None:    best_mem.append(best.best_memory_mb)
            if best.avg_runtime_percentile is not None: avg_rtp.append(best.avg_runtime_percentile)
            if best.avg_memory_percentile is not None:  avg_memp.append(best.avg_memory_percentile)

            sub_of_best = next((s for s in prompt_subs if s.id == best.submission_id), None)
            baseline = session.exec(
                select(Baseline).where(
                    Baseline.problem_id == problem_id,
                    Baseline.language == (sub_of_best.language if sub_of_best else None),
                )
            ).first()
            if baseline and best.avg_runtime_ms and best.avg_runtime_ms > 0:
                t_h_avg.append(baseline.runtime_ms_avg / best.avg_runtime_ms)
                t_h_exp.append(baseline.runtime_ms_expert / best.avg_runtime_ms)
            if baseline and best.avg_memory_mb and best.avg_memory_mb > 0:
                m_h_avg.append(baseline.memory_mb_avg / best.avg_memory_mb)
                m_h_exp.append(baseline.memory_mb_expert / best.avg_memory_mb)

        leading = max(prompt_subs,
                      key=lambda s: (s.pass_at_1 or 0.0, s.avg_runtime_percentile or 0.0))
        rows.append(ComparePromptRow(
            prompt_id=pid, prompt_name=pt.name, status=pt.status,
            n_submissions=len(prompt_subs),
            n_problems_accepted=n_accepted,
            n_problems_attempted=len(attempted_pids),
            pass_at_1_max=max((s.pass_at_1 for s in prompt_subs if s.pass_at_1 is not None), default=None),
            pass_at_k_max=max((s.pass_at_k for s in prompt_subs if s.pass_at_k is not None), default=None),
            pass_at_full_max=max((s.pass_at_full for s in prompt_subs if s.pass_at_full is not None), default=None),
            avg_runtime_ms=_mean_or_none(avg_rt),
            best_runtime_ms=_mean_or_none(best_rt),
            avg_memory_mb=_mean_or_none(avg_mem),
            best_memory_mb=_mean_or_none(best_mem),
            avg_runtime_percentile=_mean_or_none(avg_rtp),
            avg_memory_percentile=_mean_or_none(avg_memp),
            time_human_avg=_mean_or_none(t_h_avg),
            time_human_expert=_mean_or_none(t_h_exp),
            mem_human_avg=_mean_or_none(m_h_avg),
            mem_human_expert=_mean_or_none(m_h_exp),
            leading_submission_id=leading.id if leading else None,
        ))

    return ComparePromptsResponse(
        model_name=model_name, model_version=model_version,
        language=language,
        problem_slugs=sorted(wanted_slugs) if wanted_slugs else None,
        prompt_ids=list(prompt_ids),
        rows=rows,
        winners=_compute_prompt_winners(rows),
    )


def _compute_prompt_winners(rows: list[ComparePromptRow]) -> ComparePromptsWinners:
    """Same shape as `_compute_winners` but keyed off `prompt_name` and using
    the prompt-row metric names. Raw ms / MB are valid here because the
    endpoint requires a single language (so the harness overhead is fixed)."""
    def _best(metric: str, *, higher_is_better: bool) -> str | None:
        ranked = [(getattr(r, metric), r.prompt_name) for r in rows
                  if getattr(r, metric) is not None]
        if not ranked:
            return None
        ranked.sort(key=lambda x: -x[0] if higher_is_better else x[0])
        return ranked[0][1]

    metric_winners = {
        "best_pass_at_1": _best("pass_at_1_max", higher_is_better=True),
        "best_pass_at_k": _best("pass_at_k_max", higher_is_better=True),
        "best_pass_at_full": _best("pass_at_full_max", higher_is_better=True),
        "most_problems_solved": _best("n_problems_accepted", higher_is_better=True),
        "fastest_avg_runtime": _best("avg_runtime_ms", higher_is_better=False),
        "leanest_avg_memory": _best("avg_memory_mb", higher_is_better=False),
        "best_runtime_percentile": _best("avg_runtime_percentile", higher_is_better=True),
        "best_memory_percentile": _best("avg_memory_percentile", higher_is_better=True),
    }
    wins: dict[str, int] = {r.prompt_name: 0 for r in rows}
    for w in metric_winners.values():
        if w is not None:
            wins[w] = wins.get(w, 0) + 1
    overall: str | None = None
    if wins:
        by_name = {r.prompt_name: r for r in rows}
        ranking = sorted(
            wins.items(),
            key=lambda kv: (
                -kv[1],
                -(by_name[kv[0]].n_problems_accepted if by_name.get(kv[0]) else 0),
                -((by_name[kv[0]].pass_at_1_max or 0.0) if by_name.get(kv[0]) else 0.0),
                kv[0],
            ),
        )
        if ranking and ranking[0][1] > 0:
            overall = ranking[0][0]
    return ComparePromptsWinners(
        overall=overall, overall_wins_by_prompt=wins, **metric_winners,
    )


def _compute_winners(
    summary: list[CompareSummaryRow], *, single_language: bool,
) -> CompareWinners:
    """Pick the winning model per metric + a composite overall winner.

    For each metric we know whether higher or lower is better; metrics where
    every model has None contribute no winner. Ties on a single metric are
    awarded to whichever model appears first in the summary list (stable).
    Overall = most metric wins, then most solved, then highest pass@1."""
    def _best(metric: str, *, higher_is_better: bool) -> str | None:
        ranked = [(getattr(r, metric), r.model_name) for r in summary
                  if getattr(r, metric) is not None]
        if not ranked:
            return None
        ranked.sort(key=lambda x: -x[0] if higher_is_better else x[0])
        return ranked[0][1]

    # Raw ms / MB are only comparable within a single language (different
    # languages have different harness overheads, time limits, and memory
    # baselines). When the compared set spans > 1 language we set the raw-
    # number winners to None — percentile + coefficient + pass@* winners
    # stay because those are per-language-normalised by construction.
    distinct_langs: set[str] = set()
    for r in summary:
        distinct_langs.update(r.languages)
    cross_lang = single_language is False and len(distinct_langs) > 1

    metric_winners = {
        "best_pass_at_1": _best("pass_at_1_max", higher_is_better=True),
        "best_pass_at_k": _best("pass_at_k_max", higher_is_better=True),
        "best_pass_at_full": _best("pass_at_full_max", higher_is_better=True),
        "most_problems_solved": _best("n_problems_accepted", higher_is_better=True),
        "fastest_avg_runtime": None if cross_lang else _best("avg_runtime_ms", higher_is_better=False),
        "leanest_avg_memory": None if cross_lang else _best("avg_memory_mb", higher_is_better=False),
        "best_runtime_percentile": _best("avg_runtime_percentile", higher_is_better=True),
        "best_memory_percentile": _best("avg_memory_percentile", higher_is_better=True),
    }
    wins: dict[str, int] = {r.model_name: 0 for r in summary}
    for w in metric_winners.values():
        if w is not None:
            wins[w] = wins.get(w, 0) + 1

    overall: str | None = None
    if wins:
        # Sort by (wins desc, solved desc, pass@1 desc, model name) for stability.
        by_name = {r.model_name: r for r in summary}
        ranking = sorted(
            wins.items(),
            key=lambda kv: (
                -kv[1],
                -(by_name[kv[0]].n_problems_accepted if by_name.get(kv[0]) else 0),
                -((by_name[kv[0]].pass_at_1_max or 0.0) if by_name.get(kv[0]) else 0.0),
                kv[0],
            ),
        )
        if ranking and ranking[0][1] > 0:
            overall = ranking[0][0]

    return CompareWinners(
        overall=overall, overall_wins_by_model=wins, **metric_winners,
    )


def _mean_or_none(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _median_or_none(xs: list[float]) -> float | None:
    """Median of a numeric series — None when the series is empty. Used
    by the rankings/submissions tables to surface per-attempt typical
    spend without leaking the long tail of a submission with one
    blown-budget attempt at the top of a chart."""
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient of two equal-length series. None when
    fewer than 3 pairs or when either series has zero variance (correlation
    undefined). Pure-stdlib to avoid a numpy dependency."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / (sxx ** 0.5 * syy ** 0.5), 4)


@app.get("/api/analysis", response_model=AnalysisResponse)
def analysis(session: Session = Depends(get_session)):
    """Cross-language correctness × performance analysis.

    Two outputs from the same judged-submission pool:
      * by_language — per-language aggregate of correctness (pass@1/pass@k) and
        performance (runtime/memory percentile), for the cross-language grid.
      * points — one row per judged submission (model, language, pass@1,
        pass@k, runtime/memory percentile), for the correctness-vs-performance
        scatter.

    Plus Pearson correlations between correctness and performance, both pooled
    and per-language. The literature finds correctness and efficiency are
    largely decoupled (r≈0); this endpoint lets PerfArena test that on real
    LeetCode percentiles across all supported languages.
    """
    subs = session.exec(
        select(Submission)
        .where(Submission.deleted_at.is_(None))
        .where(Submission.status == SubmissionStatus.judged)
    ).all()

    points = [
        AnalysisPoint(
            submission_id=s.id, model_name=s.model_name, language=s.language,
            pass_at_1=s.pass_at_1, pass_at_k=s.pass_at_k,
            avg_runtime_percentile=s.avg_runtime_percentile,
            avg_memory_percentile=s.avg_memory_percentile,
        )
        for s in subs
    ]

    # Per-language aggregates.
    by_lang: dict[str, list[Submission]] = {}
    for s in subs:
        by_lang.setdefault(s.language, []).append(s)
    rows: list[LangAnalysisRow] = []
    for lang, group in sorted(by_lang.items()):
        p1 = [s.pass_at_1 for s in group if s.pass_at_1 is not None]
        pk = [s.pass_at_k for s in group if s.pass_at_k is not None]
        rtp = [s.avg_runtime_percentile for s in group if s.avg_runtime_percentile is not None]
        memp = [s.avg_memory_percentile for s in group if s.avg_memory_percentile is not None]
        # within-language correlation needs paired (pass@1, runtime %ile)
        paired = [(s.pass_at_1, s.avg_runtime_percentile) for s in group
                  if s.pass_at_1 is not None and s.avg_runtime_percentile is not None]
        rows.append(LangAnalysisRow(
            language=lang,
            n_submissions=len(group),
            n_models=len({s.model_name for s in group}),
            mean_pass_at_1=_mean_or_none(p1),
            best_pass_at_1=max(p1) if p1 else None,
            mean_pass_at_k=_mean_or_none(pk),
            mean_runtime_percentile=_mean_or_none(rtp),
            mean_memory_percentile=_mean_or_none(memp),
            pass1_runtime_corr=_pearson([a for a, _ in paired], [b for _, b in paired]),
        ))

    # Pooled correlations across every judged submission.
    def _paired(attr_x: str, attr_y: str) -> tuple[list[float], list[float]]:
        xs, ys = [], []
        for s in subs:
            x, y = getattr(s, attr_x), getattr(s, attr_y)
            if x is not None and y is not None:
                xs.append(x); ys.append(y)
        return xs, ys

    rx, ry = _paired("pass_at_1", "avg_runtime_percentile")
    mx, my = _paired("pass_at_1", "avg_memory_percentile")
    kx, ky = _paired("pass_at_k", "avg_runtime_percentile")

    return AnalysisResponse(
        by_language=rows,
        points=points,
        n_points=len(points),
        pass1_vs_runtime_corr=_pearson(rx, ry),
        pass1_vs_memory_corr=_pearson(mx, my),
        passk_vs_runtime_corr=_pearson(kx, ky),
    )


@app.get("/api/discrimination", response_model=DiscriminationResponse)
def discrimination():
    """Efficiency-discrimination by language: for each language, how many
    sampled problems have runtime histograms that can actually distinguish a
    fast model from a slow one (vs. being pinned at LeetCode's resolution
    floor). Sourced from data/discrimination.json (built by
    app.measure_discrimination). The novel, language-specific finding —
    a problem can discriminate in Python but be noise-floor in Java.

    Static endpoint over a precomputed file (no DB, no LeetCode); returns an
    empty result if the file hasn't been generated yet."""
    import json as _json
    path = REPO_ROOT_DATA / "discrimination.json"
    if not path.exists():
        return DiscriminationResponse(languages=[], cross_language=[], n_cross_language=0)
    try:
        data = _json.loads(path.read_text())
    except _json.JSONDecodeError:
        return DiscriminationResponse(languages=[], cross_language=[], n_cross_language=0)

    summaries: list[DiscriminationLangSummary] = []
    for lang, problems in sorted(data.items()):
        rows = list(problems.values())
        n = len(rows)
        if n == 0:
            continue
        disc = sum(1 for r in rows if r.get("discriminative"))
        shares = [r["mode_share"] for r in rows if r.get("mode_share") is not None]
        ratios = sorted(r["spread_ratio"] for r in rows if r.get("spread_ratio") is not None)
        hist = [0] * 10
        for s in shares:
            hist[min(9, int(s // 10))] += 1
        summaries.append(DiscriminationLangSummary(
            language=lang,
            n_measured=n,
            n_discriminative=disc,
            pct_discriminative=round(100 * disc / n, 1),
            mean_mode_share=round(sum(shares) / len(shares), 2) if shares else None,
            median_spread_ratio=ratios[len(ratios) // 2] if ratios else None,
            mode_share_histogram=hist,
        ))

    # Cross-language matrix: problems measured in 2+ languages.
    by_slug: dict[str, dict] = {}
    for lang, problems in data.items():
        for slug, r in problems.items():
            by_slug.setdefault(slug, {})[lang] = r
    cross: list[DiscriminationCrossRow] = []
    for slug, langs in by_slug.items():
        if len(langs) < 2:
            continue
        flags = {l: bool(r.get("discriminative")) for l, r in langs.items()}
        cross.append(DiscriminationCrossRow(
            title_slug=slug,
            level=next((r.get("level") for r in langs.values() if r.get("level")), None),
            by_language={l: {"mode_share": r.get("mode_share"),
                             "discriminative": bool(r.get("discriminative")),
                             "spread_ratio": r.get("spread_ratio")}
                         for l, r in langs.items()},
            flips=len(set(flags.values())) > 1,
        ))
    # Surface the flips first — those are the headline cases.
    cross.sort(key=lambda c: (not c.flips, c.title_slug))
    return DiscriminationResponse(
        languages=summaries, cross_language=cross, n_cross_language=len(cross))


@app.get("/api/models/{name}", response_model=ModelDetail)
def model_detail(name: str, session: Session = Depends(get_session)):
    subs = session.exec(
        select(Submission)
        .where(Submission.model_name == name)
        .where(Submission.deleted_at.is_(None))
        .order_by(Submission.created_at.desc())
    ).all()
    if not subs:
        raise HTTPException(404, "model not found")

    judged = [s for s in subs if s.status == SubmissionStatus.judged]

    # Per-language breakdown so the page can show how the same model does
    # across languages instead of collapsing everything into one row.
    all_pids = {p.id for p in session.exec(select(Problem)).all()}
    stats_by_sub = _attempt_stats_by_sub(session, [s.id for s in judged])
    by_lang_map: dict[str, list[Submission]] = {}
    for s in judged:
        by_lang_map.setdefault(s.language, []).append(s)
    by_language: list[ModelDetailLanguageStat] = []
    for lang in sorted(by_lang_map):
        group = by_lang_map[lang]
        eligible_ids = {
            s.id for s in group
            if _eligible_for_leaderboard(
                s, _attempt_stats_for(stats_by_sub, s.id)[0], all_pids,
            )
        }
        by_language.append(ModelDetailLanguageStat(
            language=lang,
            n_submissions=len(group),
            best_pass_at_1=max((s.pass_at_1 for s in group if s.pass_at_1 is not None), default=None),
            best_avg_runtime_ms=min((s.avg_runtime_ms for s in group if s.avg_runtime_ms is not None), default=None),
            best_avg_memory_mb=min((s.avg_memory_mb for s in group if s.avg_memory_mb is not None), default=None),
            best_avg_runtime_percentile=max((s.avg_runtime_percentile for s in group if s.avg_runtime_percentile is not None), default=None),
            best_avg_memory_percentile=max((s.avg_memory_percentile for s in group if s.avg_memory_percentile is not None), default=None),
            fully_accepted_any=any(s.total_problems > 0 and s.total_solved >= s.total_problems for s in group),
            leaderboard_eligible_any=bool(eligible_ids),
        ))

    return ModelDetail(
        name=name,
        versions=sorted({s.model_version for s in subs}),
        languages=sorted({s.language for s in subs}),
        n_submissions=len(subs),
        best_pass_at_1=max((s.pass_at_1 for s in judged if s.pass_at_1 is not None), default=None),
        best_avg_runtime_percentile=max(
            (s.avg_runtime_percentile for s in judged if s.avg_runtime_percentile is not None),
            default=None,
        ),
        best_avg_memory_percentile=max(
            (s.avg_memory_percentile for s in judged if s.avg_memory_percentile is not None),
            default=None,
        ),
        submitters=sorted({_submitter_label(session.get(User, s.user_id)) for s in subs}),
        submissions=[_submission_public(s, session) for s in subs],
        by_language=by_language,
    )


@app.get("/api/submitters/{slug}", response_model=SubmitterDetail)
def submitter_detail(slug: str, session: Session = Depends(get_session)):
    users = session.exec(select(User)).all()
    matched = [u for u in users if _slug(_submitter_label(u)) == slug]
    if not matched:
        raise HTTPException(404, "submitter not found")

    user_ids = [u.id for u in matched]
    subs = session.exec(
        select(Submission)
        .where(Submission.user_id.in_(user_ids))
        .where(Submission.deleted_at.is_(None))
        .order_by(Submission.created_at.desc())
    ).all()

    display = matched[0].org or matched[0].display_name
    return SubmitterDetail(
        slug=slug,
        display_name=display,
        org=matched[0].org,
        n_submissions=len(subs),
        n_models=len({s.model_name for s in subs}),
        languages=sorted({s.language for s in subs}),
        submissions=[_submission_public(s, session) for s in subs],
    )


@app.delete("/api/submissions/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_submission(
    submission_id: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Admin-only: SOFT-delete the submission. The row and all its
    attempts/runs/trace-log entries are kept so the dataset export, the
    transparency hash chain, and audit trails stay intact — only the public
    read surfaces hide the row from now on. Mutations to the curated set of
    submissions are reserved for admins; the (now removed) owner-can-delete
    path was retired alongside self-submission."""
    sub = session.get(Submission, submission_id)
    if not sub or sub.deleted_at is not None:
        raise HTTPException(404, "submission not found")
    sub.deleted_at = datetime.utcnow()
    session.add(sub)
    session.commit()
    return None


@app.get("/api/submissions/{submission_id}")
def submission_detail(submission_id: int, session: Session = Depends(get_session)):
    sub = session.get(Submission, submission_id)
    if not sub or sub.deleted_at is not None:
        raise HTTPException(404, "not found")
    user = session.get(User, sub.user_id)
    attempts = session.exec(select(Attempt).where(Attempt.submission_id == submission_id)).all()
    # Per-attempt transparency-log entry, so the UI can show prev/entry hashes
    # inline on each row instead of forcing the user to scroll to the global log.
    log_by_attempt: dict[int, TraceLogEntry] = {
        e.attempt_id: e
        for e in session.exec(
            select(TraceLogEntry).where(
                TraceLogEntry.attempt_id.in_([a.id for a in attempts])
            )
        ).all()
    } if attempts else {}
    runs_out: list[dict] = []
    traces_out: list[dict] = []
    for a in attempts:
        problem = session.get(Problem, a.problem_id)
        slug = problem.title_slug if problem else "?"
        runs = session.exec(select(Run).where(Run.attempt_id == a.id)).all()
        r = runs[-1] if runs else None
        runs_out.append(RunPublic(
            problem_slug=slug,
            status_msg=r.status_msg if r else "queued",
            runtime_ms=r.runtime_ms if r else None,
            memory_mb=r.memory_mb if r else None,
            runtime_percentile=r.runtime_percentile if r else None,
            memory_percentile=r.memory_percentile if r else None,
            runtime_energy_j=r.runtime_energy_j if r else None,
            runtime_energy_percentile=r.runtime_energy_percentile if r else None,
            iterations=r.iterations if r else None,
            energy_j_per_op=r.energy_j_per_op if r else None,
            time_us_per_op=r.time_us_per_op if r else None,
            total_correct=r.total_correct if r else None,
            total_testcases=r.total_testcases if r else None,
            error_log=_build_error_log(r.raw_response, r.status_msg) if r else None,
            # Verbatim LC payload — only ships on non-Accepted runs so the
            # response doesn't carry useless data on the happy path.
            leetcode_full_response=(
                r.leetcode_full_response
                if r and r.status_msg != "Accepted"
                else None
            ),
        ).model_dump())
        log = log_by_attempt.get(a.id)
        traces_out.append({
            "problem_slug": slug,
            "attempt_id": a.id,
            "inference_trace": a.inference_trace,
            "trace": a.trace,
            "provenance": {
                "trace_hash": a.trace_hash,
                "sig_status": a.sig_status,
                "trace_signer": a.trace_signer,
                "model_digest": a.model_digest,
                "model_digest_source": a.model_digest_source,
                "log_seq": a.log_seq,
                "logged_at": a.logged_at.isoformat() if a.logged_at else None,
                "prev_hash": log.prev_hash if log else None,
                "entry_hash": log.entry_hash if log else None,
                "leaf_hash": log.leaf_hash if log else a.trace_hash,
            },
        })
    return {
        "submission": _submission_public(sub, session).model_dump(),
        "model_params": sub.model_params,
        "prompt_template": sub.prompt_template,
        "submitter": {
            "display_name": user.display_name if user else "?",
            "org": user.org if user else None,
        },
        "runs": runs_out,
        "traces": traces_out,
        "baselines": _baseline_summary(session, sub, attempts),
    }


def _baseline_summary(
    session: Session, sub: Submission, attempts: list[Attempt],
) -> dict:
    """Per-submission breakdown of which Baseline rows fed the LeetCode-coefficient
    math: synthetic placeholder vs leetcode-distribution, freshness range,
    and how many accepted problems had no baseline at all. Lets the UI label
    coefficients as estimated vs population-derived."""
    accepted_pids = {a.problem_id for a in attempts if a.final_status == "Accepted"}
    by_source: dict[str, int] = {}
    fetched_ats: list[datetime] = []
    for pid in accepted_pids:
        b = session.exec(
            select(Baseline).where(
                Baseline.problem_id == pid, Baseline.language == sub.language,
            )
        ).first()
        if b is None:
            by_source["missing"] = by_source.get("missing", 0) + 1
            continue
        by_source[b.source] = by_source.get(b.source, 0) + 1
        if b.fetched_at:
            fetched_ats.append(b.fetched_at)
    return {
        "language": sub.language,
        "n_used": len(accepted_pids),
        "by_source": by_source,
        "has_synthetic": by_source.get("synthetic", 0) > 0,
        "oldest_fetched_at": min(fetched_ats).isoformat() if fetched_ats else None,
        "newest_fetched_at": max(fetched_ats).isoformat() if fetched_ats else None,
    }


@app.get("/api/provenance/log")
def provenance_log(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    """Public, auditable view of the append-only trace transparency log.

    Anyone can recompute the chain: each entry's `entry_hash` =
    sha256(canonical_json({seq, prev_hash, leaf_hash, attempt_id, logged_at})),
    and `prev_hash` must equal the previous entry's `entry_hash` (genesis =
    64 zeros). `verified` reports the result of that recomputation server-side.
    """
    total = session.exec(select(TraceLogEntry)).all()
    head = max(total, key=lambda e: e.seq).entry_hash if total else provenance.GENESIS
    page = session.exec(
        select(TraceLogEntry).order_by(TraceLogEntry.seq.desc())
        .offset(offset).limit(limit)
    ).all()
    entries = []
    for e in page:
        att = session.get(Attempt, e.attempt_id)
        sub = session.get(Submission, att.submission_id) if att else None
        prob = session.get(Problem, att.problem_id) if att else None
        entries.append({
            "seq": e.seq,
            "logged_at": e.logged_at.isoformat(),
            "leaf_hash": e.leaf_hash,
            "prev_hash": e.prev_hash,
            "entry_hash": e.entry_hash,
            "attempt_id": e.attempt_id,
            "submission_id": att.submission_id if att else None,
            "problem_slug": prob.title_slug if prob else None,
            "model_name": sub.model_name if sub else None,
            "model_digest": att.model_digest if att else None,
            "sig_status": att.sig_status if att else None,
        })
    return {
        "count": len(total),
        "head": head,
        "genesis": provenance.GENESIS,
        "verified": provenance.verify_chain(total),
        "entries": entries,
    }


# The only attempt columns a submission summary reads. Selecting just these
# skips the large code/trace/inference_trace text of every attempt.
_SUBMISSION_ATTEMPT_COLS = (
    Attempt.submission_id, Attempt.problem_id, Attempt.logged_at,
    Attempt.cost_usd, Attempt.prompt_tokens, Attempt.response_tokens,
    Attempt.wall_clock_ms,
)


def _submission_public(
    s: Submission, session: Session, *,
    atts: list | None = None, all_pids: set[int] | None = None,
    pricing: dict | None = None,
) -> SubmissionPublic:
    """Public summary of one submission. List endpoints pass the submission's
    attempt rows (`_SUBMISSION_ATTEMPT_COLS`), the problem ids, and the pricing
    map, loaded once per request; a single-submission caller omits them."""
    u = session.get(User, s.user_id)
    pt = session.get(PromptTemplate, s.prompt_id) if s.prompt_id is not None else None
    if atts is None:
        atts = session.exec(
            select(*_SUBMISSION_ATTEMPT_COLS).where(Attempt.submission_id == s.id)
        ).all()
    attempted = {a.problem_id for a in atts}
    if all_pids is None:
        all_pids = set(session.exec(select(Problem.id)).all())
    reason = _leaderboard_reason(s, attempted, all_pids)
    # Competition reference for the new SPA column. None on legacy rows
    # that pre-date the per-submission competition scope.
    comp = session.get(Competition, s.competition_id) if s.competition_id is not None else None
    # Effective harness (PERFORMANCE oracle) for this submission: explicit
    # override → competition default → NULL. Resolves in-process so the
    # row's Harness column can link straight to /harnesses/{slug}.
    _hid = (
        s.harness_id
        if s.harness_id is not None
        else (comp.harness_id if comp is not None else None)
    )
    harness_row = session.get(Harness, _hid) if _hid is not None else None
    # Effective CORRECTNESS oracle — twin resolution shape.
    _cid = (
        s.correctness_oracle_id
        if s.correctness_oracle_id is not None
        else (comp.correctness_oracle_id if comp is not None else None)
    )
    correctness_row = (
        session.get(CorrectnessOracle, _cid) if _cid is not None else None
    )
    # Producer (agentic harness) for this submission: its own agent_id,
    # falling back to the seeded "direct" agent for a row not yet backfilled.
    _aid = s.agent_id if s.agent_id is not None else _direct_agent_id(session)
    agent_row = session.get(Agent, _aid) if _aid is not None else None
    # Machine this submission was measured on (Parts 2+3). NULL on
    # LeetCode-scored submissions ("unknown machine").
    machine_row = (
        session.get(Machine, s.machine_id) if s.machine_id is not None else None
    )
    # Last-attempt timestamp — drives the SPA's "When" sort on the
    # /submissions table so a running sweep stays at the top of the
    # list while it's still landing attempts. None when the submission
    # has zero attempts (just created).
    last_attempt_at = max((a.logged_at for a in atts if a.logged_at is not None), default=None)
    # Live-recomputed total $: sum live per-attempt costs when the model
    # has pricing rows AND the attempt has prompt_tokens; fall back to the
    # snapshot cost_usd captured at submit on legacy attempts. Yields one
    # consistent total an operator can act on. Null when no attempt has
    # either a live or snapshot cost to contribute.
    _pricing = pricing if pricing is not None else _model_pricing_lookup(session)
    live_total: float | None = None
    # Per-attempt cost series — also feeds the median below.
    per_attempt_costs: list[float] = []
    for a in atts:
        live = _recompute_attempt_cost(a, s.model_name, s.model_version, _pricing)
        per_attempt = live if live is not None else a.cost_usd
        if per_attempt is not None:
            live_total = (live_total or 0.0) + per_attempt
            per_attempt_costs.append(per_attempt)
    # Per-attempt response_tokens series for the typical-spend median.
    per_attempt_tokens = [a.response_tokens for a in atts if a.response_tokens is not None]
    # Per-attempt generation-time series — same shape, used by the new
    # "Median gen time" column on the rankings + submissions tables.
    per_attempt_wall = [a.wall_clock_ms for a in atts if a.wall_clock_ms is not None]
    return SubmissionPublic(
        id=s.id, user_display_name=u.display_name if u else "?",
        user_org=u.org if u else None,
        submitter_slug=_slug(_submitter_label(u)),
        model_name=s.model_name, model_version=s.model_version, language=s.language,
        status=s.status, created_at=s.created_at,
        pass_at_1=s.pass_at_1,
        avg_runtime_ms=s.avg_runtime_ms,
        avg_memory_mb=s.avg_memory_mb,
        best_runtime_ms=s.best_runtime_ms,
        best_memory_mb=s.best_memory_mb,
        avg_runtime_percentile=s.avg_runtime_percentile,
        avg_memory_percentile=s.avg_memory_percentile,
        avg_runtime_energy_j=s.avg_runtime_energy_j,
        best_runtime_energy_j=s.best_runtime_energy_j,
        avg_runtime_energy_percentile=s.avg_runtime_energy_percentile,
        best_runtime_energy_percentile=s.best_runtime_energy_percentile,
        # Per function call rollup (B3): time/energy per function call ARE the aliased avg_runtime_*
        # columns; expose them explicitly (µs / J) + the solve count.
        avg_time_us_per_op=(s.avg_runtime_ms * 1000.0
                            if s.avg_runtime_ms is not None else None),
        avg_energy_j_per_op=s.avg_runtime_energy_j,
        avg_iterations=s.avg_iterations,
        time_human_avg=s.time_human_avg,
        time_human_expert=s.time_human_expert,
        mem_human_avg=s.mem_human_avg,
        mem_human_expert=s.mem_human_expert,
        total_solved=s.total_solved, total_problems=s.total_problems,
        n_attempts=len(atts),
        n_samples_per_problem=s.n_samples_per_problem or 1,
        pass_at_k=s.pass_at_k,
        pass_at_full=s.pass_at_full,
        fully_accepted=(
            s.total_problems > 0 and s.total_solved >= s.total_problems
        ),
        leaderboard_eligible=(reason == "eligible"),
        leaderboard_reason=reason,
        prompt_id=s.prompt_id,
        prompt_name=pt.name if pt else None,
        n_rounds=s.n_rounds or 1,
        include_hints=bool(s.include_hints),
        optimization_target=s.optimization_target,
        stop_on_accept=bool(s.stop_on_accept),
        # Prefer the LIVE total (current rates × stored tokens). When no
        # attempt had stored tokens AND the model has no pricing row,
        # `live_total` is None and we fall back to the snapshot column.
        total_cost_usd=live_total if live_total is not None else s.total_cost_usd,
        total_cost_usd_snapshot=s.total_cost_usd,
        total_response_tokens=s.total_response_tokens,
        median_response_tokens_per_attempt=_median_or_none(per_attempt_tokens),
        median_cost_usd_per_attempt=_median_or_none(per_attempt_costs),
        median_generation_ms_per_attempt=_median_or_none(per_attempt_wall),
        total_generation_ms=s.total_generation_ms,
        total_generation_energy_j=s.total_generation_energy_j,
        competition_slug=comp.slug if comp else None,
        competition_name=comp.name if comp else None,
        harness_slug=harness_row.slug if harness_row else None,
        harness_name=harness_row.name if harness_row else None,
        correctness_oracle_slug=correctness_row.slug if correctness_row else None,
        correctness_oracle_name=correctness_row.name if correctness_row else None,
        agent_slug=agent_row.slug if agent_row else None,
        agent_name=agent_row.name if agent_row else None,
        energy_tool=s.energy_tool,
        machine_id=s.machine_id,
        machine_name=machine_row.name if machine_row else None,
        run_uid=s.run_uid,
        last_attempt_at=last_attempt_at,
    )


# ---------- Register --------------------------------------------------------

@app.get("/api/captcha")
def get_captcha():
    return captcha.issue()


@app.get("/api/languages")
def list_languages(session: Session = Depends(get_session)):
    """One row per language the platform knows about. Enriched with the
    SPA's at-a-glance card metadata: paradigms list, TIOBE rank, distinct
    model count, and one row per model (its best submission by pass@k, then
    avg_runtime_ms). The /languages page re-ranks these client-side by the
    chosen metric (runtime by default, or memory / energy / pass@k), so every
    metric including execution energy rides along on each row."""
    all_subs = session.exec(
        select(Submission).where(Submission.deleted_at.is_(None))
    ).all()
    # Bucket submissions by language so we can compute n_submissions,
    # n_fully_accepted, n_distinct_models, and the per-language top-3
    # in a single pass.
    by_lang: dict[str, list[Submission]] = {}
    for s in all_subs:
        by_lang.setdefault(s.language, []).append(s)
    out = []
    for name, info in langs_mod.LANGUAGES.items():
        subs = by_lang.get(name, [])
        # "Fully accepted" = the submission solved every problem in its
        # competition. Matches the existing /languages/:name semantics.
        n_fully = sum(
            1 for s in subs
            if s.total_problems > 0 and s.total_solved >= s.total_problems
        )
        # Distinct models — same key the rest of the SPA uses (name, version).
        n_models = len({(s.model_name, s.model_version) for s in subs})
        # Top-3 by pass@k; tie-break by avg_runtime_ms ascending so a
        # faster solution wins among models with the same pass-rate.
        # `pass_at_k or 0` and `avg_runtime_ms or inf` keep rows with
        # null metrics at the bottom rather than crashing the sort.
        # One row per distinct model (its best submission by pass@k, then
        # runtime). The /languages page re-ranks these client-side by the
        # chosen metric — runtime by default, or memory / energy / pass@k —
        # so we return every model with all metrics (energy included) rather
        # than a pass@k-only top-3.
        def _rank_key(s):
            return (
                -(s.pass_at_k if s.pass_at_k is not None else -1.0),
                s.avg_runtime_ms if s.avg_runtime_ms is not None else float("inf"),
            )
        best_by_model: dict[tuple, Submission] = {}
        for s in subs:
            if s.status != SubmissionStatus.judged:
                continue
            k = (s.model_name, s.model_version)
            cur = best_by_model.get(k)
            if cur is None or _rank_key(s) < _rank_key(cur):
                best_by_model[k] = s
        top_subs = []
        for s in sorted(best_by_model.values(), key=_rank_key):
            u = session.get(User, s.user_id)
            top_subs.append({
                "submission_id": s.id,
                "model_name": s.model_name,
                "model_version": s.model_version,
                "submitter": _submitter_label(u),
                "pass_at_1": s.pass_at_1,
                "pass_at_k": s.pass_at_k,
                "avg_runtime_ms": s.avg_runtime_ms,
                "avg_memory_mb": s.avg_memory_mb,
                "avg_runtime_energy_j": s.avg_runtime_energy_j,
                "avg_runtime_percentile": s.avg_runtime_percentile,
                "avg_memory_percentile": s.avg_memory_percentile,
            })
        out.append({
            "name": name,
            "display_name": info.display_name,
            "category": info.category,
            # Card metadata for the /languages page.
            "paradigms": info.paradigms,
            "tiobe_rank_2025": info.tiobe_rank_2025,
            # Activity counts.
            "n_submissions": len(subs),
            "n_fully_accepted": n_fully,
            "n_models": n_models,
            "top_submissions": top_subs,
        })
    out.sort(key=lambda x: (-x["n_submissions"], x["name"]))
    return out


@app.get("/api/languages/{name}")
def language_detail(name: str, session: Session = Depends(get_session)):
    info = langs_mod.LANGUAGES.get(name)
    if not info:
        raise HTTPException(404, "language not found")

    subs = session.exec(
        select(Submission)
        .where(Submission.language == name)
        .where(Submission.deleted_at.is_(None))
        .order_by(Submission.created_at.desc())
    ).all()
    judged = [s for s in subs if s.status == SubmissionStatus.judged]
    fully_n = sum(1 for s in judged if s.total_problems > 0 and s.total_solved >= s.total_problems)

    # Leaderboard slice on the language page — same eligibility rules as
    # /api/rankings, including the per-submission universe (competition's
    # problem set or canonical fallback) so the language page agrees with
    # the main leaderboard on what counts as "complete".
    resolve_universe = _build_universe_resolver(session, judged)
    stats_by_sub = _attempt_stats_by_sub(session, [s.id for s in judged])
    eligible = [
        s for s in judged
        if _eligible_for_leaderboard(
            s, _attempt_stats_for(stats_by_sub, s.id)[0], resolve_universe(s),
        )
    ]
    # Return EVERY eligible submission rather than the top-5 — the SPA
    # offers a sort-by dropdown (pass@1, pass@k, runtime, memory, energy,
    # cost) and lets the user pick the ranking criterion. Eligibility
    # gating still applies so the list mirrors the leaderboard universe.
    # Default sort is pass@1 desc + runtime percentile desc, same as the
    # old "top 5" picker, so an SPA that doesn't pass a sort still sees
    # the most-relevant rows first.
    top = sorted(
        eligible,
        key=lambda s: (-(s.pass_at_1 or 0), -(s.avg_runtime_percentile or 0)),
    )
    # Single lookup so we don't re-query PromptTemplate per row.
    prompt_name_by_id = {
        pt.id: pt.name for pt in session.exec(select(PromptTemplate)).all()
    }
    top_models = [
        {
            "submission_id": s.id,
            "model_name": s.model_name,
            "model_version": s.model_version,
            "submitter": _submitter_label(session.get(User, s.user_id)),
            "submitter_slug": _slug(_submitter_label(session.get(User, s.user_id))),
            "pass_at_1": s.pass_at_1,
            "pass_at_k": s.pass_at_k,
            "avg_runtime_ms": s.avg_runtime_ms,
            "avg_memory_mb": s.avg_memory_mb,
            "avg_runtime_percentile": s.avg_runtime_percentile,
            "avg_memory_percentile": s.avg_memory_percentile,
            # Cost / energy / generation-time — additional sort axes the
            # SPA's dropdown exposes. Nullable on legacy rows; the SPA
            # treats null as "worst" when sorting ascending and "best"
            # when sorting descending so unreported rows sink to the
            # bottom regardless of direction.
            "total_cost_usd": s.total_cost_usd,
            "total_response_tokens": s.total_response_tokens,
            "total_generation_ms": s.total_generation_ms,
            "total_generation_energy_j": s.total_generation_energy_j,
            # Prompt-template attribution so the table can render which
            # registered template produced the row. Two submissions for the
            # same model+language but different prompts are different cells
            # (the matcher we wired into find_existing_submission enforces
            # this) — surfacing it here closes the loop visually.
            "prompt_id": s.prompt_id,
            "prompt_name": prompt_name_by_id.get(s.prompt_id) if s.prompt_id else None,
        }
        for s in top
    ]

    best = {
        "pass_at_1": max((s.pass_at_1 for s in judged if s.pass_at_1 is not None), default=None),
        "avg_runtime_ms": min((s.avg_runtime_ms for s in judged if s.avg_runtime_ms is not None), default=None),
        "avg_memory_mb": min((s.avg_memory_mb for s in judged if s.avg_memory_mb is not None), default=None),
        "best_runtime_ms": min((s.best_runtime_ms for s in judged if s.best_runtime_ms is not None), default=None),
        "best_memory_mb": min((s.best_memory_mb for s in judged if s.best_memory_mb is not None), default=None),
        "avg_runtime_percentile": max((s.avg_runtime_percentile for s in judged if s.avg_runtime_percentile is not None), default=None),
        "avg_memory_percentile": max((s.avg_memory_percentile for s in judged if s.avg_memory_percentile is not None), default=None),
        "time_human_expert": max((s.time_human_expert for s in judged if s.time_human_expert is not None), default=None),
        "mem_human_expert": max((s.mem_human_expert for s in judged if s.mem_human_expert is not None), default=None),
    }

    return {
        "info": langs_mod.language_info_dict(info),
        "stats": {
            "n_submissions": len(subs),
            "n_judged": len(judged),
            "n_fully_accepted": fully_n,
            "n_distinct_models": len({s.model_name for s in subs}),
            "n_distinct_submitters": len({s.user_id for s in subs}),
            "best": best,
        },
        "top_models": top_models,
    }


@app.get("/api/snapshots")
def list_snapshots(session: Session = Depends(get_session)):
    rows = session.exec(select(Snapshot).order_by(Snapshot.taken_at.desc())).all()
    return [
        {"id": s.id, "label": s.label, "taken_at": s.taken_at, "totals": s.payload.get("totals", {})}
        for s in rows
    ]


@app.get("/api/snapshots/{snapshot_id}")
def snapshot_detail(snapshot_id: int, session: Session = Depends(get_session)):
    s = session.get(Snapshot, snapshot_id)
    if not s:
        raise HTTPException(404, "snapshot not found")
    return {"id": s.id, "label": s.label, "taken_at": s.taken_at, "report": s.payload}


@app.get("/api/admin/baselines/state")
def baseline_refresh_state(
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Diagnostic: where the auto-refresher stands + breakdown of how many
    baselines are synthetic vs pulled from LeetCode, broken down by language
    so it's obvious which language's synthetic placeholders can actually be
    upgraded vs which are stuck waiting for a real-mode accepted submission.

    For each language we report:
      * `synthetic_with_anchor` — synthetic placeholders whose problem has
        an accepted run with a stored external sid. These can be upgraded
        on the next /api/admin/baselines/refresh (auth permitting).
      * `synthetic_without_anchor` — synthetic placeholders with NO
        external-sid-bearing accepted run. No amount of refresh-button
        clicking will upgrade these; they need a new submission judged in
        real-dispatch mode (ARENA_DISPATCH_ENABLED=1) with valid LeetCode
        credentials so dispatch can capture an anchor.
    """
    state = session.get(BaselineRefreshState, 1)
    counts: dict[str, int] = {}
    for b in session.exec(select(Baseline)).all():
        counts[b.source] = counts.get(b.source, 0) + 1

    # Per-language synthetic breakdown — the headline diagnostic for
    # "why isn't this getting upgraded?" complaints.
    from .dispatch import _latest_accepted_external                     # noqa: E402
    by_lang: dict[str, dict[str, int]] = {}
    for b in session.exec(
        select(Baseline).where(Baseline.source == "synthetic")
    ).all():
        bucket = by_lang.setdefault(b.language, {
            "synthetic_total": 0,
            "synthetic_with_anchor": 0,
            "synthetic_without_anchor": 0,
        })
        bucket["synthetic_total"] += 1
        if _latest_accepted_external(session, b.problem_id, b.language) is not None:
            bucket["synthetic_with_anchor"] += 1
        else:
            bucket["synthetic_without_anchor"] += 1

    # Auth pre-flight so the UI can tell "stuck because of expired
    # credentials" apart from "stuck because no anchors exist".
    from . import leetcode_dist                                          # noqa: E402
    auth_ok: bool | None = None
    auth_err: str | None = None
    try:
        c = leetcode_dist.build_client()
        c.close()
        auth_ok = True
    except leetcode_dist.AuthMissing as e:
        auth_ok = False
        auth_err = str(e)

    return {
        "last_refresh_at": state.last_refresh_at if state else None,
        "submissions_since_last": state.submissions_since_last if state else 0,
        "trigger_every_n": int(os.environ.get("ARENA_BASELINE_REFRESH_EVERY_N", "5")),
        "trigger_hours": float(os.environ.get("ARENA_BASELINE_REFRESH_HOURS", "12")),
        "stale_days": float(os.environ.get("ARENA_BASELINE_STALE_DAYS", "14")),
        "auto_refresh_enabled": os.environ.get("ARENA_AUTO_REFRESH_BASELINES", "1") != "0",
        "dispatch_real_mode": os.environ.get("ARENA_DISPATCH_ENABLED") == "1",
        "leetcode_auth_ok": auth_ok,
        "leetcode_auth_error": auth_err,
        "baselines_by_source": counts,
        "synthetic_by_language": by_lang,
    }


@app.post("/api/admin/baselines/refresh", status_code=status.HTTP_202_ACCEPTED)
def baseline_refresh_now(_: User = Depends(current_admin)) -> dict:
    """Kick off a baseline backfill in the background (~12 minutes for ~700
    pairs at 1 RPS to LeetCode) and return IMMEDIATELY with the initial
    progress snapshot. The UI polls /api/admin/baselines/refresh/progress
    while `state == 'running'` and reads off the final counts when the
    state flips to 'done'. Hitting POST again while a refresh is already
    running is a no-op that returns the in-flight state."""
    return start_refresh_in_background()


@app.get("/api/admin/baselines/refresh/progress")
def baseline_refresh_progress(_: User = Depends(current_admin)) -> dict:
    """Read-out the background baseline-refresh worker's current progress.
    The state field is one of `idle | running | done | error`; counts
    update live as the worker chews through (problem, language) pairs."""
    return get_refresh_progress()


@app.post("/api/admin/baselines", status_code=status.HTTP_200_OK)
def upsert_baselines(
    payload: BaselineBulkIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Bulk upsert LeetCode-population baseline rows produced by `pull_baselines.py`.

    For each (title_slug, language), an existing Baseline row is updated in
    place; otherwise inserted. Unknown slugs are skipped (and reported), so a
    JSON dump from an older sample doesn't crash the load.
    """
    slug_to_id = {
        p.title_slug: p.id
        for p in session.exec(select(Problem)).all()
    }
    upserted = 0
    skipped: list[str] = []
    langs_touched: set[str] = set()
    for b in payload.baselines:
        pid = slug_to_id.get(b.title_slug)
        if pid is None:
            skipped.append(f"{b.title_slug}/{b.language}")
            continue
        existing = session.exec(
            select(Baseline).where(
                Baseline.problem_id == pid,
                Baseline.language == b.language,
            )
        ).first()
        if existing is None:
            existing = Baseline(problem_id=pid, language=b.language,
                                runtime_ms_avg=0, runtime_ms_expert=0,
                                memory_mb_avg=0, memory_mb_expert=0)
        existing.runtime_ms_avg = b.runtime_ms_avg
        existing.runtime_ms_expert = b.runtime_ms_expert
        existing.memory_mb_avg = b.memory_mb_avg
        existing.memory_mb_expert = b.memory_mb_expert
        existing.source = b.source
        existing.fetched_at = datetime.utcnow()
        session.add(existing)
        upserted += 1
        langs_touched.add(b.language)
    session.commit()
    # Loading real baselines must flow into the leaderboard: recompute the
    # LeetCode-coefficient fields on judged submissions in the touched langs.
    for lang in langs_touched:
        _recompute_human_coefficients(session, lang)
    return {"upserted": upserted, "skipped": skipped, "total": len(payload.baselines),
            "languages_recomputed": sorted(langs_touched)}


@app.post("/api/admin/snapshots", status_code=status.HTTP_201_CREATED)
def take_snapshot(
    label: str | None = None,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    snap = snapshot_mod.capture(session, label=label)
    return {"id": snap.id, "label": snap.label, "taken_at": snap.taken_at}


@app.post("/api/register", status_code=status.HTTP_201_CREATED)
def register(
    req: RegisterRequest, request: Request,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Admin-only: onboard a new user account. Self-signup was retired with
    the curated-platform pivot — the team adds accounts for collaborators
    who will submit on behalf of model authors. Captcha + IP-rate-limit
    checks are still applied so the same code path is reusable if a public
    signup window is ever reopened."""
    captcha.enforce_register_ip_limit(request)
    captcha.verify(req.captcha_token, req.captcha_answer)
    if session.exec(select(User).where(User.email == req.email)).first():
        raise HTTPException(409, "email already registered")
    user = User(
        email=req.email, display_name=req.display_name,
        org=req.org, motivation=req.motivation,
        password_hash=hash_password(req.password) if req.password else None,
    )
    session.add(user)
    session.commit()
    return {"status": "pending", "message": "Awaiting admin approval."}


@app.post("/api/login", response_model=LoginResponse)
def login(req: LoginRequest, session: Session = Depends(get_session)):
    """Email + password → returns the user's API key (which the frontend then
    stores and uses for subsequent Bearer-auth calls). Same key the user could
    paste in by hand — login is just the friendlier UX."""
    user = session.exec(select(User).where(User.email == req.email)).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "invalid email or password")
    if user.status != UserStatus.approved:
        raise HTTPException(403, "account is not approved yet")
    # Return a short-lived SESSION token, not the API key. API keys are hashed
    # at rest and cannot be handed back; the browser authenticates with a
    # session, while scripts use the (separately issued) API key. The token
    # goes in the api_key field so the existing client stores and sends it as
    # the bearer unchanged, and current_user routes it to the session path.
    return LoginResponse(
        email=user.email, display_name=user.display_name, org=user.org,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        api_key=issue_session(user.id),
    )


@app.post("/api/me/api-key/regenerate")
def regenerate_api_key(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Issues a fresh API key and revokes the previous one. The caller MUST
    swap the new key into their client immediately — the current Bearer dies
    on this call."""
    new_key = generate_api_key()
    user.api_key = hash_api_key(new_key)
    session.add(user); session.commit()
    return {"api_key": new_key}


@app.delete("/api/me/api-key", status_code=status.HTTP_204_NO_CONTENT)
def discard_api_key(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Clears the user's API key. Programmatic access is blocked until the
    user logs in again (which auto-issues a fresh key) or hits /regenerate."""
    user.api_key = None
    session.add(user); session.commit()
    return None


# ---------- Gated (any approved user) --------------------------------------

@app.get("/api/me")
def me(user: User = Depends(current_user)) -> dict:
    return {
        "email": user.email, "display_name": user.display_name,
        "org": user.org, "role": user.role,
    }


@app.get("/api/problems/{slug}", response_model=ProblemDetail)
def problem_detail(slug: str, session: Session = Depends(get_session)):
    p = session.exec(select(Problem).where(Problem.title_slug == slug)).first()
    if not p:
        raise HTTPException(404, "not found")

    # Per-attempt aggregates. Each attempt has up to 5 dispatch runs; the
    # Attempt model carries pre-computed best (min ms / max %ile) and avg
    # (mean across the 5 runs) of those metrics. Headline ranking and
    # display use BEST — the per-run percentile field is noisy enough that
    # picking a single run produces the kind of paradox the user reported
    # (see docs/leetcode-percentile-semantics.md).
    attempts = session.exec(select(Attempt).where(Attempt.problem_id == p.id)).all()
    runs_by_attempt: dict[int, list[Run]] = {}
    testcases: int | None = None
    for a in attempts:
        runs = session.exec(
            select(Run).where(Run.attempt_id == a.id).order_by(Run.created_at.desc())
        ).all()
        if not runs:
            continue
        runs_by_attempt[a.id] = runs
        latest = runs[0]
        if latest.total_testcases is not None:
            testcases = max(testcases or 0, latest.total_testcases)

    n_attempts = len(runs_by_attempt)
    # An attempt counts as accepted iff its final_status was Accepted (Attempt
    # already aggregates the majority verdict across runs).
    n_accepted = sum(1 for a in attempts
                     if a.id in runs_by_attempt and a.final_status == "Accepted")

    rows: list[tuple[Attempt, list[Run], Submission]] = []
    for a in attempts:
        runs = runs_by_attempt.get(a.id)
        if not runs:
            continue
        sub = session.get(Submission, a.submission_id)
        if sub is None or sub.status != SubmissionStatus.judged or sub.deleted_at is not None:
            continue
        rows.append((a, runs, sub))

    def sort_key(row: tuple[Attempt, list[Run], Submission]):
        a, _, _s = row
        # Accepted first, then by BEST runtime %ile descending (highest
        # of the 5 runs), then by best runtime ms ascending. Sorting by
        # best instead of by a single arbitrary run's number means a model
        # whose code can hit a top-bucket runtime — even if other runs
        # were noisier — ranks ahead of one whose runs were all middling.
        accepted_first = 0 if a.final_status == "Accepted" else 1
        return (
            accepted_first,
            -(a.best_runtime_percentile or 0),
            a.best_runtime_ms if a.best_runtime_ms is not None else 1e9,
        )

    rows.sort(key=sort_key)

    def _row_to_top(a: Attempt, runs: list[Run], sub: Submission) -> ProblemTopSolution:
        u = session.get(User, sub.user_id)
        label = _submitter_label(u)
        # status_msg for the row: use Attempt.final_status if present (it's
        # the majority verdict across runs); fall back to the most recent
        # run's individual status_msg.
        latest = runs[0]
        status = a.final_status or latest.status_msg
        return ProblemTopSolution(
            submission_id=sub.id,
            model_name=sub.model_name, model_version=sub.model_version,
            language=sub.language,
            submitter=label,
            submitter_slug=_slug(label),
            status_msg=status,
            runtime_ms=a.best_runtime_ms,
            memory_mb=a.best_memory_mb,
            runtime_percentile=a.best_runtime_percentile,
            memory_percentile=a.best_memory_percentile,
            avg_runtime_ms=a.avg_runtime_ms,
            avg_memory_mb=a.avg_memory_mb,
            avg_runtime_percentile=a.avg_runtime_percentile,
            avg_memory_percentile=a.avg_memory_percentile,
            runtime_energy_j=a.best_runtime_energy_j,
            avg_runtime_energy_j=a.avg_runtime_energy_j,
            runtime_energy_percentile=a.best_runtime_energy_percentile,
            avg_runtime_energy_percentile=a.avg_runtime_energy_percentile,
            n_runs=a.n_runs,
            n_accepted_runs=a.n_accepted_runs,
        )

    top: list[ProblemTopSolution] = [_row_to_top(*row) for row in rows[:10]]

    # Per-language: best single solution + attempt/accepted counts.
    n_attempts_by_lang: dict[str, int] = {}
    n_accepted_by_lang: dict[str, int] = {}
    best_in_lang: dict[str, tuple[Attempt, list[Run], Submission]] = {}
    for row in rows:
        a, _runs, sub = row
        n_attempts_by_lang[sub.language] = n_attempts_by_lang.get(sub.language, 0) + 1
        if a.final_status == "Accepted":
            n_accepted_by_lang[sub.language] = n_accepted_by_lang.get(sub.language, 0) + 1
        if sub.language not in best_in_lang or \
           sort_key(row) < sort_key(best_in_lang[sub.language]):
            best_in_lang[sub.language] = row
    best_per_language = sorted(
        (_row_to_top(*row) for row in best_in_lang.values()),
        key=lambda s: (
            0 if s.status_msg == "Accepted" else 1,
            -(s.runtime_percentile or 0),
            s.runtime_ms or 1e9,
        ),
    )

    # If this is an anti-contamination variant, look up the original so the
    # SPA can render a "Variant of X" badge. One cheap lookup; null on
    # canonical problems (vast majority).
    original_slug = original_title = None
    if p.original_problem_id is not None:
        original = session.get(Problem, p.original_problem_id)
        if original is not None:
            original_slug = original.title_slug
            original_title = original.title

    # Competition memberships — the detail endpoint had been omitting these,
    # so the variant-page chip in the Problems list didn't carry over.
    comp_links = session.exec(
        select(CompetitionProblem, Competition)
        .where(CompetitionProblem.problem_id == p.id)
        .where(CompetitionProblem.competition_id == Competition.id)
    ).all()
    competitions = [
        CompetitionRef(slug=c.slug, name=c.name) for _, c in comp_links
    ]
    competitions.sort(key=lambda c: c.name.lower())

    return ProblemDetail(
        id=p.id, title_slug=p.title_slug, title=p.title, level=p.level,
        tags=[t.strip() for t in p.tags.split(",") if t.strip()],
        competitions=competitions,
        content=p.content,
        hints=p.hints,
        code_snippets=p.code_snippets or {},
        testcases=testcases,
        n_attempts=n_attempts,
        n_accepted=n_accepted,
        top_solutions=top,
        best_per_language=best_per_language,
        n_attempts_by_language=n_attempts_by_lang,
        n_accepted_by_language=n_accepted_by_lang,
        original_slug=original_slug,
        original_title=original_title,
        original_function_name=p.original_function_name,
        function_name=p.function_name,
    )


def _supersede_pending_attempts_for_slugs(
    session: Session, sub: Submission, slug_to_id: dict[str, int],
    incoming: list[AttemptIn],
) -> int:
    """For every slug in the incoming append, hard-DELETE any phantom
    pending attempt in this submission for that slug. The caller's POST
    explicitly says "abandon the queued one and try again" — same
    semantics the dispatch worker would reach by itself after
    ATTEMPT_STALE_AFTER_S, but immediately.

    A "phantom pending" attempt is one with `n_runs == 0` AND
    `dispatch_warning IS NULL` AND `prefailed_reason IS NULL`. Those
    are the ones the budget check would otherwise count, blocking the
    retry with "sample budget exceeded".

    Why hard-delete instead of stamping a sentinel: the retry POST
    triggers a BackgroundTask that dispatches every "pending" attempt
    (no Run rows). If the phantom is still in the table, it gets
    dispatched alongside the new attempt, breaking the k=1 budget
    cap silently. Deletion is the only race-proof option.

    No audit loss: a phantom by definition never reached LeetCode, has
    no Run rows, and its trace is preserved on the new (replacing)
    attempt's POST. The append-only provenance hash chain still has
    its leaf entry from the original POST — that's the tamper-evidence
    record, not the Attempt row.

    Returns the number of rows deleted. Real verdicts (n_runs > 0) and
    already-warned/prefailed phantoms are untouched."""
    incoming_pids = {
        slug_to_id[a.title_slug] for a in incoming
        if a.title_slug in slug_to_id
    }
    if not incoming_pids:
        return 0
    candidates = session.exec(
        select(Attempt).where(
            Attempt.submission_id == sub.id,
            Attempt.problem_id.in_(incoming_pids),
            (Attempt.n_runs == 0) | (Attempt.n_runs.is_(None)),
            Attempt.dispatch_warning.is_(None),
            Attempt.prefailed_reason.is_(None),
        )
    ).all()
    if not candidates:
        return 0
    for a in candidates:
        session.delete(a)
    session.commit()
    return len(candidates)


def _reject_oversampling(
    session: Session, sub: Submission, slug_to_id: dict[str, int],
    incoming: list[AttemptIn],
) -> None:
    """Enforce the sample budget: per problem, the total attempts
    (existing + incoming) must never exceed `n_samples_per_problem × n_rounds`.

    Single-shot (n_rounds=1) → exactly the legacy pass@k cap. Refinement
    chains (n_rounds=N) get N times the budget per problem so each round
    can submit a fresh code attempt for the same slug. The chain link
    itself (previous_attempt_id) is validated separately in _ingest_attempts.

    System-failure attempts DO NOT count against the budget. An attempt
    with `dispatch_warning` set and `n_runs == 0` never received a LeetCode
    verdict — it represents a retry the sweep should be allowed to make.
    Counting it would lock the operator out of recovering from auth/pool
    outages with a "sample budget exceeded" 400, even though the slot the
    failed attempt is meant to occupy is still effectively vacant."""
    k = sub.n_samples_per_problem or 1
    n_rounds = sub.n_rounds or 1
    cap = k * n_rounds
    existing = session.exec(
        select(Attempt).where(Attempt.submission_id == sub.id)
    ).all()
    counts: dict[int, int] = {}
    for a in existing:
        # Phantom attempts (no runs + dispatch_warning) are retry slots.
        # Don't burn the budget on them.
        if (a.n_runs or 0) == 0 and a.dispatch_warning:
            continue
        counts[a.problem_id] = counts.get(a.problem_id, 0) + 1
    overflows: list[str] = []
    for a in incoming:
        pid = slug_to_id.get(a.title_slug)
        if pid is None:
            continue          # unknown slug already rejected upstream
        counts[pid] = counts.get(pid, 0) + 1
        if counts[pid] > cap:
            budget_desc = f"k={k}" if n_rounds == 1 else f"k×n_rounds = {k}×{n_rounds} = {cap}"
            overflows.append(
                f"{a.title_slug}: would be sample #{counts[pid]} but submission's "
                f"budget is {budget_desc}"
            )
    if overflows:
        raise HTTPException(400, {
            "detail": "sample budget exceeded",
            "errors": overflows,
        })


def _provider_from_params(params: dict | None) -> str:
    """Best-effort guess at the provider from a submission's model_params, for
    seeding the auto-stub RegisteredModel.provider field. Admins can correct
    it later via the edit form. Defaults to 'unknown' so the row never blocks
    the list query on a NOT NULL constraint."""
    if isinstance(params, dict):
        prov = params.get("provider") or params.get("source")
        if isinstance(prov, str) and prov.strip():
            return prov.strip().lower()
    return "unknown"


def _ensure_registered_model_stub(
    session: Session, name: str, version: str,
    *, provider_hint: str = "unknown", user_id: int | None = None,
) -> None:
    """If no RegisteredModel exists for (name, version), create a minimal
    stub with status='unverified'. Admins fill in description, type, family,
    and provenance later via the /models edit form. Called from every
    submission ingest path so the registry reflects every model the platform
    has ever seen — `/models` then renders all of them."""
    existing = session.exec(
        select(RegisteredModel)
        .where(RegisteredModel.name == name, RegisteredModel.version == version)
    ).first()
    if existing is not None:
        return
    session.add(RegisteredModel(
        name=name, version=version, provider=provider_hint,
        status="unverified", added_by_user_id=user_id,
    ))
    session.commit()


def _backfill_registered_model_stubs_from_submissions() -> None:
    """One-time sweep: for every distinct (model_name, model_version) the
    Submission table has ever seen, make sure a RegisteredModel row exists.

    Auto-stub on the POST /api/submissions path only triggers for NEW
    submissions, so anything pre-dating that feature (qwen runs, older
    benchmarks) needs this sweep to show up on /models. Idempotent —
    submissions whose pair is already registered are skipped, so re-running
    on every startup is cheap and safe.

    The provider is guessed from the submission's model_params (same hint
    auto-stub uses); admins refine the rest of the metadata in the UI."""
    from sqlmodel import Session
    with Session(engine) as session:
        # Distinct (name, version) → pick any one submission's params + user
        # so the inserted stub has a plausible provider hint and creator.
        seen: dict[tuple[str, str], tuple[dict | None, int | None]] = {}
        for s in session.exec(
            select(Submission).where(Submission.deleted_at.is_(None))
        ).all():
            key = (s.model_name, s.model_version)
            if key in seen:
                continue
            seen[key] = (s.model_params, s.user_id)

        if not seen:
            return

        # Existing pairs — fetch once so we skip the per-pair query inside
        # the loop.
        registered = {
            (rm.name, rm.version)
            for rm in session.exec(select(RegisteredModel)).all()
        }
        added = 0
        for (name, version), (params, user_id) in seen.items():
            if (name, version) in registered:
                continue
            session.add(RegisteredModel(
                name=name, version=version,
                provider=_provider_from_params(params),
                status="unverified",
                added_by_user_id=user_id,
            ))
            added += 1
        if added:
            session.commit()
            print(f"[startup] backfilled {added} RegisteredModel stub(s) "
                  f"from existing submissions",
                  file=sys.stderr)


def _reject_invalid_attempts(attempts: list[AttemptIn], language: str) -> None:
    """Fast sanity gate: reject empty/stub/placeholder code before anything is
    persisted. This is NOT a correctness check — only the judge decides whether
    a solution actually solves its problem.

    Attempts the client has already flagged via `prefailed_reason` are exempt:
    the client KNOWS the response isn't a valid solution and wants the failure
    recorded with the raw model response preserved in `trace`. The validator
    would otherwise reject the malformed text and we'd lose the data point."""
    errors = [
        f"{a.title_slug}: {reason}"
        for a in attempts
        if a.prefailed_reason is None
        and (reason := validate_code(language, a.code)) is not None
    ]
    if errors:
        raise HTTPException(400, {"detail": "rejected attempts", "errors": errors})


_ERROR_FIELDS = (
    "runtime_error", "full_runtime_error", "compile_error", "full_compile_error",
    "last_testcase", "expected_output", "code_output", "std_output",
    "note", "raw_stdout", "raw_stderr",
    # verbatim handler/validator error string (arena-local /validate + /measure):
    # the full-suite validator's VALIDATE line, a compile tail, etc.
    "error_log",
)


def _build_error_log(raw: dict | None, status_msg: str | None) -> RunErrorLog | None:
    """Pluck the diagnostic fields out of a Run.raw_response. Returns None when
    the run was Accepted or there's nothing useful to surface."""
    if not raw or status_msg == "Accepted":
        return None
    # Some fields might be in raw["input"] (older LeetCode field for last_testcase).
    payload = {k: raw.get(k) for k in _ERROR_FIELDS if raw.get(k)}
    if not payload.get("last_testcase") and raw.get("input"):
        payload["last_testcase"] = raw["input"]
    return RunErrorLog(**payload) if payload else None


def _ingest_attempts(
    session: Session, sub: Submission, attempts: list[AttemptIn],
    slug_to_id: dict[str, int],
) -> None:
    """Persist attempts with provenance: content-binding hash, model digest,
    optional signature verification, then anchor each in the append-only
    transparency log (server-timestamped, hash-chained).

    Refinement-chain integrity: when a payload sets `previous_attempt_id`,
    we verify the referenced attempt belongs to this same submission AND
    the same problem before accepting. Otherwise a client could spoof a
    chain across submissions / problems.
    """
    # Validate every previous_attempt_id up-front (single query batch).
    prev_ids = {a.previous_attempt_id for a in attempts if a.previous_attempt_id}
    if prev_ids:
        prev_atts = session.exec(
            select(Attempt).where(Attempt.id.in_(prev_ids))  # type: ignore[arg-type]
        ).all()
        by_id = {a.id: a for a in prev_atts}
        for a in attempts:
            if a.previous_attempt_id is None:
                continue
            prev = by_id.get(a.previous_attempt_id)
            if prev is None:
                raise HTTPException(400,
                    f"previous_attempt_id={a.previous_attempt_id} does not exist")
            if prev.submission_id != sub.id:
                raise HTTPException(400,
                    f"previous_attempt_id={a.previous_attempt_id} belongs to a "
                    f"different submission (#{prev.submission_id})")
            if prev.problem_id != slug_to_id[a.title_slug]:
                raise HTTPException(400,
                    f"previous_attempt_id={a.previous_attempt_id} is for a "
                    f"different problem than this attempt's title_slug")

    created: list[tuple[Attempt, str]] = []
    for a in attempts:
        leaf = provenance.compute_trace_hash(a.code, a.inference_trace, a.trace)
        digest, dsrc = provenance.derive_model_digest(
            sub.model_name, sub.model_version, sub.model_params,
            a.trace, a.model_digest,
        )
        sig_status = provenance.verify_signature(a.trace_signer, a.trace_sig, leaf)
        att = Attempt(
            submission_id=sub.id,
            problem_id=slug_to_id[a.title_slug],
            code=a.code,
            inference_trace=a.inference_trace,
            trace=a.trace,
            wall_clock_ms=a.wall_clock_ms,
            cost_usd=a.cost_usd,
            trace_hash=leaf,
            trace_sig=a.trace_sig,
            trace_signer=a.trace_signer,
            sig_status=sig_status,
            model_digest=digest,
            model_digest_source=dsrc,
            round_index=a.round_index,
            previous_attempt_id=a.previous_attempt_id,
            response_tokens=a.response_tokens,
            prompt_tokens=a.prompt_tokens,
            generation_energy_j=a.generation_energy_j,
            prefailed_reason=a.prefailed_reason,
        )
        session.add(att)
        created.append((att, leaf))
    session.commit()
    for att, leaf in created:
        session.refresh(att)
        entry = provenance.append_to_log(session, att.id, leaf)
        att.log_seq = entry.seq
        att.logged_at = entry.logged_at
        session.add(att)
    session.commit()
    # Roll up cost + token totals across ALL the submission's attempts (the
    # newly-ingested ones plus any from earlier appends). Done at ingest, not
    # dispatch, because the figures are known the moment the client sends them.
    _recompute_submission_costs(session, sub)


def _recompute_submission_costs(session: Session, sub: Submission) -> None:
    """Re-sum cost_usd + response_tokens + generation wall-clock over every
    attempt of `sub`.

    Convention: if NO attempt has a value for a field, the total stays None
    ("not reported") rather than 0 — so a submission predating this feature is
    distinguishable from one that genuinely cost $0. Once at least one attempt
    reports a value, the total is the sum of the present values (missing ones
    treated as 0)."""
    attempts = session.exec(
        select(Attempt).where(Attempt.submission_id == sub.id)
    ).all()
    costs = [a.cost_usd for a in attempts if a.cost_usd is not None]
    toks = [a.response_tokens for a in attempts if a.response_tokens is not None]
    gen_ms = [a.wall_clock_ms for a in attempts if a.wall_clock_ms is not None]
    energy = [a.generation_energy_j for a in attempts if a.generation_energy_j is not None]
    sub.total_cost_usd = round(sum(costs), 6) if costs else None
    sub.total_response_tokens = sum(toks) if toks else None
    sub.total_generation_ms = sum(gen_ms) if gen_ms else None
    sub.total_generation_energy_j = round(sum(energy), 4) if energy else None
    session.add(sub)
    session.commit()


@app.post("/api/submissions", status_code=status.HTTP_202_ACCEPTED)
def submit_batch(
    payload: SubmissionIn,
    bg: BackgroundTasks,
    user: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Admin-only: register a new submission batch. The platform is curated —
    the team submits on behalf of model authors who've granted access; there
    is no public self-submission path. The same enforce_submission_limit
    rate-limit still runs so a runaway admin script can't spam the queue."""
    enforce_submission_limit(user, session)

    # Resolve the competition scope first — every other validation below
    # restricts to its problem set. The default of `leetcode-99-perf`
    # matches the legacy "canonical 99 problems" sweep behaviour for
    # callers that don't explicitly target a competition.
    comp_slug = (payload.competition_slug or "leetcode-99-perf").strip()
    comp = session.exec(
        select(Competition).where(Competition.slug == comp_slug)
    ).first()
    if comp is None:
        raise HTTPException(
            400, f"competition_slug={comp_slug!r} does not exist")
    comp_problem_ids = {
        cp.problem_id for cp in session.exec(
            select(CompetitionProblem).where(
                CompetitionProblem.competition_id == comp.id
            )
        ).all()
    }
    # Problem-slug → id, RESTRICTED to the competition. Slug lookups for
    # problems outside the scope fall through to the "unknown slug" 400.
    comp_problems = session.exec(
        select(Problem).where(Problem.id.in_(comp_problem_ids))
    ).all() if comp_problem_ids else []
    slug_to_id = {p.title_slug: p.id for p in comp_problems}
    bad = [a.title_slug for a in payload.attempts if a.title_slug not in slug_to_id]
    if bad:
        raise HTTPException(
            400,
            f"problem slugs not in competition {comp_slug!r}: {bad[:5]}",
        )
    _reject_invalid_attempts(payload.attempts, payload.language)

    # Validate optional prompt_id reference up-front so a bad id surfaces as
    # a 400 instead of a stored dangling FK.
    if payload.prompt_id is not None:
        if session.get(PromptTemplate, payload.prompt_id) is None:
            raise HTTPException(400, f"prompt_id={payload.prompt_id} does not exist")

    # Optional per-submission harness override. When set, this submission
    # dispatches through the named harness regardless of the competition's
    # default binding — lets a client run parallel sweeps against
    # different perf oracles from one endpoint.
    submission_harness_id: int | None = None
    if payload.harness_slug:
        h = session.exec(
            select(Harness).where(Harness.slug == payload.harness_slug)
        ).first()
        if h is None:
            raise HTTPException(
                400, f"harness_slug={payload.harness_slug!r} does not exist")
        # Compat enforcement — the competition's admin allow-list must
        # include this harness. Empty list means the operator hasn't
        # blessed any harness yet; the check surfaces both the
        # empty-list and the "not-in-list" cases with the same clear
        # error so the client knows exactly what to fix.
        compat = (comp.compatible_harness_slugs or [])
        if payload.harness_slug not in compat:
            raise HTTPException(
                400,
                f"harness {payload.harness_slug!r} is not compatible with "
                f"competition {comp.slug!r} (allowed: {compat or 'none — '
                'add one on /competitions admin form'})"
            )
        submission_harness_id = h.id
    # Same shape for the correctness oracle. Independent of the perf
    # harness — see dispatch._resolve_effective_correctness_oracle for
    # the layering used at dispatch time.
    submission_correctness_oracle_id: int | None = None
    if payload.correctness_oracle_slug:
        o = session.exec(
            select(CorrectnessOracle)
            .where(CorrectnessOracle.slug == payload.correctness_oracle_slug)
        ).first()
        if o is None:
            raise HTTPException(
                400,
                f"correctness_oracle_slug={payload.correctness_oracle_slug!r} "
                "does not exist")
        submission_correctness_oracle_id = o.id

    # Optional producer (agentic harness / workflow). Null resolves to the
    # seeded "direct" agent, so a legacy client that omits the field lands
    # exactly where every pre-feature submission is backfilled — no behavior
    # change. A named-but-unknown slug is a 400, same shape as harness.
    if payload.agent_slug:
        ag = session.exec(
            select(Agent).where(Agent.slug == payload.agent_slug)
        ).first()
        if ag is None:
            raise HTTPException(
                400, f"agent_slug={payload.agent_slug!r} does not exist")
        submission_agent_id = ag.id
    else:
        submission_agent_id = _direct_agent_id(session)

    sub = Submission(
        user_id=user.id, model_name=payload.model_name,
        model_version=payload.model_version, language=payload.language,
        prompt_template=payload.prompt_template, model_params=payload.model_params,
        n_samples_per_problem=payload.n_samples_per_problem,
        prompt_id=payload.prompt_id,
        n_rounds=payload.n_rounds,
        include_hints=payload.include_hints,
        optimization_target=payload.optimization_target,
        stop_on_accept=payload.stop_on_accept,
        competition_id=comp.id,
        harness_id=submission_harness_id,
        correctness_oracle_id=submission_correctness_oracle_id,
        agent_id=submission_agent_id,
        total_problems=len(comp_problems),
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)

    # Auto-stub a RegisteredModel row for first-seen (name, version) pairs so
    # /models can list every model that ever submitted answers. Admins fill in
    # the rich metadata (description, type, family, provenance) later.
    _ensure_registered_model_stub(
        session, payload.model_name, payload.model_version,
        provider_hint=_provider_from_params(payload.model_params),
        user_id=user.id,
    )

    _reject_oversampling(session, sub, slug_to_id, payload.attempts)
    _ingest_attempts(session, sub, payload.attempts, slug_to_id)

    if payload.attempts:
        bg.add_task(dispatch_submission, sub.id)
    return {"submission_id": sub.id, "status": sub.status, "attempts_received": len(payload.attempts)}


@app.post("/api/submissions/{submission_id}/attempts", status_code=status.HTTP_202_ACCEPTED)
def append_attempts(
    submission_id: int,
    payload: AppendAttemptsIn,
    bg: BackgroundTasks,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Admin-only: append attempts to an existing submission. Same admin-only
    rationale as POST /api/submissions — adding to the curated set is reserved
    for the team."""
    sub = session.get(Submission, submission_id)
    if not sub or sub.deleted_at is not None:
        raise HTTPException(404, "submission not found")

    # Scope slug lookup to the submission's competition (when set) so
    # appends can't smuggle in a problem outside the original scope —
    # otherwise pass@k denominators would silently drift from "k out of N"
    # to "k out of N+rogue". Legacy submissions with competition_id NULL
    # keep the all-problems behaviour.
    if sub.competition_id is not None:
        comp_problem_ids = {
            cp.problem_id for cp in session.exec(
                select(CompetitionProblem).where(
                    CompetitionProblem.competition_id == sub.competition_id
                )
            ).all()
        }
        scoped_problems = session.exec(
            select(Problem).where(Problem.id.in_(comp_problem_ids))
        ).all() if comp_problem_ids else []
        slug_to_id = {p.title_slug: p.id for p in scoped_problems}
        bad = [a.title_slug for a in payload.attempts if a.title_slug not in slug_to_id]
        if bad:
            raise HTTPException(
                400,
                f"problem slugs not in submission's competition: {bad[:5]}",
            )
    else:
        all_problems = session.exec(select(Problem)).all()
        slug_to_id = {p.title_slug: p.id for p in all_problems}
        bad = [a.title_slug for a in payload.attempts if a.title_slug not in slug_to_id]
        if bad:
            raise HTTPException(400, f"unknown problem slugs: {bad[:5]}")
    _reject_invalid_attempts(payload.attempts, sub.language)
    # Supersede ANY phantom pending attempts for the slugs we're about
    # to append. The operator's POST is an explicit "retry" signal —
    # the budget check below would otherwise count the phantom and
    # reject with "sample budget exceeded" even when no LC verdict
    # ever landed for it.
    n_superseded = _supersede_pending_attempts_for_slugs(
        session, sub, slug_to_id, payload.attempts,
    )
    if n_superseded:
        print(f"[append] superseded {n_superseded} phantom pending "
              f"attempt(s) on submission #{sub.id}", file=sys.stderr)
    _reject_oversampling(session, sub, slug_to_id, payload.attempts)

    _ingest_attempts(session, sub, payload.attempts, slug_to_id)
    bg.add_task(dispatch_submission, sub.id)
    return {"submission_id": sub.id, "appended": len(payload.attempts), "status": sub.status}


def _progress_for(sub: Submission, session: Session) -> SubmissionProgress:
    """Per-PROBLEM progress: multiple attempts for the same problem collapse
    to one bucket. A problem is counted accepted iff any attempt was accepted;
    queued iff no attempt is accepted yet but at least one is still pending;
    rejected iff every attempt for it ran and none was accepted."""
    all_slugs = {p.title_slug: p.id for p in session.exec(select(Problem)).all()}
    id_to_slug = {pid: slug for slug, pid in all_slugs.items()}
    attempts = session.exec(select(Attempt).where(Attempt.submission_id == sub.id)).all()

    by_pid: dict[int, list[Attempt]] = {}
    for a in attempts:
        by_pid.setdefault(a.problem_id, []).append(a)

    accepted_pids: set[int] = set()
    queued_pids: set[int] = set()
    rejected_pids: set[int] = set()
    for pid, atts in by_pid.items():
        any_queued = False
        any_accepted = False
        any_judged = False
        for a in atts:
            latest = session.exec(
                select(Run).where(Run.attempt_id == a.id).order_by(Run.created_at.desc())
            ).first()
            if latest is None:
                any_queued = True
                continue
            any_judged = True
            if latest.status_msg == "Accepted":
                any_accepted = True
        if any_accepted:
            accepted_pids.add(pid)
        elif any_queued:
            queued_pids.add(pid)
        elif any_judged:
            rejected_pids.add(pid)

    attempted_pids = set(by_pid)
    missing = sorted(set(all_slugs) - {id_to_slug[p] for p in attempted_pids})
    return SubmissionProgress(
        submission_id=sub.id,
        language=sub.language,
        attempted=len(attempted_pids),
        accepted=len(accepted_pids),
        queued=len(queued_pids),
        total_supported=len(all_slugs),
        fully_accepted=len(accepted_pids) == len(all_slugs),
        missing=missing,
        rejected=sorted(id_to_slug[p] for p in rejected_pids),
    )


@app.get("/api/submissions/{submission_id}/progress", response_model=SubmissionProgress)
def submission_progress(submission_id: int, session: Session = Depends(get_session)):
    sub = session.get(Submission, submission_id)
    if not sub or sub.deleted_at is not None:
        raise HTTPException(404, "submission not found")
    return _progress_for(sub, session)


@app.get("/api/submissions/{submission_id}/attempts",
         response_model=list[AttemptResult])
def submission_attempts(submission_id: int, session: Session = Depends(get_session)):
    """Per-attempt outcomes for one (possibly ongoing) submission: each
    attempt's final verdict, accepted-run ratio, avg/best metrics, and every
    individual dispatch run. Attempts with no runs yet report status 'queued'.
    Ordered worst-first (rejected, then queued, then accepted) so the things
    that still need fixing surface at the top."""
    sub = session.get(Submission, submission_id)
    if not sub or sub.deleted_at is not None:
        raise HTTPException(404, "submission not found")
    attempts = session.exec(
        select(Attempt).where(Attempt.submission_id == submission_id)
    ).all()
    # Per-attempt transparency-log entry — same join submission_detail does.
    log_by_attempt: dict[int, TraceLogEntry] = {
        e.attempt_id: e
        for e in session.exec(
            select(TraceLogEntry).where(
                TraceLogEntry.attempt_id.in_([a.id for a in attempts])
            )
        ).all()
    } if attempts else {}
    # One pricing snapshot for the whole response — keeps the per-row
    # `_recompute_attempt_cost` calls O(1) lookups and avoids hammering
    # the RegisteredModel table N times. The map is small (≪ 1000 rows).
    _pricing = _model_pricing_lookup(session)
    # Per-(problem, language, harness) runtime baselines for the too-fast-outlier
    # flag (accepted solutions that ran implausibly faster than their peers —
    # usually a skipped workload). Grouping by harness keeps the comparison within
    # one measurement basis. Computed once for the whole response.
    from . import integrity
    _speed_baselines = integrity.speed_baselines(session)
    out: list[AttemptResult] = []
    for a in attempts:
        p = session.get(Problem, a.problem_id)
        runs = session.exec(
            select(Run).where(Run.attempt_id == a.id).order_by(Run.run_index)
        ).all()
        if not runs:
            # No Run rows. Distinguish the states a runless attempt can be in, so
            # a client-prefailed or already-verdicted attempt is not mislabelled as
            # still-in-flight "queued" (which makes the SPA look like there are 20
            # active submissions when nothing's actually moving, and hid the
            # wrong_language_extracted prefailures that looked "queued").
            if a.prefailed_reason:
                # The client rejected the extracted output before dispatch
                # (e.g. wrong_language_extracted). It is a failed attempt.
                status = a.prefailed_reason
            elif a.dispatch_warning:
                # Auto-failed by the stale-marker, or stamped by the dispatcher
                # when LeetCode auth blew up.
                status = "Dispatch failed"
            elif a.final_status:
                # Has a terminal verdict but its Run rows are gone (a measured-
                # but-unpersisted attempt). Show the verdict, not "queued".
                status = a.final_status
            else:
                status = "queued"
        else:
            status = a.final_status or "Pending"
        _so, _sr, _, _ = integrity.is_outlier(
            a.best_runtime_ms,
            _speed_baselines.get((a.problem_id, sub.language, sub.harness_id), []))
        out.append(AttemptResult(
            attempt_id=a.id,
            problem_slug=p.title_slug if p else "?",
            problem_title=p.title if p else "?",
            level=p.level if p else "?",
            tags=(p.tags or "") if p else "",
            status=status,
            accepted=a.final_status == "Accepted",
            round_index=a.round_index or 0,
            previous_attempt_id=a.previous_attempt_id,
            cost_usd=a.cost_usd,
            response_tokens=a.response_tokens,
            prompt_tokens=a.prompt_tokens,
            cost_usd_live=_recompute_attempt_cost(
                a, sub.model_name, sub.model_version, _pricing
            ) if sub is not None else None,
            wall_clock_ms=a.wall_clock_ms,
            generation_energy_j=a.generation_energy_j,
            prefailed_reason=a.prefailed_reason,
            dispatch_warning=a.dispatch_warning,
            n_runs=a.n_runs,
            n_accepted_runs=a.n_accepted_runs,
            avg_runtime_ms=a.avg_runtime_ms,
            best_runtime_ms=a.best_runtime_ms,
            avg_memory_mb=a.avg_memory_mb,
            best_memory_mb=a.best_memory_mb,
            avg_runtime_percentile=a.avg_runtime_percentile,
            avg_memory_percentile=a.avg_memory_percentile,
            avg_runtime_energy_j=a.avg_runtime_energy_j,
            best_runtime_energy_j=a.best_runtime_energy_j,
            speed_outlier=_so,
            speed_ratio=(round(_sr, 1) if _sr is not None else None),
            submitted_at=a.logged_at,
            runs=[AttemptRunDetail(
                run_index=r.run_index,
                status_msg=r.status_msg,
                runtime_ms=r.runtime_ms,
                memory_mb=r.memory_mb,
                runtime_percentile=r.runtime_percentile,
                memory_percentile=r.memory_percentile,
                runtime_energy_j=r.runtime_energy_j,
                energy_j_per_op=r.energy_j_per_op,
                time_us_per_op=r.time_us_per_op,
                total_correct=r.total_correct,
                total_testcases=r.total_testcases,
                error_log=_build_error_log(r.raw_response, r.status_msg),
                # Verbatim LC payload — only on non-Accepted runs, so the
                # diagnostic blob isn't shipped on the happy path.
                leetcode_full_response=(
                    r.leetcode_full_response
                    if r.status_msg != "Accepted" else None
                ),
            ) for r in runs],
            inference_trace=a.inference_trace,
            trace=a.trace,
            provenance={
                "trace_hash": a.trace_hash,
                "sig_status": a.sig_status,
                "trace_signer": a.trace_signer,
                "model_digest": a.model_digest,
                "model_digest_source": a.model_digest_source,
                "log_seq": a.log_seq,
                "logged_at": a.logged_at.isoformat() if a.logged_at else None,
                "prev_hash": log_by_attempt.get(a.id).prev_hash if log_by_attempt.get(a.id) else None,
                "entry_hash": log_by_attempt.get(a.id).entry_hash if log_by_attempt.get(a.id) else None,
                "leaf_hash": (log_by_attempt.get(a.id).leaf_hash
                              if log_by_attempt.get(a.id) else a.trace_hash),
            },
        ))
    order = {"queued": 1}  # rejected = 0, queued = 1, accepted = 2
    out.sort(key=lambda x: (
        2 if x.accepted else order.get(x.status, 0), x.problem_slug
    ))
    return out


@app.get("/api/me/progress", response_model=list[ProgressEntry])
def my_progress(user: User = Depends(current_user), session: Session = Depends(get_session)):
    subs = session.exec(
        select(Submission)
        .where(Submission.user_id == user.id)
        .where(Submission.deleted_at.is_(None))
        .order_by(Submission.created_at.desc())
    ).all()
    out: list[ProgressEntry] = []
    for s in subs:
        p = _progress_for(s, session)
        out.append(ProgressEntry(
            submission_id=s.id, language=s.language,
            model_name=s.model_name, model_version=s.model_version,
            attempted=p.attempted, accepted=p.accepted, queued=p.queued,
            total_supported=p.total_supported,
            missing_count=len(p.missing),
            fully_accepted=p.fully_accepted,
            created_at=s.created_at,
        ))
    return out


@app.get("/api/datasets/solutions")
def download_solutions(
    language: str | None = None,
    level: str | None = None,
    model: str | None = None,
    only_accepted: bool = False,
    session: Session = Depends(get_session),
):
    """Stream every solution + metrics as JSONL — public so the community
    can verify the leaderboard end-to-end. Soft-deleted submissions are kept
    in the database (for audit / hash-chain integrity) but excluded from this
    research export."""
    sub_q = (
        select(Submission)
        .where(Submission.status == SubmissionStatus.judged)
        .where(Submission.deleted_at.is_(None))
    )
    if language:
        sub_q = sub_q.where(Submission.language == language)
    if model:
        sub_q = sub_q.where(Submission.model_name == model)
    subs = session.exec(sub_q).all()

    problem_cache: dict[int, Problem] = {}
    user_cache: dict[int, User] = {}
    # Producer (agent) lookup — tiny table, one round-trip. Legacy rows that
    # the seed backfill hasn't reached yet fall back to the "direct" slug so
    # the export field is never spuriously null.
    agents_by_id: dict[int, Agent] = {a.id: a for a in session.exec(select(Agent)).all()}
    _direct_aid_export = next(
        (a.id for a in agents_by_id.values() if a.slug == "direct"), None,
    )

    def iter_records():
        for sub in subs:
            user = user_cache.get(sub.user_id)
            if user is None:
                user = user_cache[sub.user_id] = session.get(User, sub.user_id)
            attempts = session.exec(
                select(Attempt).where(Attempt.submission_id == sub.id)
            ).all()
            for a in attempts:
                p = problem_cache.get(a.problem_id)
                if p is None:
                    p = problem_cache[a.problem_id] = session.get(Problem, a.problem_id)
                if level and p.level != level:
                    continue
                runs = session.exec(
                    select(Run).where(Run.attempt_id == a.id).order_by(Run.created_at.desc())
                ).all()
                run = runs[0] if runs else None
                if only_accepted and (run is None or run.status_msg != "Accepted"):
                    continue
                _aid = sub.agent_id if sub.agent_id is not None else _direct_aid_export
                _agent = agents_by_id.get(_aid) if _aid is not None else None
                record = {
                    "submission_id": sub.id,
                    "submitted_at": sub.created_at.isoformat(),
                    "submitter": _submitter_label(user),
                    "submitter_slug": _slug(_submitter_label(user)),
                    "model_name": sub.model_name,
                    "model_version": sub.model_version,
                    # Producer that drove the model: the agentic-harness slug,
                    # or "direct" for a one-shot prompt. Additive field — older
                    # consumers that don't read it are unaffected.
                    "agent": _agent.slug if _agent else None,
                    "language": sub.language,
                    "model_params": sub.model_params,
                    "prompt_template": sub.prompt_template,
                    "problem_slug": p.title_slug,
                    "problem_title": p.title,
                    "problem_level": p.level,
                    "problem_tags": [t.strip() for t in p.tags.split(",") if t.strip()],
                    "code": a.code,
                    "inference_trace": a.inference_trace,
                    "trace": a.trace,
                    "trace_hash": a.trace_hash,
                    "sig_status": a.sig_status,
                    "trace_signer": a.trace_signer,
                    "model_digest": a.model_digest,
                    "model_digest_source": a.model_digest_source,
                    "log_seq": a.log_seq,
                    "logged_at": a.logged_at.isoformat() if a.logged_at else None,
                    "wall_clock_ms": a.wall_clock_ms,
                    "cost_usd": a.cost_usd,
                    "status_msg": run.status_msg if run else "queued",
                    "runtime_ms": run.runtime_ms if run else None,
                    "memory_mb": run.memory_mb if run else None,
                    "runtime_percentile": run.runtime_percentile if run else None,
                    "memory_percentile": run.memory_percentile if run else None,
                    "total_correct": run.total_correct if run else None,
                    "total_testcases": run.total_testcases if run else None,
                }
                yield json.dumps(record, ensure_ascii=False) + "\n"

    return StreamingResponse(
        iter_records(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="arena_solutions.jsonl"'},
    )


@app.get("/api/me/submissions", response_model=list[SubmissionPublic])
def my_submissions(user: User = Depends(current_user), session: Session = Depends(get_session)):
    subs = session.exec(
        select(Submission)
        .where(Submission.user_id == user.id)
        .where(Submission.deleted_at.is_(None))
        .order_by(Submission.created_at.desc())
    ).all()
    return [_submission_public(s, session) for s in subs]


@app.get("/api/me/by-language")
def my_by_language(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Per-language matrix of the user's work: across ALL the user's
    submissions in a given language, the union of attempts per problem with
    the best status (accepted-first, then most-recent). Lets a competitor
    see at a glance what they have in python vs cpp vs java side-by-side."""
    subs = session.exec(
        select(Submission)
        .where(Submission.user_id == user.id)
        .where(Submission.deleted_at.is_(None))
        .order_by(Submission.created_at.desc())
    ).all()
    all_problems = session.exec(select(Problem)).all()
    id_to_problem = {p.id: p for p in all_problems}

    by_lang_subs: dict[str, list[Submission]] = {}
    for s in subs:
        by_lang_subs.setdefault(s.language, []).append(s)

    out: list[dict] = []
    for lang, lang_subs in by_lang_subs.items():
        sub_ids = [s.id for s in lang_subs]
        attempts = session.exec(
            select(Attempt).where(Attempt.submission_id.in_(sub_ids))
        ).all()
        by_pid: dict[int, list[Attempt]] = {}
        for a in attempts:
            by_pid.setdefault(a.problem_id, []).append(a)

        problems_out: list[dict] = []
        n_attempted = 0
        n_accepted = 0
        for pid, p in id_to_problem.items():
            atts = by_pid.get(pid, [])
            if not atts:
                problems_out.append({
                    "problem_slug": p.title_slug, "problem_title": p.title,
                    "level": p.level, "status": None, "accepted": False,
                    "n_attempts": 0, "submission_id": None,
                    "attempt_id": None, "runtime_ms": None, "memory_mb": None,
                })
                continue
            n_attempted += 1
            accepted_atts = [a for a in atts if a.final_status == "Accepted"]
            if accepted_atts:
                n_accepted += 1
                best = max(accepted_atts, key=lambda a: (
                    (a.n_accepted_runs / a.n_runs) if a.n_runs else 0.0,
                    -(a.avg_runtime_ms if a.avg_runtime_ms is not None else 1e18),
                ))
            else:
                best = max(atts, key=lambda a: a.id)
            problems_out.append({
                "problem_slug": p.title_slug, "problem_title": p.title,
                "level": p.level,
                "status": best.final_status or "queued",
                "accepted": best.final_status == "Accepted",
                "n_attempts": len(atts),
                "submission_id": best.submission_id,
                "attempt_id": best.id,
                "runtime_ms": best.avg_runtime_ms,
                "memory_mb": best.avg_memory_mb,
            })

        def _sort_key(p: dict) -> tuple:
            if p["accepted"]: return (0, p["problem_slug"])
            if p["status"]:   return (1, p["problem_slug"])
            return (2, p["problem_slug"])
        problems_out.sort(key=_sort_key)

        out.append({
            "language": lang,
            "submissions": [
                _submission_public(s, session).model_dump() for s in lang_subs
            ],
            "n_supported": len(all_problems),
            "n_attempted": n_attempted,
            "n_accepted": n_accepted,
            "problems": problems_out,
        })
    out.sort(key=lambda x: (-x["n_accepted"], x["language"]))
    return out


# ---------- Admin -----------------------------------------------------------

from . import export_import                                              # noqa: E402

# Tables you can pick on /admin/export. Exposed so the frontend can render
# the right set of checkboxes without hard-coding the list twice.
# DB tables PLUS the file-backed `leetcode_accounts` slot — the SPA
# renders one checkbox per entry. LC accounts are handled by a special
# branch inside export_import.export_json / import_bundle (they're a
# JSON file on disk, not a SQLModel) and ship with cookies REDACTED
# unless the operator passes include_credentials=true.
_EXPORTABLE_TABLE_NAMES = list(export_import.ALL_EXPORT_NAMES)


@app.get("/api/admin/export/tables")
def admin_export_tables(_: User = Depends(current_admin)) -> dict:
    """List the table names the export/import endpoints accept. The order
    here matches the import dependency order so a UI rendering checkboxes
    can preserve a sensible top-to-bottom flow (users → … → runs)."""
    return {"tables": _EXPORTABLE_TABLE_NAMES}


@app.get("/api/admin/export")
def admin_export(
    tables: str | None = Query(
        None,
        description="comma-separated table names; omit for all. See "
                    "/api/admin/export/tables for the menu.",
    ),
    format: str = Query("json", regex="^(json|csv)$"),
    include_credentials: bool = Query(
        False,
        description="When `leetcode_accounts` is selected, include the raw "
                    "session cookies in the bundle. DEFAULT FALSE — cookies "
                    "are redacted unless this flag is explicitly true. The "
                    "bundle becomes a secret when set, so callers must treat "
                    "it accordingly (encrypt at rest, do not commit to git).",
    ),
    compress: str = Query(
        "none", pattern="^(none|gzip)$",
        description="JSON only: `gzip` streams the bundle gzip-compressed "
                    "(.json.gz), several times smaller than plain JSON.",
    ),
    exclude_columns: str | None = Query(
        None,
        description="JSON only: comma-separated column names to drop from "
                    "every row, e.g. trace,inference_trace,raw_response,"
                    "leetcode_full_response. Such a slim bundle is for "
                    "analysis; an import fills the dropped columns with NULL.",
    ),
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Download an export bundle of the selected tables. JSON returns a
    single envelope round-trippable through /api/admin/import; CSV returns
    a zip with one CSV per table (human-inspection only — CSV loses the
    JSON-column structure)."""
    requested = (
        [t.strip() for t in tables.split(",") if t.strip()]
        if tables else list(_EXPORTABLE_TABLE_NAMES)
    )
    unknown = [t for t in requested if t not in _EXPORTABLE_TABLE_NAMES]
    if unknown:
        raise HTTPException(400, {"detail": "unknown tables", "unknown": unknown})
    drop = {c.strip() for c in (exclude_columns or "").split(",") if c.strip()}
    unknown_cols = sorted(drop - export_import.export_columns())
    if unknown_cols:
        raise HTTPException(400, {"detail": "unknown columns", "unknown": unknown_cols})

    if format == "json":
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        # Stream row-by-row (own session inside the generator) so a large DB
        # doesn't build the whole bundle + its json string in memory and
        # OOM-kill the container. Rows go out in ~1 MB pieces.
        chunks = export_import.batched(export_import.export_json_iter(
            requested, include_credentials=include_credentials, exclude_columns=drop))
        if compress == "gzip":
            return StreamingResponse(
                export_import.gzip_stream(chunks),
                media_type="application/gzip",
                headers={"Content-Disposition": f"attachment; filename=perfarena-export-{stamp}.json.gz"},
            )
        return StreamingResponse(
            (chunk.encode() for chunk in chunks),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=perfarena-export-{stamp}.json"},
        )
    # CSV → zip.
    blob = export_import.export_csv_zip(
        session, requested, include_credentials=include_credentials,
    )
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([blob]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=perfarena-export-{stamp}.zip"},
    )


@app.post("/api/admin/import")
async def admin_import(
    request: Request,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Merge another instance's export bundle into THIS database.

    Body: the JSON envelope produced by GET /api/admin/export?format=json.
    Returns per-table counts of inserted / skipped / errored rows.

    Semantics (see `app.export_import`):
      * Natural-key tables (users, problems, prompts, models, competitions,
        blog posts, baselines) — skip rows whose natural key already exists
        locally; admin curation wins.
      * Surrogate-key tables (submissions, attempts, runs) — always insert
        with new local ids and remap FKs as we go, so two replicas with
        overlapping integer ids merge cleanly.
      * TraceLogEntry / Snapshot are NOT imported — the hash chain is per
        instance by construction and merging it would break verification.
    """
    try:
        body = await request.json()
    except Exception as e:                                              # noqa: BLE001
        raise HTTPException(400, f"body must be valid JSON: {e}")
    try:
        result = export_import.import_bundle(session, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ---------- External-harness measurement ingest ---------------------------
#
# Offline import path for results produced by harnesses that DON'T go
# through the interactive attempt/dispatch flow (e.g. the perfarena-local
# harness that measures execution energy + wall-clock on real hardware).
# One POST creates the whole Submission + Attempts + Runs graph in the
# right shape so the leaderboard math, energy columns, and head-to-head
# views treat it identically to a LeetCode-dispatched submission.


@app.post("/api/admin/import-harness-measurements",
          status_code=status.HTTP_201_CREATED)
async def import_harness_measurements(
    request: Request,
    admin: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Ingest one model's full measurement bundle from an external harness.

    Expected body shape:
    {
      "harness_slug": "arena-local",           # perf oracle (Harness)
      "harness_name": "PerfArena local",       # display name if creating
      "correctness_oracle_slug": "arena-local",# correctness oracle
      "correctness_oracle_name": "PerfArena local",
      "competition_slug": "leetcode-99-perf",
      "model_name": "qwen3:4b",
      "model_version": "ollama",
      "language": "python3",
      "prompt_template": "(external harness — no prompt)",
      "measurements": [
        {
          "title_slug": "2-keys-keyboard",
          "code": "…solution source…",
          "runtime_ms": 0.1039,                # median wall-clock per call
          "runtime_energy_j": 0.000486,        # median CPU energy per call
          "n_test_cases": 65,                  # informational
          "accepted": true                     # verdict from the harness
        },
        …
      ]
    }

    The perf + correctness oracle rows are UPSERTED (idempotent). One
    Submission is created scoped to the given competition; one Attempt per
    measurement (final_status derived from `accepted`); one Run per Attempt
    carrying the runtime_ms + runtime_energy_j + harness_id. Attempt and
    Submission aggregates are computed inline so the leaderboard math is
    valid the instant the response returns.
    """
    try:
        body = await request.json()
    except Exception as e:                                                  # noqa: BLE001
        raise HTTPException(400, f"body must be valid JSON: {e}")

    for required in ("model_name", "model_version", "language",
                     "measurements"):
        if required not in body:
            raise HTTPException(400, f"missing field: {required}")
    if not isinstance(body["measurements"], list) or not body["measurements"]:
        raise HTTPException(400, "measurements must be a non-empty list")

    comp_slug = body.get("competition_slug") or "leetcode-99-perf"
    comp = session.exec(
        select(Competition).where(Competition.slug == comp_slug)
    ).first()
    if comp is None:
        raise HTTPException(404, f"competition '{comp_slug}' not found")

    # Upsert the two oracles. Uses the given slug + name; anything else on
    # the row (description, config) stays at whatever the admin filled in
    # via the SPA. Only touch `kind`+`name` when we CREATE the row so an
    # operator's edits on an existing seed aren't clobbered by an import.
    def _upsert_perf_harness(slug: str, name: str | None) -> Harness:
        h = session.exec(select(Harness).where(Harness.slug == slug)).first()
        if h is None:
            h = Harness(
                slug=slug,
                name=name or slug,
                kind="arena-local" if "local" in slug else "custom",
                description=(
                    "Auto-created by /api/admin/import-harness-measurements. "
                    "Edit description + config on the /harnesses page."
                ),
                status="active",
                created_by_user_id=admin.id,
            )
            session.add(h)
            session.commit()
            session.refresh(h)
        return h

    def _upsert_correctness(slug: str, name: str | None) -> CorrectnessOracle:
        o = session.exec(
            select(CorrectnessOracle).where(CorrectnessOracle.slug == slug)
        ).first()
        if o is None:
            o = CorrectnessOracle(
                slug=slug,
                name=name or slug,
                kind="arena-local" if "local" in slug else "custom",
                description=(
                    "Auto-created by /api/admin/import-harness-measurements. "
                    "Edit description + config on /correctness-oracles."
                ),
                status="active",
                created_by_user_id=admin.id,
            )
            session.add(o)
            session.commit()
            session.refresh(o)
        return o

    harness = _upsert_perf_harness(
        body.get("harness_slug") or "arena-local",
        body.get("harness_name"),
    )
    correctness = _upsert_correctness(
        body.get("correctness_oracle_slug") or "arena-local",
        body.get("correctness_oracle_name"),
    )

    # Resolve every incoming slug to a problem.id. Reject unknown slugs
    # upfront rather than silently dropping them mid-ingest.
    slug_to_pid: dict[str, int] = {
        p.title_slug: p.id
        for p in session.exec(select(Problem)).all()
    }
    unknown = [m["title_slug"] for m in body["measurements"]
               if m.get("title_slug") not in slug_to_pid]
    if unknown:
        raise HTTPException(400, {
            "detail": "unknown problem slugs — seed them first via "
                      "`python -m app.seed` or the parquet import",
            "unknown_slugs": unknown[:20],
            "total_unknown": len(unknown),
        })

    # The COMPETITION's problem count is the denominator for pass@1 (not
    # the size of the measurement bundle). A model that ships a bundle
    # covering 44 of the 99 competition problems, all Accepted, has
    # pass@1 = 44/99 ≈ 44.4% — NOT 100%. Also lets us tag out-of-
    # competition measurements so they don't inflate `total_solved`.
    comp_pids: set[int] = {
        cp.problem_id for cp in session.exec(
            select(CompetitionProblem).where(
                CompetitionProblem.competition_id == comp.id
            )
        ).all()
    }
    if not comp_pids:
        raise HTTPException(400, {
            "detail": f"competition '{comp_slug}' has no problems linked "
                      f"— add them via /competitions before importing",
        })

    # Create the submission. Every field the SPA reads gets populated up
    # front (status=judged, aggregates computed after we insert attempts).
    # total_problems = COMPETITION size, NOT bundle size — so the pass@1
    # denominator matches every other submission on the platform.
    sub = Submission(
        user_id=admin.id,
        model_name=body["model_name"],
        model_version=body["model_version"],
        language=body["language"],
        prompt_template=body.get("prompt_template") or "(external harness)",
        model_params=body.get("model_params") or {},
        status=SubmissionStatus.judged,
        competition_id=comp.id,
        n_samples_per_problem=1,
        harness_id=harness.id,
        correctness_oracle_id=correctness.id,
        total_problems=len(comp_pids),
    )
    session.add(sub); session.commit(); session.refresh(sub)

    # Per-attempt aggregates we'll roll up onto the Submission afterwards.
    all_rt: list[float] = []
    all_en: list[float] = []
    n_solved_in_comp = 0    # counts only problems that are in the competition
    n_bundle_solved = 0     # informational — includes out-of-competition
    n_out_of_competition = 0
    now = datetime.utcnow()
    for m in body["measurements"]:
        pid = slug_to_pid[m["title_slug"]]
        in_comp = pid in comp_pids
        accepted = bool(m.get("accepted", True))
        rt = m.get("runtime_ms")
        en = m.get("runtime_energy_j")
        mem = m.get("memory_mb")
        att = Attempt(
            submission_id=sub.id,
            problem_id=pid,
            code=m.get("code") or "",
            inference_trace="(external harness — no LLM trace)",
            trace=None,
            round_index=0,
            n_runs=1,
            n_accepted_runs=1 if accepted else 0,
            final_status="Accepted" if accepted else "Wrong Answer",
            avg_runtime_ms=rt,
            best_runtime_ms=rt,
            avg_memory_mb=mem,
            best_memory_mb=mem,
            avg_runtime_energy_j=en,
            best_runtime_energy_j=en,
            logged_at=now,
        )
        session.add(att); session.flush()          # populate att.id
        # One Run per Attempt — that's how many the harness ran; if the
        # operator wants to encode more per-case variance later they can
        # extend this endpoint to accept a `runs` list per measurement.
        run = Run(
            attempt_id=att.id,
            run_index=1,
            status_msg="Accepted" if accepted else "Wrong Answer",
            runtime_ms=rt,
            memory_mb=mem,
            runtime_energy_j=en,
            total_correct=m.get("n_test_cases"),
            total_testcases=m.get("n_test_cases"),
            harness_id=harness.id,
            correctness_oracle_id=correctness.id,
        )
        session.add(run)
        if accepted:
            n_bundle_solved += 1
            if in_comp:
                n_solved_in_comp += 1
            # Aggregates over accepted attempts regardless of scope so
            # avg_runtime / avg_energy reflect the model's actual measured
            # perf, not a competition-filtered subset. If you want the
            # competition-only slice for research, filter in analysis.
            if rt is not None:
                all_rt.append(rt)
            if en is not None:
                all_en.append(en)
        if not in_comp:
            n_out_of_competition += 1
    session.commit()

    # Submission-level rollup — pass@1 = in-competition accepted / competition
    # size. pass@k and pass@full are identical here (single-sample, external
    # harness). n_samples_per_problem=1 → pass_at_k denominator is the same.
    denom = len(comp_pids)
    sub.total_solved = n_solved_in_comp
    sub.pass_at_1 = n_solved_in_comp / denom
    sub.pass_at_k = sub.pass_at_1
    sub.pass_at_full = sub.pass_at_1
    sub.avg_runtime_ms = _mean_or_none(all_rt)
    sub.best_runtime_ms = min(all_rt) if all_rt else None
    sub.avg_runtime_energy_j = _mean_or_none(all_en)
    sub.best_runtime_energy_j = min(all_en) if all_en else None
    session.add(sub); session.commit(); session.refresh(sub)

    return {
        "submission_id": sub.id,
        "harness_slug": harness.slug,
        "correctness_oracle_slug": correctness.slug,
        "competition_slug": comp.slug,
        "competition_size": denom,
        "n_measurements": len(body["measurements"]),
        "n_solved_in_competition": n_solved_in_comp,
        "n_solved_in_bundle": n_bundle_solved,
        "n_out_of_competition_measurements": n_out_of_competition,
        "avg_runtime_ms": sub.avg_runtime_ms,
        "avg_runtime_energy_j": sub.avg_runtime_energy_j,
        "pass_at_1": sub.pass_at_1,
    }


# ---------- LeetCode credential pool management ----------------------------
#
# Thin HTTP shell over app.leetcode_accounts so admins can manage the pool
# from the SPA. The CLI (`python -m app.leetcode_accounts …`) remains the
# canonical interface — these endpoints just forward to the same functions
# under admin auth, so behaviour stays consistent and we don't fork logic.

from . import leetcode_accounts as _lc_accounts


def _build_lc_status_response(*, probe: bool = False) -> LeetCodeAccountsResponse:
    """Snapshot the pool. If `probe=True` we additionally hit LC's
    userStatus GraphQL once per account — slow (5 accounts ≈ 25-50 s
    worst case) so it's only called from the dedicated probe endpoint."""
    s = _lc_accounts.pool_status()
    accts_by_name = {a.name: a for a in _lc_accounts.load_accounts()}
    rows: list[LeetCodeAccountStatus] = []
    for raw in s["accounts"]:
        row = LeetCodeAccountStatus(
            name=raw["name"],
            is_exhausted=raw["is_exhausted"],
            exhausted_until=raw["exhausted_until"],
            last_used_at=raw.get("last_used_at"),
        )
        if probe:
            acc = accts_by_name.get(raw["name"])
            if acc is not None:
                ok, info = _lc_accounts.whoami(acc)
                row.is_signed_in = ok
                row.probe_message = info
                # `info` looks like "signed in as v69KdKlFf8" on success or
                # a freeform error string on failure; extract username from
                # the success shape so the UI can show it discretely.
                if ok and "signed in as " in info:
                    row.username = info.split("signed in as ", 1)[1].strip()
        rows.append(row)
    # Surface the server's configured per-account inter-submission gap so
    # external pacing clients (matrix_sweep, mostly) don't have to assume
    # a value. Imported lazily to avoid a circular import at module-load.
    from .dispatch import INTER_DELAY as _inter_delay
    return LeetCodeAccountsResponse(
        total=s["total"],
        available=s["available"],
        exhausted=s["exhausted"],
        earliest_available_after=s["earliest_available_after"],
        min_gap_s=_inter_delay,
        accounts=rows,
    )


@app.get("/api/admin/leetcode-accounts", response_model=LeetCodeAccountsResponse)
def admin_list_leetcode_accounts(
    _: User = Depends(current_admin),
):
    """List the credential pool — fast, no LC probe. Use /probe to refresh
    signed-in status for each account (more expensive)."""
    return _build_lc_status_response(probe=False)


@app.post("/api/admin/leetcode-accounts/probe", response_model=LeetCodeAccountsResponse)
def admin_probe_leetcode_accounts(
    _: User = Depends(current_admin),
):
    """Probe LC's userStatus for every account in the pool. Returns the
    same shape as the LIST endpoint but with is_signed_in / username /
    probe_message populated. Slow (N × ~5 s) — call sparingly."""
    return _build_lc_status_response(probe=True)


@app.post("/api/admin/leetcode-accounts", response_model=LeetCodeAccountsResponse,
          status_code=status.HTTP_201_CREATED)
def admin_add_leetcode_account(
    body: LeetCodeAccountIn,
    _: User = Depends(current_admin),
):
    """Add (or update by name) a credential set.

    Accepts EITHER:
      * `session_token` + `csrf_token` — the two cookies copied out of
        DevTools → Application → Cookies → leetcode.com. Preferred.
      * `cookie_header` — full `Cookie:` request header pasted from
        DevTools Network panel. Legacy back-compat for CLI callers.

    Either way, the stored cookie_header is always assembled as
    "LEETCODE_SESSION=...; csrftoken=..." so downstream readers
    (leetbot.py, etc.) see one canonical shape."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    session_token = (body.session_token or "").strip()
    csrf_token = (body.csrf_token or "").strip()

    if session_token or csrf_token:
        # Split-field path. Both are required when one is provided so we
        # don't silently store a half-formed credential set.
        if not session_token or not csrf_token:
            raise HTTPException(
                400,
                "both session_token and csrf_token are required when using "
                "the split fields (got one, missing the other).",
            )
        cookie = f"LEETCODE_SESSION={session_token}; csrftoken={csrf_token}"
    else:
        # Legacy full-header path.
        cookie = (body.cookie_header or "").strip()
        if "LEETCODE_SESSION=" not in cookie or "csrftoken=" not in cookie:
            raise HTTPException(
                400,
                "supply either session_token + csrf_token, OR a cookie_header "
                "that contains LEETCODE_SESSION=… and csrftoken=…",
            )

    _lc_accounts.add_account(name, cookie)
    return _build_lc_status_response(probe=False)


@app.delete("/api/admin/leetcode-accounts/{name}", status_code=status.HTTP_204_NO_CONTENT)
def admin_remove_leetcode_account(
    name: str,
    _: User = Depends(current_admin),
):
    """Drop an account from the pool. 404 if no such name."""
    ok = _lc_accounts.remove_account(name)
    if not ok:
        raise HTTPException(404, f"no pool account named {name!r}")
    return


@app.post("/api/admin/leetcode-accounts/{name}/clear-exhaustion",
          response_model=LeetCodeAccountsResponse)
def admin_clear_leetcode_account_exhaustion(
    name: str,
    _: User = Depends(current_admin),
):
    """Manually clear the 24h cooldown timer on an account. Useful when
    you've confirmed the rate limit has actually rolled over and want the
    pool to start picking the account again without waiting for the timer
    to expire on its own. Pass name='*' to clear ALL accounts at once."""
    if name == "*":
        _lc_accounts.clear_exhaustion(None)
    else:
        # `clear_exhaustion` is a no-op for unknown names; treat that as
        # 404 here so the UI can show a clear error message.
        accounts = {a.name for a in _lc_accounts.load_accounts()}
        if name not in accounts:
            raise HTTPException(404, f"no pool account named {name!r}")
        _lc_accounts.clear_exhaustion(name)
    return _build_lc_status_response(probe=False)


_DELETABLE_SYSTEM_FAILURE_STATUSES = {
    "Auth failed",
    "Dispatch error",
    "Dispatch timeout",
    "Dispatch parse error",
    "LeetCode transient error",
    "Rate limited",
}


@app.post("/api/admin/attempts/purge-failed", status_code=status.HTTP_200_OK)
def admin_purge_failed_attempts(
    _: User = Depends(current_admin),
):
    """Hard-DELETE every attempt that's stuck in a system-failure state.
    Two flavours both qualify:

      1. Modern (post-dispatch-warning): `dispatch_warning IS NOT NULL`
         AND `n_runs ∈ {0, NULL}`. Never received any LeetCode verdict.

      2. Legacy (pre-dispatch-warning, when the old dispatcher synthesised
         5 fake failure runs per attempt): every Run row for the attempt
         has a status_msg in the system-failure set (Auth failed,
         Dispatch error, Dispatch timeout, Dispatch parse error, LeetCode
         transient error, Rate limited). These are the "44 synthetic
         failure attempts under #46" pattern — the runs look real but
         they never reached LC.

    Cleanup includes the Run rows pointing at each deleted attempt so the
    schema stays consistent (SQLite doesn't cascade by default).

    Real verdicts (any Accepted/WA/TLE/RE/MLE/CE run) and client-side
    prefailed attempts are NEVER touched — those carry audit value.

    Idempotent: re-running on a clean DB returns `deleted: 0`."""
    deleted_ids: list[int] = []
    with Session(engine) as session:
        # Flavour 1 — modern dispatch_warning'd phantoms.
        flavour1 = session.exec(
            select(Attempt).where(
                Attempt.dispatch_warning.isnot(None),
                ((Attempt.n_runs == 0) | (Attempt.n_runs.is_(None))),
            )
        ).all()

        # Flavour 2 — legacy attempts where ALL Run rows are
        # system-failure status_msgs. We have to inspect runs per attempt
        # because the predicate "ALL runs have status X" doesn't map onto
        # one SELECT cleanly. Bounded query: pulls candidates that have
        # at least ONE such run, then filters down in Python by checking
        # that EVERY run is a system failure.
        candidate_ids = {
            r.attempt_id for r in session.exec(
                select(Run).where(
                    Run.status_msg.in_(_DELETABLE_SYSTEM_FAILURE_STATUSES)
                )
            ).all()
            if r.attempt_id is not None
        }
        flavour2: list[Attempt] = []
        for aid in candidate_ids:
            runs = session.exec(select(Run).where(Run.attempt_id == aid)).all()
            if not runs:
                continue
            if all(r.status_msg in _DELETABLE_SYSTEM_FAILURE_STATUSES
                   for r in runs):
                att = session.get(Attempt, aid)
                if att is not None:
                    flavour2.append(att)

        # De-dup (an attempt could match both flavours).
        to_delete: dict[int, Attempt] = {a.id: a for a in (*flavour1, *flavour2)}

        for aid, a in to_delete.items():
            # Wipe the Run rows first so the SQL DELETE on attempt doesn't
            # trip a foreign-key check on databases that enforce them.
            for r in session.exec(select(Run).where(Run.attempt_id == aid)).all():
                session.delete(r)
            deleted_ids.append(aid)
            session.delete(a)
        if to_delete:
            session.commit()
    return {
        "deleted": len(deleted_ids),
        "deleted_attempt_ids": deleted_ids[:100],   # cap for response size
        "by_flavour": {
            "dispatch_warning_phantoms": len([
                a for a in flavour1 if a.id in to_delete
            ]),
            "all_runs_system_failure_legacy": len([
                a for a in flavour2 if a.id in to_delete
            ]),
        },
    }


@app.get("/api/admin/attempts/pending")
def admin_list_pending_attempts(_: User = Depends(current_admin)):
    """Every attempt that has not finalized yet, across all submissions, newest
    first. These are the queued / Pending / dispatch-warning rows that make up
    the backend queue. Powers the admin Pending page (view state and delete)."""
    from .models import Attempt, Competition, Problem, Submission
    out: list[dict] = []
    with Session(engine) as session:
        comp_slug = {c.id: c.slug for c in session.exec(select(Competition)).all()}
        rows = session.exec(
            select(Attempt)
            .where(Attempt.final_status.is_(None))
            .order_by(Attempt.logged_at.desc())
        ).all()
        subs: dict[int, Submission | None] = {}
        prob_slug: dict[int, str] = {}
        for a in rows:
            if a.submission_id not in subs:
                subs[a.submission_id] = session.get(Submission, a.submission_id)
            sub = subs[a.submission_id]
            if sub is None or sub.deleted_at is not None:
                continue
            if a.problem_id not in prob_slug:
                p = session.get(Problem, a.problem_id)
                prob_slug[a.problem_id] = p.title_slug if p else "?"
            if a.n_runs == 0:
                state = "Dispatch failed" if a.dispatch_warning else "queued"
            else:
                state = "Pending"
            out.append({
                "attempt_id": a.id,
                "submission_id": a.submission_id,
                "model": sub.model_name,
                "language": sub.language,
                "competition_slug": comp_slug.get(sub.competition_id),
                "problem_slug": prob_slug[a.problem_id],
                "state": state,
                "n_runs": a.n_runs,
                "round_index": a.round_index,
                "logged_at": a.logged_at.isoformat() if a.logged_at else None,
                "dispatch_warning": a.dispatch_warning,
            })
    return {"pending": out, "count": len(out)}


@app.delete("/api/admin/attempts/{attempt_id}", status_code=status.HTTP_200_OK)
def admin_delete_attempt(
    attempt_id: int,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Admin-only hard delete of a SINGLE attempt and its Run rows.

    Unlike purge-failed (which only clears system-failure phantoms and
    preserves real verdicts), this removes whatever attempt the operator points
    at — e.g. a solution that failed on a since-fixed harness bug — so a re-run
    re-measures the problem cleanly. Deleting an Accepted attempt is allowed;
    it is the operator's call. SQLite has no cascade, so the Run rows go first."""
    att = session.get(Attempt, attempt_id)
    if att is None:
        raise HTTPException(404, "attempt not found")
    n_runs = 0
    for r in session.exec(select(Run).where(Run.attempt_id == attempt_id)).all():
        session.delete(r)
        n_runs += 1
    session.delete(att)
    session.commit()
    return {"deleted_attempt_id": attempt_id, "deleted_runs": n_runs}


@app.post("/api/admin/attempts/mark-stale", status_code=status.HTTP_200_OK)
def admin_mark_stale_attempts(
    _: User = Depends(current_admin),
):
    """On-demand version of the background stale-marker. Useful when
    you've just bumped ARENA_ATTEMPT_STALE_AFTER_S or want to drain
    visible "queued" rows before waiting for the next periodic pass.
    Also triggers the phantom-GC pass so old dispatch-failed rows can
    be cleared in the same call."""
    n = _mark_stale_pending_attempts()
    gc = _gc_old_phantom_attempts()
    return {
        "marked": n,
        "threshold_seconds": ATTEMPT_STALE_AFTER_S,
        "gc_deleted": gc,
        "gc_threshold_seconds": ATTEMPT_GC_AFTER_S,
    }


@app.post("/api/admin/submissions/{submission_id}/dispatch",
          status_code=status.HTTP_202_ACCEPTED)
def admin_redispatch_submission(
    submission_id: int,
    bg: BackgroundTasks,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Re-trigger the dispatch worker on a submission. Used by the sweep
    client when it has waited out a credential-pool exhaustion: pending
    attempts (those with no Run rows — including ones that hit
    `dispatch_warning` on the previous run) get re-processed from scratch.

    Returns 202 immediately; the worker runs in the background and the
    sweep keeps polling /api/submissions/{id}/attempts to see the verdict
    or a fresh dispatch_warning. Idempotent: a submission with no pending
    attempts is a no-op."""
    sub = session.get(Submission, submission_id)
    if not sub or sub.deleted_at is not None:
        raise HTTPException(404, "submission not found")
    bg.add_task(dispatch_submission, sub.id)
    return {"submission_id": sub.id, "status": "queued"}


@app.get("/api/admin/pending")
def list_pending(_: User = Depends(current_admin), session: Session = Depends(get_session)):
    rows = session.exec(select(User).where(User.status == UserStatus.pending)).all()
    return [
        {"id": u.id, "email": u.email, "display_name": u.display_name, "org": u.org, "motivation": u.motivation, "created_at": u.created_at}
        for u in rows
    ]


@app.post("/api/admin/users/{user_id}/approve")
def approve_user(user_id: int, _: User = Depends(current_admin), session: Session = Depends(get_session)):
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "not found")
    if u.status == UserStatus.approved:
        # Key is hashed at rest and not recoverable; admin can regenerate.
        return {"api_key": None}
    u.status = UserStatus.approved
    u.approved_at = datetime.utcnow()
    new_key = generate_api_key()
    u.api_key = hash_api_key(new_key)
    session.add(u)
    session.commit()
    return {"api_key": new_key}


@app.post("/api/admin/users/{user_id}/reject")
def reject_user(user_id: int, _: User = Depends(current_admin), session: Session = Depends(get_session)):
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "not found")
    u.status = UserStatus.rejected
    session.add(u)
    session.commit()
    return {"status": "rejected"}


# ---------- SPA serving (single-container deploy) ---------------------------
#
# When ARENA_SERVE_FRONTEND_DIR points at a built Vite dist/, the same
# FastAPI process serves the SPA at root so a `docker run` ships the whole
# product in one container. Skipped in dev (frontend served by Vite).
#
# The route is registered LAST so every /api/* path above wins before the
# catch-all sees it. Inside the catch-all we keep a small allowlist of
# concrete file paths (logo.png, favicon, etc.) and otherwise return the
# SPA shell so React Router takes over.

_FRONTEND_DIR = os.environ.get("ARENA_SERVE_FRONTEND_DIR")
if _FRONTEND_DIR:
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _spa_root = Path(_FRONTEND_DIR)
    _spa_index = _spa_root / "index.html"
    _spa_assets = _spa_root / "assets"

    if not _spa_index.is_file():
        # Fail loud at startup rather than silently 404 every request.
        raise RuntimeError(
            f"ARENA_SERVE_FRONTEND_DIR={_FRONTEND_DIR} but no index.html "
            "there. Build the frontend (`npm run build` in webapp/frontend) "
            "and point this env var at the resulting dist/ directory."
        )

    # Vite's chunked JS/CSS goes under /assets/* — mounted as a real static
    # directory so the browser cache keys cleanly off filename hashes.
    if _spa_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_spa_assets)), name="assets")

    # Everything else falls through here. We DON'T catch /api/*, /docs,
    # /redoc, /openapi.json — those are real FastAPI routes the matcher
    # has already handled by the time we get here. (Belt-and-braces: still
    # raise 404 for anything that looks like a stray API call so a typo
    # doesn't accidentally show the SPA index.)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_catch_all(full_path: str):
        if (full_path.startswith("api/") or full_path == "openapi.json"
                or full_path.startswith("docs") or full_path.startswith("redoc")):
            raise HTTPException(404)
        # Serve concrete public files (logo.png, favicon.svg, robots.txt, …)
        # before the SPA fallback so the browser tab gets the real icon.
        candidate = _spa_root / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # All client-side routes (/rankings, /competitions/foo, etc.) → SPA.
        return FileResponse(_spa_index)
