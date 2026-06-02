"""FastAPI app — LLM LeetCode Arena."""
from __future__ import annotations

from datetime import datetime
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
    hash_password, verify_password,
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
    Attempt, Baseline, BaselineRefreshState, BlogPost,
    Competition, CompetitionProblem, Problem,
    PromptTemplate, RegisteredModel,
    Run, Snapshot,
    Submission, SubmissionStatus, TraceLogEntry,
    User, UserRole, UserStatus,
)
from . import provenance
from . import snapshot as snapshot_mod
from .validation import validate_code
from .schemas import (
    AnalysisPoint, AnalysisResponse, LangAnalysisRow,
    DiscriminationCrossRow, DiscriminationLangSummary, DiscriminationResponse,
    AppendAttemptsIn, AttemptIn, AttemptResult, AttemptRunDetail,
    BaselineBulkIn,
    CompareModelOnProblem, CompareProblemRow, CompareResponse, CompareSummaryRow,
    CompareWinners,
    BlogPostCard, BlogPostIn, BlogPostPublic,
    ComparePromptRow, ComparePromptsResponse, ComparePromptsWinners,
    CompetitionDetail, CompetitionIn, CompetitionProblemsIn, CompetitionPublic,
    ModelDetail, ModelDetailLanguageStat, ModelListEntry,
    PromptTemplateIn, PromptTemplatePublic,
    RegisteredModelIn, RegisteredModelOverview, RegisteredModelPublic, RunErrorLog,
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


def _attempt_stats_by_sub(
    session: Session, sub_ids: list[int],
) -> dict[int, dict]:
    """One round-trip → {sub_id: {"problem_ids": set, "n_attempts": int}}."""
    if not sub_ids:
        return {}
    out: dict[int, dict] = {}
    attempts = session.exec(
        select(Attempt).where(Attempt.submission_id.in_(sub_ids))
    ).all()
    for a in attempts:
        entry = out.setdefault(a.submission_id,
                               {"problem_ids": set(), "n_attempts": 0})
        entry["problem_ids"].add(a.problem_id)
        entry["n_attempts"] += 1
    return out


def _attempt_stats_for(stats_map: dict[int, dict], sub_id: int) -> tuple[set[int], int]:
    s = stats_map.get(sub_id) or {"problem_ids": set(), "n_attempts": 0}
    return s["problem_ids"], s["n_attempts"]


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


app = FastAPI(title="PerfArena", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
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


@app.on_event("startup")
def _startup() -> None:
    init_db()
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


def _seed_bootstrap_admin() -> None:
    """First-boot admin bootstrap so a fresh container is reachable as
    admin without docker-exec'ing into it.

    Honoured env vars:
      ARENA_BOOTSTRAP_ADMIN_EMAIL     (required to trigger anything)
      ARENA_BOOTSTRAP_ADMIN_NAME      default "Admin"
      ARENA_BOOTSTRAP_ADMIN_API_KEY   default = freshly generated
      ARENA_BOOTSTRAP_ADMIN_PASSWORD  default = unset (no password login;
                                      use the API key with Bearer auth)

    Idempotent: if a user with the given email already exists we leave it
    alone — we don't downgrade or rotate keys for existing rows. To rotate
    after the fact, do it via the admin endpoints or directly in the DB.
    The fresh API key is printed to stderr so a `docker logs` can recover
    it when it was auto-generated."""
    from sqlmodel import Session                                          # noqa: E402
    email = os.environ.get("ARENA_BOOTSTRAP_ADMIN_EMAIL")
    if not email:
        return
    with Session(engine) as s:
        if s.exec(select(User).where(User.email == email)).first():
            return
        api_key = os.environ.get("ARENA_BOOTSTRAP_ADMIN_API_KEY") or generate_api_key()
        password = os.environ.get("ARENA_BOOTSTRAP_ADMIN_PASSWORD")
        u = User(
            email=email,
            display_name=os.environ.get("ARENA_BOOTSTRAP_ADMIN_NAME", "Admin"),
            role=UserRole.admin,
            status=UserStatus.approved,
            api_key=api_key,
            password_hash=hash_password(password) if password else None,
        )
        s.add(u); s.commit()
        # Surface the credentials so `docker logs <container>` can read them
        # back. Skip the api_key line if the operator supplied one — they
        # already know it and re-printing it to logs is needless exposure.
        print(f"[bootstrap] created admin user {email!r}", file=sys.stderr)
        if not os.environ.get("ARENA_BOOTSTRAP_ADMIN_API_KEY"):
            print(f"[bootstrap]   api_key = {api_key}", file=sys.stderr)
        if password:
            print(f"[bootstrap]   password login enabled (use /login)", file=sys.stderr)


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
    session: Session = Depends(get_session),
):
    q = select(Problem)
    if level:
        q = q.where(Problem.level == level)
    rows = session.exec(q).all()
    if tag:
        rows = [r for r in rows if tag.lower() in r.tags.lower()]
    return [
        ProblemPublic(
            id=r.id, title_slug=r.title_slug, title=r.title,
            level=r.level, tags=[t.strip() for t in r.tags.split(",") if t.strip()],
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

    # Competition scope: only keep submissions whose attempts touch a problem
    # that belongs to the competition's membership set. The seed competition
    # links every problem, so this is a no-op for `leetcode-99-perf`.
    if competition:
        comp = session.exec(
            select(Competition).where(Competition.slug == competition)
        ).first()
        if comp is None:
            raise HTTPException(404, f"competition '{competition}' not found")
        comp_pids = {
            m.problem_id for m in session.exec(
                select(CompetitionProblem)
                .where(CompetitionProblem.competition_id == comp.id)
            ).all()
        }
        if comp_pids:
            keep_sids = {
                a.submission_id for a in session.exec(
                    select(Attempt).where(Attempt.problem_id.in_(comp_pids))   # type: ignore[arg-type]
                ).all()
            }
            subs = [s for s in subs if s.id in keep_sids]
        else:
            subs = []

    # Eligibility gate: only complete (all problems attempted) submissions with
    # > 25% accepted solutions appear on the leaderboard.
    all_problem_ids = {p.id for p in session.exec(select(Problem)).all()}
    stats_by_sub = _attempt_stats_by_sub(session, [s.id for s in subs])
    subs = [
        s for s in subs
        if _eligible_for_leaderboard(
            s, _attempt_stats_for(stats_by_sub, s.id)[0], all_problem_ids,
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
    for i, s in enumerate(subs, start=1):
        u = session.get(User, s.user_id)
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
        ))
    return out


@app.get("/api/submissions", response_model=list[SubmissionPublic])
def list_submissions(session: Session = Depends(get_session)):
    subs = session.exec(
        select(Submission)
        .where(Submission.deleted_at.is_(None))
        .order_by(Submission.created_at.desc())
    ).all()
    return [_submission_public(s, session) for s in subs]


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
        ))
    return out


@app.get("/api/competitions/{slug}", response_model=CompetitionDetail)
def get_competition(slug: str, session: Session = Depends(get_session)):
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

    n_subs, n_models, langs, judged = _competition_stats(session, member_pids)

    # Top models by best pass@1 within the competition's problems. Cap at 5.
    by_model: dict[tuple[str, str], list[Submission]] = {}
    for s in judged:
        by_model.setdefault((s.model_name, s.model_version), []).append(s)
    top: list[dict] = []
    for (name, ver), subs in by_model.items():
        best = max((s.pass_at_1 for s in subs if s.pass_at_1 is not None), default=None)
        if best is None:
            continue
        top.append({"model_name": name, "model_version": ver,
                    "best_pass_at_1": best,
                    "n_submissions": len(subs)})
    top.sort(key=lambda r: r["best_pass_at_1"], reverse=True)

    return CompetitionDetail(
        id=c.id, slug=c.slug, name=c.name,
        description=c.description, kind=c.kind, status=c.status,
        created_at=c.created_at,
        n_problems=len(member_pids),
        n_submissions=n_subs,
        n_models=n_models,
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
    c = Competition(**payload.model_dump(), created_by_user_id=admin.id)
    session.add(c); session.commit(); session.refresh(c)
    return CompetitionPublic(
        id=c.id, slug=c.slug, name=c.name, description=c.description,
        kind=c.kind, status=c.status, created_at=c.created_at,
        n_problems=0, n_submissions=0, n_models=0,
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
    for k, v in payload.model_dump().items():
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
    )


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


@app.get("/api/compare", response_model=CompareResponse)
def compare_models(
    models: list[str] = Query(..., min_length=2,
                              description="repeat ?models=NAME for each model to compare"),
    language: str | None = None,
    prompt_id: int | None = Query(None, description="restrict to one PromptTemplate"),
    problems: str | None = Query(
        None, description="comma-separated problem slugs to restrict to"),
    session: Session = Depends(get_session),
):
    """Head-to-head model comparison. Aggregates each model's judged
    submissions (optionally filtered by language and/or a problem subset)
    into one summary row + a per-problem matrix (where every requested
    problem shows what each model achieved)."""
    wanted_slugs: set[str] | None = None
    if problems:
        wanted_slugs = {s.strip() for s in problems.split(",") if s.strip()}

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
        for pid, atts in by_pid.items():
            accepted = [a for a in atts if a.final_status == "Accepted"]
            if accepted:
                best = max(accepted, key=lambda a: (
                    (a.n_accepted_runs / a.n_runs) if a.n_runs else 0.0,
                    -(a.avg_runtime_ms if a.avg_runtime_ms is not None else 1e18),
                ))
                n_accepted += 1
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
            if best.avg_memory_percentile is not None:  avg_memp.append(best.avg_memory_percentile)

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
            )
        problems_out.append(CompareProblemRow(
            problem_slug=p.title_slug, problem_title=p.title, level=p.level,
            per_model=per_model,
        ))

    return CompareResponse(
        models=models, language=language,
        problem_slugs=sorted(wanted_slugs) if wanted_slugs else None,
        summary=summary, problems=problems_out,
        winners=_compute_winners(summary, single_language=language is not None),
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


def _submission_public(s: Submission, session: Session) -> SubmissionPublic:
    u = session.get(User, s.user_id)
    pt = session.get(PromptTemplate, s.prompt_id) if s.prompt_id is not None else None
    atts = session.exec(
        select(Attempt).where(Attempt.submission_id == s.id)
    ).all()
    attempted = {a.problem_id for a in atts}
    all_pids = {p.id for p in session.exec(select(Problem)).all()}
    reason = _leaderboard_reason(s, attempted, all_pids)
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
        total_cost_usd=s.total_cost_usd,
        total_response_tokens=s.total_response_tokens,
        total_generation_ms=s.total_generation_ms,
        total_generation_energy_j=s.total_generation_energy_j,
    )


# ---------- Register --------------------------------------------------------

@app.get("/api/captcha")
def get_captcha():
    return captcha.issue()


@app.get("/api/languages")
def list_languages(session: Session = Depends(get_session)):
    counts: dict[str, int] = {}
    fully: dict[str, int] = {}
    for s in session.exec(
        select(Submission).where(Submission.deleted_at.is_(None))
    ).all():
        counts[s.language] = counts.get(s.language, 0) + 1
        if s.total_problems > 0 and s.total_solved >= s.total_problems:
            fully[s.language] = fully.get(s.language, 0) + 1
    out = []
    for name, info in langs_mod.LANGUAGES.items():
        out.append({
            "name": name,
            "display_name": info.display_name,
            "category": info.category,
            "n_submissions": counts.get(name, 0),
            "n_fully_accepted": fully.get(name, 0),
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

    # Leaderboard slice on the language page — same eligibility rules as /api/rankings.
    all_problem_ids = {p.id for p in session.exec(select(Problem)).all()}
    stats_by_sub = _attempt_stats_by_sub(session, [s.id for s in judged])
    eligible = [
        s for s in judged
        if _eligible_for_leaderboard(
            s, _attempt_stats_for(stats_by_sub, s.id)[0], all_problem_ids,
        )
    ]
    top = sorted(
        eligible,
        key=lambda s: (-(s.pass_at_1 or 0), -(s.avg_runtime_percentile or 0)),
    )[:5]
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
            "avg_runtime_ms": s.avg_runtime_ms,
            "avg_memory_mb": s.avg_memory_mb,
            "avg_runtime_percentile": s.avg_runtime_percentile,
            "avg_memory_percentile": s.avg_memory_percentile,
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
    if not user.api_key:
        # User previously discarded their key — issue a fresh one on login so
        # they're not locked out of API access just because they cleared it.
        user.api_key = generate_api_key()
        session.add(user); session.commit(); session.refresh(user)
    return LoginResponse(
        email=user.email, display_name=user.display_name, org=user.org,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        api_key=user.api_key,
    )


@app.post("/api/me/api-key/regenerate")
def regenerate_api_key(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Issues a fresh API key and revokes the previous one. The caller MUST
    swap the new key into their client immediately — the current Bearer dies
    on this call."""
    user.api_key = generate_api_key()
    session.add(user); session.commit(); session.refresh(user)
    return {"api_key": user.api_key}


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

    return ProblemDetail(
        id=p.id, title_slug=p.title_slug, title=p.title, level=p.level,
        tags=[t.strip() for t in p.tags.split(",") if t.strip()],
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
    )


def _reject_oversampling(
    session: Session, sub: Submission, slug_to_id: dict[str, int],
    incoming: list[AttemptIn],
) -> None:
    """Enforce the sample budget: per problem, the total attempts
    (existing + incoming) must never exceed `n_samples_per_problem × n_rounds`.

    Single-shot (n_rounds=1) → exactly the legacy pass@k cap. Refinement
    chains (n_rounds=N) get N times the budget per problem so each round
    can submit a fresh code attempt for the same slug. The chain link
    itself (previous_attempt_id) is validated separately in _ingest_attempts."""
    k = sub.n_samples_per_problem or 1
    n_rounds = sub.n_rounds or 1
    cap = k * n_rounds
    existing = session.exec(
        select(Attempt).where(Attempt.submission_id == sub.id)
    ).all()
    counts: dict[int, int] = {}
    for a in existing:
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

    all_problems = session.exec(select(Problem)).all()
    slug_to_id = {p.title_slug: p.id for p in all_problems}
    bad = [a.title_slug for a in payload.attempts if a.title_slug not in slug_to_id]
    if bad:
        raise HTTPException(400, f"unknown problem slugs: {bad[:5]}")
    _reject_invalid_attempts(payload.attempts, payload.language)

    # Validate optional prompt_id reference up-front so a bad id surfaces as
    # a 400 instead of a stored dangling FK.
    if payload.prompt_id is not None:
        if session.get(PromptTemplate, payload.prompt_id) is None:
            raise HTTPException(400, f"prompt_id={payload.prompt_id} does not exist")

    sub = Submission(
        user_id=user.id, model_name=payload.model_name,
        model_version=payload.model_version, language=payload.language,
        prompt_template=payload.prompt_template, model_params=payload.model_params,
        n_samples_per_problem=payload.n_samples_per_problem,
        prompt_id=payload.prompt_id,
        n_rounds=payload.n_rounds,
        include_hints=payload.include_hints,
        optimization_target=payload.optimization_target,
        total_problems=len(all_problems),
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

    all_problems = session.exec(select(Problem)).all()
    slug_to_id = {p.title_slug: p.id for p in all_problems}
    bad = [a.title_slug for a in payload.attempts if a.title_slug not in slug_to_id]
    if bad:
        raise HTTPException(400, f"unknown problem slugs: {bad[:5]}")
    _reject_invalid_attempts(payload.attempts, sub.language)
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
    out: list[AttemptResult] = []
    for a in attempts:
        p = session.get(Problem, a.problem_id)
        runs = session.exec(
            select(Run).where(Run.attempt_id == a.id).order_by(Run.run_index)
        ).all()
        if not runs:
            status = "queued"
        else:
            status = a.final_status or "Pending"
        out.append(AttemptResult(
            attempt_id=a.id,
            problem_slug=p.title_slug if p else "?",
            problem_title=p.title if p else "?",
            level=p.level if p else "?",
            status=status,
            accepted=a.final_status == "Accepted",
            round_index=a.round_index or 0,
            previous_attempt_id=a.previous_attempt_id,
            cost_usd=a.cost_usd,
            response_tokens=a.response_tokens,
            wall_clock_ms=a.wall_clock_ms,
            generation_energy_j=a.generation_energy_j,
            prefailed_reason=a.prefailed_reason,
            n_runs=a.n_runs,
            n_accepted_runs=a.n_accepted_runs,
            avg_runtime_ms=a.avg_runtime_ms,
            best_runtime_ms=a.best_runtime_ms,
            avg_memory_mb=a.avg_memory_mb,
            best_memory_mb=a.best_memory_mb,
            avg_runtime_percentile=a.avg_runtime_percentile,
            avg_memory_percentile=a.avg_memory_percentile,
            submitted_at=a.logged_at,
            runs=[AttemptRunDetail(
                run_index=r.run_index,
                status_msg=r.status_msg,
                runtime_ms=r.runtime_ms,
                memory_mb=r.memory_mb,
                runtime_percentile=r.runtime_percentile,
                memory_percentile=r.memory_percentile,
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
                record = {
                    "submission_id": sub.id,
                    "submitted_at": sub.created_at.isoformat(),
                    "submitter": _submitter_label(user),
                    "submitter_slug": _slug(_submitter_label(user)),
                    "model_name": sub.model_name,
                    "model_version": sub.model_version,
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
_EXPORTABLE_TABLE_NAMES = [name for name, _ in export_import.EXPORTABLE]


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

    if format == "json":
        bundle = export_import.export_json(session, requested)
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        return StreamingResponse(
            iter([json.dumps(bundle, default=str).encode()]),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=perfarena-export-{stamp}.json"},
        )
    # CSV → zip.
    blob = export_import.export_csv_zip(session, requested)
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
        return {"api_key": u.api_key}
    u.status = UserStatus.approved
    u.approved_at = datetime.utcnow()
    u.api_key = generate_api_key()
    session.add(u)
    session.commit()
    return {"api_key": u.api_key}


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
