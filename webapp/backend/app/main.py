"""FastAPI app — LLM LeetCode Arena."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

import json
import os

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
    _refresh_baselines_global, _recompute_human_coefficients,
)
from .ratelimit import enforce_submission_limit
from .models import (
    Attempt, Baseline, BaselineRefreshState, Problem,
    PromptTemplate, RegisteredModel,
    Run, Snapshot,
    Submission, SubmissionStatus, TraceLogEntry,
    User, UserRole, UserStatus,
)
from . import provenance
from . import snapshot as snapshot_mod
from .validation import validate_code
from .schemas import (
    AppendAttemptsIn, AttemptIn, AttemptResult, AttemptRunDetail,
    BaselineBulkIn,
    CompareModelOnProblem, CompareProblemRow, CompareResponse, CompareSummaryRow,
    CompareWinners,
    ModelDetail, ModelDetailLanguageStat, ModelListEntry,
    PromptTemplateIn, PromptTemplatePublic,
    RegisteredModelIn, RegisteredModelPublic, RunErrorLog,
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


@app.on_event("startup")
def _startup() -> None:
    init_db()
    _seed_default_prompts()


def _seed_default_prompts() -> None:
    """Idempotent — only inserts when the prompt table is empty so the team
    can edit / replace the defaults without a startup overwrite."""
    from sqlmodel import Session
    with Session(engine) as session:
        if session.exec(select(PromptTemplate)).first() is not None:
            return
        session.add(PromptTemplate(
            name="standard-code-only",
            system_prompt=(
                "You are a competitive-programming assistant. Solve the "
                "problem in {{language_display}} using LeetCode's standard "
                "`Solution` shape (the exact class and method names LeetCode "
                "expects). Think step by step, then return the final solution "
                "in a single ```{{language_fence}} code block. Don't include "
                "any other comments or text outside the code block."
            ),
            user_prompt_template=(
                "Solve this LeetCode problem `{{slug}}` in "
                "{{language_display}}.\n\n{{statement}}"
            ),
            language_hint=None,
            notes=(
                "The original prompt used by submit_with_local_model.py. "
                "Targeted at single-shot pass@1 — no chain-of-thought leaked "
                "into the final answer."
            ),
        ))
        session.add(PromptTemplate(
            name="explain-then-code",
            system_prompt=(
                "You are a thoughtful programming tutor. First explain the "
                "approach in 2-3 short sentences, then write the solution in "
                "{{language_display}} as a single ```{{language_fence}} code "
                "block following LeetCode's `Solution` shape. Keep the "
                "explanation outside the code block; nothing else after the "
                "code block."
            ),
            user_prompt_template=(
                "Problem `{{slug}}`:\n\n{{statement}}\n\n"
                "Write a clean {{language_display}} solution."
            ),
            language_hint=None,
            notes=(
                "Encourages a short natural-language rationale before the "
                "code. Useful for measuring how reasoning affects pass@1."
            ),
        ))
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

@app.get("/api/registered-models", response_model=list[RegisteredModelPublic])
def list_registered_models(
    include_retired: bool = False,
    session: Session = Depends(get_session),
):
    q = select(RegisteredModel).order_by(RegisteredModel.added_at.desc())
    if not include_retired:
        q = q.where(RegisteredModel.status == "active")
    return session.exec(q).all()


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
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Owner-or-admin: SOFT-delete the submission. The row and all its
    attempts/runs/trace-log entries are kept so the dataset export, the
    transparency hash chain, and audit trails stay intact — only the public
    read surfaces hide the row from now on."""
    sub = session.get(Submission, submission_id)
    if not sub or sub.deleted_at is not None:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id and user.role != UserRole.admin:
        raise HTTPException(403, "not your submission")
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
    """Per-submission breakdown of which Baseline rows fed the human-coefficient
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
    baselines are synthetic vs pulled from LeetCode."""
    state = session.get(BaselineRefreshState, 1)
    counts: dict[str, int] = {}
    for b in session.exec(select(Baseline)).all():
        counts[b.source] = counts.get(b.source, 0) + 1
    return {
        "last_refresh_at": state.last_refresh_at if state else None,
        "submissions_since_last": state.submissions_since_last if state else 0,
        "trigger_every_n": int(os.environ.get("ARENA_BASELINE_REFRESH_EVERY_N", "5")),
        "trigger_hours": float(os.environ.get("ARENA_BASELINE_REFRESH_HOURS", "12")),
        "stale_days": float(os.environ.get("ARENA_BASELINE_STALE_DAYS", "14")),
        "auto_refresh_enabled": os.environ.get("ARENA_AUTO_REFRESH_BASELINES", "1") != "0",
        "baselines_by_source": counts,
    }


@app.post("/api/admin/baselines/refresh")
def baseline_refresh_now(
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Force an immediate global baseline refresh + coefficient recompute.
    Same code path the auto-trigger runs; bypasses the N/12h thresholds."""
    n, langs = _refresh_baselines_global(session)
    for lang in langs:
        _recompute_human_coefficients(session, lang)
    state = session.get(BaselineRefreshState, 1) or BaselineRefreshState(id=1)
    state.last_refresh_at = datetime.utcnow()
    state.submissions_since_last = 0
    session.add(state); session.commit()
    return {"upserted": n, "languages_touched": sorted(langs)}


@app.post("/api/admin/baselines", status_code=status.HTTP_200_OK)
def upsert_baselines(
    payload: BaselineBulkIn,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Bulk upsert human-baseline rows produced by `pull_baselines.py`.

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
        session.add(existing)
        upserted += 1
    session.commit()
    return {"upserted": upserted, "skipped": skipped, "total": len(payload.baselines)}


@app.post("/api/admin/snapshots", status_code=status.HTTP_201_CREATED)
def take_snapshot(
    label: str | None = None,
    _: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    snap = snapshot_mod.capture(session, label=label)
    return {"id": snap.id, "label": snap.label, "taken_at": snap.taken_at}


@app.post("/api/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, request: Request, session: Session = Depends(get_session)):
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

    attempts = session.exec(select(Attempt).where(Attempt.problem_id == p.id)).all()
    runs_by_attempt: dict[int, Run] = {}
    testcases: int | None = None
    for a in attempts:
        runs = session.exec(
            select(Run).where(Run.attempt_id == a.id).order_by(Run.created_at.desc())
        ).all()
        if not runs:
            continue
        latest = runs[0]
        runs_by_attempt[a.id] = latest
        if latest.total_testcases is not None:
            testcases = max(testcases or 0, latest.total_testcases)

    n_attempts = len(runs_by_attempt)
    n_accepted = sum(1 for r in runs_by_attempt.values() if r.status_msg == "Accepted")

    rows: list[tuple[Attempt, Run, Submission]] = []
    for a in attempts:
        r = runs_by_attempt.get(a.id)
        if r is None:
            continue
        sub = session.get(Submission, a.submission_id)
        if sub is None or sub.status != SubmissionStatus.judged or sub.deleted_at is not None:
            continue
        rows.append((a, r, sub))

    def sort_key(row: tuple[Attempt, Run, Submission]):
        _, r, _s = row
        accepted_first = 0 if r.status_msg == "Accepted" else 1
        return (accepted_first, -(r.runtime_percentile or 0), r.runtime_ms or 1e9)

    rows.sort(key=sort_key)

    def _row_to_top(a: Attempt, r: Run, sub: Submission) -> ProblemTopSolution:
        u = session.get(User, sub.user_id)
        label = _submitter_label(u)
        return ProblemTopSolution(
            submission_id=sub.id,
            model_name=sub.model_name, model_version=sub.model_version,
            language=sub.language,
            submitter=label,
            submitter_slug=_slug(label),
            status_msg=r.status_msg,
            runtime_ms=r.runtime_ms, memory_mb=r.memory_mb,
            runtime_percentile=r.runtime_percentile,
            memory_percentile=r.memory_percentile,
        )

    top: list[ProblemTopSolution] = [_row_to_top(*row) for row in rows[:10]]

    # Per-language: best single solution + attempt/accepted counts.
    n_attempts_by_lang: dict[str, int] = {}
    n_accepted_by_lang: dict[str, int] = {}
    best_in_lang: dict[str, tuple[Attempt, Run, Submission]] = {}
    for row in rows:
        _a, r, sub = row
        n_attempts_by_lang[sub.language] = n_attempts_by_lang.get(sub.language, 0) + 1
        if r.status_msg == "Accepted":
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
    """Enforce the pass@k sample budget: per problem, the total attempts
    (existing + incoming) must never exceed `sub.n_samples_per_problem`."""
    k = sub.n_samples_per_problem or 1
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
        if counts[pid] > k:
            overflows.append(
                f"{a.title_slug}: would be sample #{counts[pid]} but submission's "
                f"budget is k={k}"
            )
    if overflows:
        raise HTTPException(400, {
            "detail": "sample budget exceeded",
            "errors": overflows,
        })


def _reject_invalid_attempts(attempts: list[AttemptIn], language: str) -> None:
    """Fast sanity gate: reject empty/stub/placeholder code before anything is
    persisted. This is NOT a correctness check — only the judge decides whether
    a solution actually solves its problem."""
    errors = [
        f"{a.title_slug}: {reason}"
        for a in attempts
        if (reason := validate_code(language, a.code)) is not None
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
    transparency log (server-timestamped, hash-chained)."""
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


@app.post("/api/submissions", status_code=status.HTTP_202_ACCEPTED)
def submit_batch(
    payload: SubmissionIn,
    bg: BackgroundTasks,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
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
        total_problems=len(all_problems),
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)

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
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    sub = session.get(Submission, submission_id)
    if not sub or sub.deleted_at is not None:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id and user.role != UserRole.admin:
        raise HTTPException(403, "not your submission")

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
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Stream every solution + metrics as JSONL (gated). Soft-deleted
    submissions are kept in the database (for audit / hash-chain integrity)
    but excluded from this research export."""
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
