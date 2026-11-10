"""Weekly snapshot capture.

Freezes the current state of judged submissions into a Snapshot row.
Each snapshot is a self-contained JSON document so historical reports stay
stable even as data evolves.

Usage:
  from app.snapshot import capture
  capture(session, label="2026-W19")

Or via POST /api/admin/snapshots.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime

from sqlmodel import Session, select

from .models import Competition, PromptTemplate, Snapshot, Submission, SubmissionStatus, User


def _label_for(d: date | None = None) -> str:
    d = d or date.today()
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _submitter_label(user: User | None) -> str:
    if not user:
        return "anon"
    return user.org or user.display_name


def build_report(session: Session) -> dict:
    subs = session.exec(
        select(Submission)
        .where(Submission.status == SubmissionStatus.judged)
        .where(Submission.deleted_at.is_(None))
    ).all()

    users = {u.id: u for u in session.exec(select(User)).all()}

    n_subs = len(subs)
    submitters = {_submitter_label(users.get(s.user_id)) for s in subs}
    models = {s.model_name for s in subs}
    languages = Counter(s.language for s in subs)

    top_models = sorted(
        subs, key=lambda s: (-(s.pass_at_1 or 0), -(s.avg_runtime_percentile or 0))
    )[:10]
    # Prompt-template name lookup so the snapshot records which registered
    # prompt produced each top-row. New snapshots get the fields; existing
    # ones (pre-this-change) just don't have them — the frontend's
    # PromptName component falls back to "ad-hoc" for that case.
    prompt_name_by_id = {pt.id: pt.name for pt in session.exec(select(PromptTemplate)).all()}

    top_models_out = [
        {
            "rank": i + 1,
            "model_name": s.model_name,
            "model_version": s.model_version,
            "language": s.language,
            "submitter": _submitter_label(users.get(s.user_id)),
            "pass_at_1": s.pass_at_1,
            "avg_runtime_percentile": s.avg_runtime_percentile,
            "avg_memory_percentile": s.avg_memory_percentile,
            "total_solved": s.total_solved,
            "total_problems": s.total_problems,
            "submission_id": s.id,
            "prompt_id": s.prompt_id,
            "prompt_name": prompt_name_by_id.get(s.prompt_id) if s.prompt_id else None,
        }
        for i, s in enumerate(top_models)
    ]

    by_lang: dict[str, dict] = defaultdict(lambda: {"n_submissions": 0, "best_pass_at_1": None})
    for s in subs:
        bucket = by_lang[s.language]
        bucket["n_submissions"] += 1
        if s.pass_at_1 is not None and (bucket["best_pass_at_1"] is None or s.pass_at_1 > bucket["best_pass_at_1"]):
            bucket["best_pass_at_1"] = s.pass_at_1

    submitter_counts: Counter[str] = Counter(_submitter_label(users.get(s.user_id)) for s in subs)
    top_submitters = [
        {"submitter": name, "n_submissions": n}
        for name, n in submitter_counts.most_common(10)
    ]

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "efficiency": efficiency_by_competition(session),
        "totals": {
            "n_submissions": n_subs,
            "n_distinct_submitters": len(submitters),
            "n_distinct_models": len(models),
            "n_languages": len(languages),
        },
        "top_models": top_models_out,
        "top_submitters": top_submitters,
        "language_breakdown": [
            {"language": lang, **dict(stats)} for lang, stats in sorted(by_lang.items())
        ],
    }


def efficiency_by_competition(session: Session) -> list[dict]:
    """The efficiency ranking of every active competition as it stands now (the
    default view: all workflows, the competition's main harness), so a dated
    snapshot keeps the ranking even after new results change it."""
    from .ranking_api import efficiency_rankings     # avoid an import cycle
    out = []
    for comp in session.exec(select(Competition).where(Competition.status == "active")).all():
        r = efficiency_rankings(session, comp.slug, fresh=True)
        if not r["entrants"]:
            continue
        out.append({
            "competition": comp.slug, "competition_name": comp.name, "harness": r["harness"],
            "computed_at": r["computed_at"], "n_problems": r["n_problems"],
            "languages": r["languages"],
            "entrants": [
                {"key": e["key"], "model_name": e["model_name"],
                 "model_version": e["model_version"], "workflow": e["workflow"],
                 "pass_at_1": e["pass_at_1"], "cost_per_attempt_usd": e["cost_per_attempt_usd"],
                 "pareto_level": e["pareto_level"], "reference": e["reference"],
                 "metrics": {m: {k: v[k] for k in ("win_prob", "tied_leader", "rank", "rated")
                                 if k in v}
                             for m, v in e["metrics"].items()}}
                for e in r["entrants"]],
        })
    return out


def capture(session: Session, label: str | None = None) -> Snapshot:
    label = label or _label_for()
    existing = session.exec(select(Snapshot).where(Snapshot.label == label)).first()
    if existing:
        existing.payload = build_report(session)
        existing.taken_at = datetime.utcnow()
        session.add(existing); session.commit(); session.refresh(existing)
        return existing
    snap = Snapshot(label=label, payload=build_report(session))
    session.add(snap); session.commit(); session.refresh(snap)
    return snap
