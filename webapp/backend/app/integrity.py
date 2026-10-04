"""Measurement-integrity checks.

A model can pass a problem's correctness oracle while doing far less work than
the benchmark intends — e.g. computing a CLBG checksum in closed form instead
of actually allocating the trees. The output is byte-correct so the oracle
accepts it, but the runtime/energy it records are meaningless.

We cannot catch this from the OUTPUT (it is a valid answer), but we can catch it
from the MEASUREMENT: a solution that runs dramatically faster than every other
accepted solution for the same (problem, language) is almost always skipping the
workload. `speed_outliers` flags accepted attempts whose best runtime is at
least `factor`x faster than the MEDIAN of the OTHER accepted solutions for the
same (problem, language), given at least `min_peers` peers to form a baseline.

This is a review signal, not an auto-reject: a genuinely clever optimization can
also be an outlier. It surfaces attempts worth a human look.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from sqlmodel import Session, select

from .models import Attempt, Problem, Submission

DEFAULT_FACTOR = 8.0     # >= this many times faster than the peer median -> flagged
DEFAULT_MIN_PEERS = 3    # need at least this many OTHER solutions for a baseline


def _accepted_rows(session: Session):
    """All accepted, measured attempts as tuples:
    (problem_id, language, attempt_id, submission_id, model_name,
     model_version, problem_slug, best_runtime_ms)."""
    q = (
        select(Attempt, Submission, Problem)
        .join(Submission, Attempt.submission_id == Submission.id)
        .join(Problem, Attempt.problem_id == Problem.id)
        .where(Attempt.final_status == "Accepted")
        .where(Attempt.best_runtime_ms.is_not(None))
        .where(Submission.deleted_at.is_(None))
    )
    rows = []
    for a, s, p in session.exec(q).all():
        if a.best_runtime_ms and a.best_runtime_ms > 0:
            rows.append((p.id, s.language, a.id, s.id, s.model_name,
                         s.model_version, p.title_slug, a.best_runtime_ms))
    return rows


def speed_baselines(session: Session) -> dict[tuple[int, str], list[float]]:
    """(problem_id, language) -> list of best_runtime_ms over all accepted
    attempts. Reused by the per-attempt flag in the attempts endpoint."""
    groups: dict[tuple[int, str], list[float]] = defaultdict(list)
    for pid, lang, _aid, _sid, _m, _v, _slug, rt in _accepted_rows(session):
        groups[(pid, lang)].append(rt)
    return groups


def is_outlier(runtime_ms, peer_runtimes, factor=DEFAULT_FACTOR,
               min_peers=DEFAULT_MIN_PEERS):
    """`peer_runtimes` is the full group INCLUDING this attempt's runtime; the
    comparison excludes one occurrence of it. Returns
    (flagged: bool, ratio: float|None, peer_median: float|None, n_peers: int)
    where ratio = peer_median / runtime_ms (how many times faster than typical)."""
    if not runtime_ms or runtime_ms <= 0:
        return (False, None, None, 0)
    others = list(peer_runtimes)
    try:
        others.remove(runtime_ms)   # drop the attempt itself (one occurrence)
    except ValueError:
        pass
    if len(others) < min_peers:
        return (False, None, None, len(others))
    med = median(others)
    ratio = med / runtime_ms
    return (ratio >= factor, ratio, med, len(others))


def speed_outliers(session: Session, *, factor=DEFAULT_FACTOR,
                   min_peers=DEFAULT_MIN_PEERS) -> list[dict]:
    """Every accepted attempt that is >= `factor`x faster than the median of its
    (problem, language) peers, newest-anomaly (largest ratio) first."""
    rows = _accepted_rows(session)
    groups: dict[tuple[int, str], list[float]] = defaultdict(list)
    for pid, lang, _aid, _sid, _m, _v, _slug, rt in rows:
        groups[(pid, lang)].append(rt)
    flagged = []
    for pid, lang, aid, sid, model, ver, slug, rt in rows:
        ok, ratio, med, npeers = is_outlier(rt, groups[(pid, lang)], factor, min_peers)
        if ok:
            flagged.append({
                "attempt_id": aid,
                "submission_id": sid,
                "problem_slug": slug,
                "language": lang,
                "model_name": model,
                "model_version": ver,
                "best_runtime_ms": round(rt, 4),
                "peer_median_ms": round(med, 4),
                "ratio": round(ratio, 1),
                "n_peers": npeers,
            })
    flagged.sort(key=lambda r: r["ratio"], reverse=True)
    return flagged
