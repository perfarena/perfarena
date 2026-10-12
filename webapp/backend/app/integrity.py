"""Measurement-integrity checks.

A model can pass a problem's correctness oracle while doing far less work than
the benchmark intends — e.g. computing a CLBG checksum in closed form instead
of actually allocating the trees. The output is byte-correct so the oracle
accepts it, but the runtime/energy it records are meaningless.

We cannot catch this from the OUTPUT (it is a valid answer), but we can catch it
from the MEASUREMENT: a solution that runs dramatically faster than every other
accepted solution for the same work is almost always skipping the workload.

`speed_outliers` flags accepted attempts whose best runtime is at least
`factor`x faster than the MEDIAN of the other accepted solutions for the same
(problem, language, HARNESS). Grouping by harness is essential: the same problem
is measured on different bases (LeetCode's wall-clock oracle vs the arena's
per-op loop), and comparing across them is apples-to-oranges. A cell needs at
least `min_peers` other solutions for a meaningful median. The optional floors
`min_median_ms` and `min_runtime_ms` are off by default: the arena's per-op
times are precise at any scale, and a zero time (the LeetCode judge rounds to
whole milliseconds) is always skipped. This is a review signal, not an
auto-reject — a genuine optimization can also be an outlier.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from sqlmodel import Session, select

from .models import Attempt, Problem, Submission

DEFAULT_FACTOR = 2.0        # >= this many times faster than the peer median -> flagged
DEFAULT_MIN_PEERS = 3       # need at least this many OTHER solutions for a baseline
DEFAULT_MIN_MEDIAN_MS = 0.0   # optional floor on the peer median (off)
DEFAULT_MIN_RUNTIME_MS = 0.0  # optional floor on the attempt's own time (off)


def _accepted_rows(session: Session):
    """Accepted, measured attempts as tuples:
    (problem_id, language, harness_id, attempt_id, submission_id, model_name,
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
            rows.append((p.id, s.language, s.harness_id, a.id, s.id,
                         s.model_name, s.model_version, p.title_slug, a.best_runtime_ms))
    return rows


def speed_baselines(session: Session) -> dict[tuple, list[float]]:
    """(problem_id, language, harness_id) -> list of best_runtime_ms over all
    accepted attempts. Reused by the per-attempt flag in the attempts endpoint."""
    groups: dict[tuple, list[float]] = defaultdict(list)
    for pid, lang, hid, _aid, _sid, _m, _v, _slug, rt in _accepted_rows(session):
        groups[(pid, lang, hid)].append(rt)
    return groups


def is_outlier(runtime_ms, peer_runtimes, factor=DEFAULT_FACTOR,
               min_peers=DEFAULT_MIN_PEERS, min_median_ms=DEFAULT_MIN_MEDIAN_MS,
               min_runtime_ms=DEFAULT_MIN_RUNTIME_MS):
    """`peer_runtimes` is the full same-(problem,lang,harness) group INCLUDING
    this attempt's runtime; the comparison excludes one occurrence of it.
    Returns (flagged, ratio, peer_median, n_peers) where
    ratio = peer_median / runtime_ms (how many times faster than typical).
    Not flagged when the attempt is below the measurement floor, the baseline is
    too small, or there are too few peers."""
    if not runtime_ms or runtime_ms < min_runtime_ms:
        return (False, None, None, 0)
    others = list(peer_runtimes)
    try:
        others.remove(runtime_ms)   # drop the attempt itself (one occurrence)
    except ValueError:
        pass
    if len(others) < min_peers:
        return (False, None, None, len(others))
    med = median(others)
    if med < min_median_ms:
        return (False, None, med, len(others))
    ratio = med / runtime_ms
    return (ratio >= factor, ratio, med, len(others))


def speed_outliers(session: Session, *, factor=DEFAULT_FACTOR,
                   min_peers=DEFAULT_MIN_PEERS, min_median_ms=DEFAULT_MIN_MEDIAN_MS,
                   min_runtime_ms=DEFAULT_MIN_RUNTIME_MS) -> list[dict]:
    """Accepted attempts that are >= `factor`x faster than the median of their
    (problem, language, harness) peers, largest ratio first."""
    rows = _accepted_rows(session)
    groups: dict[tuple, list[float]] = defaultdict(list)
    for pid, lang, hid, _aid, _sid, _m, _v, _slug, rt in rows:
        groups[(pid, lang, hid)].append(rt)
    flagged = []
    for pid, lang, hid, aid, sid, model, ver, slug, rt in rows:
        ok, ratio, med, npeers = is_outlier(
            rt, groups[(pid, lang, hid)], factor, min_peers, min_median_ms, min_runtime_ms)
        if ok:
            flagged.append({
                "attempt_id": aid,
                "submission_id": sid,
                "problem_slug": slug,
                "language": lang,
                "harness_id": hid,
                "model_name": model,
                "model_version": ver,
                "best_runtime_ms": round(rt, 4),
                "peer_median_ms": round(med, 4),
                "ratio": round(ratio, 1),
                "n_peers": npeers,
            })
    flagged.sort(key=lambda r: r["ratio"], reverse=True)
    return flagged
