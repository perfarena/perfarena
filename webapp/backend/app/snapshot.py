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

from .models import Snapshot, Submission, SubmissionStatus, User


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
