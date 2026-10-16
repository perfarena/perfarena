"""Small endpoints that keep API clients (the perfarena SDK in clients/python,
its MCP server) free of heuristics: one attempt's machine-readable state,
find-or-create a submission by its experiment cell, and the per-model prices.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from .auth import current_admin
from .db import get_session
from .dispatch import _TRANSIENT_STATUSES
from .models import (
    Agent, Attempt, Competition, CorrectnessOracle, Harness, Problem,
    RegisteredModel, Run, Submission, User,
)
from .schemas import (
    AttemptRunDetail, AttemptState, ModelPrice, SubmissionIn,
    SubmissionUpsertIn, SubmissionUpsertOut,
)

router = APIRouter()

_QUEUE_OPEN = ("queued", "claimed")


def attempt_state(a: Attempt, runs: list[Run]) -> tuple[str, str | None]:
    """(state, verdict) of one attempt. See schemas.AttemptState."""
    if a.final_status is not None:
        # A system status with no run is not a verdict: the legacy remote
        # path finalizes a runner outage as "Dispatch error" with no Run row.
        if not runs and a.final_status in _TRANSIENT_STATUSES:
            return "failed", None
        return "done", a.final_status
    if a.prefailed_reason:
        return "done", a.prefailed_reason
    if a.dispatch_state == "failed" or (
            a.dispatch_warning and a.dispatch_state not in _QUEUE_OPEN):
        return "failed", None
    if a.dispatch_state == "claimed" or runs:
        return "running", None
    return "queued", None


@router.get("/api/attempts/{attempt_id}", response_model=AttemptState)
def get_attempt_state(attempt_id: int, session: Session = Depends(get_session)):
    """One attempt's dispatch state and results. Public, like
    /api/submissions/{id}/attempts, but for a single attempt, so a client can
    poll it cheaply."""
    from .main import _build_error_log
    a = session.get(Attempt, attempt_id)
    sub = session.get(Submission, a.submission_id) if a else None
    if a is None or sub is None or sub.deleted_at is not None:
        raise HTTPException(404, "attempt not found")
    runs = session.exec(select(Run).where(Run.attempt_id == a.id)
                        .order_by(Run.run_index)).all()
    state, verdict = attempt_state(a, runs)
    p = session.get(Problem, a.problem_id)
    return AttemptState(
        attempt_id=a.id, submission_id=a.submission_id,
        problem_slug=p.title_slug if p else "?",
        round_index=a.round_index or 0, previous_attempt_id=a.previous_attempt_id,
        state=state, terminal=state in ("done", "failed"), verdict=verdict,
        accepted=a.final_status == "Accepted",
        prefailed_reason=a.prefailed_reason, dispatch_warning=a.dispatch_warning,
        dispatch_state=a.dispatch_state,
        n_runs=a.n_runs or 0, n_accepted_runs=a.n_accepted_runs or 0,
        avg_runtime_ms=a.avg_runtime_ms, best_runtime_ms=a.best_runtime_ms,
        avg_memory_mb=a.avg_memory_mb, best_memory_mb=a.best_memory_mb,
        avg_runtime_percentile=a.avg_runtime_percentile,
        avg_memory_percentile=a.avg_memory_percentile,
        avg_runtime_energy_j=a.avg_runtime_energy_j,
        best_runtime_energy_j=a.best_runtime_energy_j,
        runs=[AttemptRunDetail(
            run_index=r.run_index, status_msg=r.status_msg,
            runtime_ms=r.runtime_ms, memory_mb=r.memory_mb,
            runtime_percentile=r.runtime_percentile,
            memory_percentile=r.memory_percentile,
            runtime_energy_j=r.runtime_energy_j,
            energy_j_per_op=r.energy_j_per_op, time_us_per_op=r.time_us_per_op,
            total_correct=r.total_correct, total_testcases=r.total_testcases,
            error_log=_build_error_log(r.raw_response, r.status_msg),
        ) for r in runs],
        code=a.code,
    )


def _by_slug(session: Session, model, slug: str, what: str):
    row = session.exec(select(model).where(model.slug == slug)).first()
    if row is None:
        raise HTTPException(400, f"{what}={slug!r} does not exist")
    return row


@router.put("/api/submissions/upsert", response_model=SubmissionUpsertOut)
def upsert_submission(
    payload: SubmissionUpsertIn,
    bg: BackgroundTasks,
    user: User = Depends(current_admin),
    session: Session = Depends(get_session),
):
    """Admin-only, like POST /api/submissions: return the caller's submission
    for this experiment cell, or create an empty one (attempts are appended
    with POST /api/submissions/{id}/attempts). See SubmissionUpsertIn for the
    match rule; create=false only looks."""
    from .main import _direct_agent_id, submit_batch
    comp = _by_slug(session, Competition,
                    (payload.competition_slug or "leetcode-99-perf").strip(),
                    "competition_slug")
    harness = _by_slug(session, Harness, payload.harness_slug, "harness_slug")
    oracle = (_by_slug(session, CorrectnessOracle, payload.correctness_oracle_slug,
                       "correctness_oracle_slug")
              if payload.correctness_oracle_slug else None)
    direct = _direct_agent_id(session)
    agent_id = (_by_slug(session, Agent, payload.agent_slug, "agent_slug").id
                if payload.agent_slug else direct)

    candidates = session.exec(
        select(Submission).where(
            Submission.user_id == user.id, Submission.deleted_at.is_(None),
            Submission.model_name == payload.model_name,
            Submission.model_version == payload.model_version,
            Submission.language == payload.language,
        ).order_by(Submission.created_at.desc(), Submission.id.desc())
    ).all()
    for s in candidates:
        if (s.prompt_id or None) != (payload.prompt_id or None):
            continue
        if bool(s.include_hints) != bool(payload.include_hints):
            continue
        if (s.optimization_target or None) != (payload.optimization_target or None):
            continue
        if int(s.n_rounds or 1) != int(payload.n_rounds):
            continue
        if s.competition_id not in (None, comp.id) or s.harness_id not in (None, harness.id):
            continue
        if oracle is not None and s.correctness_oracle_id not in (None, oracle.id):
            continue
        if (s.agent_id if s.agent_id is not None else direct) != agent_id:
            continue
        if (not payload.include_fully_accepted and s.total_problems > 0
                and s.total_solved >= s.total_problems):
            continue
        return SubmissionUpsertOut(submission_id=s.id, created=False, status=s.status)

    if not payload.create:
        return SubmissionUpsertOut(submission_id=None, created=False, status=None)
    body = SubmissionIn(**payload.model_dump(exclude={"include_fully_accepted", "create"}),
                        attempts=[])
    out = submit_batch(body, bg, user, session)
    return SubmissionUpsertOut(submission_id=out["submission_id"], created=True,
                               status=str(getattr(out["status"], "value", out["status"])))


@router.get("/api/pricing", response_model=list[ModelPrice])
def list_pricing(session: Session = Depends(get_session)):
    """Per-1M-token USD rates of every registered model that has both rates
    set (the rates the SPA uses for live cost). Public: prices are not
    secret."""
    rows = session.exec(select(RegisteredModel).where(
        RegisteredModel.input_price_per_1m_usd.is_not(None),
        RegisteredModel.output_price_per_1m_usd.is_not(None),
    ).order_by(RegisteredModel.name, RegisteredModel.version)).all()
    return [ModelPrice(model_name=r.name, model_version=r.version,
                       input_price_per_1m_usd=r.input_price_per_1m_usd,
                       output_price_per_1m_usd=r.output_price_per_1m_usd)
            for r in rows]
