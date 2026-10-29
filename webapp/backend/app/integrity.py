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
auto-reject — a genuine optimization can also be an outlier. Reference
submissions (the golden baseline) copy the best solutions, so they are neither
flagged nor counted as peers.
"""
from __future__ import annotations

import re
from collections import defaultdict
from statistics import median

from sqlmodel import Session, select

from .models import Agent, Attempt, Problem, SolutionReview, Submission

DEFAULT_FACTOR = 2.0        # >= this many times faster than the peer median -> flagged
DEFAULT_MIN_PEERS = 3       # need at least this many OTHER solutions for a baseline
DEFAULT_MIN_MEDIAN_MS = 0.0   # optional floor on the peer median (off)
DEFAULT_MIN_RUNTIME_MS = 0.0  # optional floor on the attempt's own time (off)


def _accepted_rows(session: Session, *, problem_id: int | None = None,
                   language: str | None = None, harness_id: int | None = None):
    """Accepted, measured attempts of non-reference submissions as tuples:
    (problem_id, language, harness_id, attempt_id, submission_id, model_name,
     model_version, problem_slug, best_runtime_ms, competition_id). Given all
    three of problem_id, language, and harness_id, only that peer group."""
    from .ranking_api import REFERENCE_KIND       # avoid an import cycle
    reference = {a.id for a in session.exec(
        select(Agent).where(Agent.kind == REFERENCE_KIND)).all()}
    q = (
        select(Attempt, Submission, Problem)
        .join(Submission, Attempt.submission_id == Submission.id)
        .join(Problem, Attempt.problem_id == Problem.id)
        .where(Attempt.final_status == "Accepted")
        .where(Attempt.best_runtime_ms.is_not(None))
        .where(Submission.deleted_at.is_(None))
    )
    if problem_id is not None:
        q = q.where(Attempt.problem_id == problem_id, Submission.language == language,
                    Submission.harness_id == harness_id)
    rows = []
    for a, s, p in session.exec(q).all():
        if a.best_runtime_ms and a.best_runtime_ms > 0 and s.agent_id not in reference:
            rows.append((p.id, s.language, s.harness_id, a.id, s.id,
                         s.model_name, s.model_version, p.title_slug, a.best_runtime_ms,
                         s.competition_id))
    return rows


def speed_baselines(session: Session) -> dict[tuple, list[float]]:
    """(problem_id, language, harness_id) -> list of best_runtime_ms over all
    accepted attempts. Reused by the per-attempt flag in the attempts endpoint."""
    groups: dict[tuple, list[float]] = defaultdict(list)
    for pid, lang, hid, _aid, _sid, _m, _v, _slug, rt, _cid in _accepted_rows(session):
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
    for pid, lang, hid, _aid, _sid, _m, _v, _slug, rt, _cid in rows:
        groups[(pid, lang, hid)].append(rt)
    flagged = []
    for pid, lang, hid, aid, sid, model, ver, slug, rt, cid in rows:
        ok, ratio, med, npeers = is_outlier(
            rt, groups[(pid, lang, hid)], factor, min_peers, min_median_ms, min_runtime_ms)
        if ok:
            flagged.append({
                "attempt_id": aid,
                "submission_id": sid,
                "problem_slug": slug,
                "language": lang,
                "harness_id": hid,
                "competition_id": cid,
                "model_name": model,
                "model_version": ver,
                "best_runtime_ms": round(rt, 4),
                "peer_median_ms": round(med, 4),
                "ratio": round(ratio, 1),
                "n_peers": npeers,
            })
    flagged.sort(key=lambda r: r["ratio"], reverse=True)
    return flagged


def peer_rows(session: Session, problem_id: int, language: str, harness_id: int | None):
    """The accepted attempts of one (problem, language, harness) peer group, in
    the tuple shape of `_accepted_rows`."""
    return _accepted_rows(session, problem_id=problem_id, language=language,
                          harness_id=harness_id)


# --- code in another language -----------------------------------------------
# A solution must do its work in its own language. These patterns find code
# that starts another process, loads native code, runs WebAssembly, or embeds
# assembly. A match names what it found; like a speed outlier, it is a review
# signal (a pattern can also match a comment or a string).
_PROCESS = "starts another process"
_NATIVE = "loads native code"
_JVM = [(r"\bRuntime\.getRuntime\(\)\.exec\b|\bProcessBuilder\b", _PROCESS),
        (r"\bSystem\.load(Library)?\s*\(", _NATIVE),
        (r"\bnative\s+[\w<>\[\]]+\s+\w+\s*\(", "declares a JNI native method"),
        (r"\bjavax\.script\b|\bScriptEngine\b", "runs a script engine")]
_JS = [(r"\bchild_process\b", _PROCESS),
       (r"\bWebAssembly\b", "runs WebAssembly"),
       (r"\bffi-napi\b|\bnode-ffi\b|\bprocess\.dlopen\b", _NATIVE)]
_C = [(r"(?:(?<![\w.>:])|(?<=std::))(system|popen|execv|execvp|execve|execl|execlp|posix_spawn)\s*\(",
       _PROCESS),
      (r"\bdlopen\s*\(", _NATIVE),
      (r"\b(__asm__|__asm|asm)\b\s*(volatile|__volatile__)?\s*[({]", "embeds assembly")]
FOREIGN_CODE: dict[str, list[tuple[str, str]]] = {
    "python3": [(r"\bctypes\b", "loads native code with ctypes"),
                (r"\bcffi\b", "loads native code with cffi"),
                (r"\bsubprocess\b|\bos\.(system|popen|exec\w*|spawn\w*|posix_spawn\w*)\s*\(",
                 _PROCESS),
                (r"\b(pyximport|Cython)\b", "compiles Cython")],
    "javascript": _JS, "typescript": _JS,
    "java": _JVM, "kotlin": _JVM,
    "scala": _JVM + [(r"\bsys\.process\b", _PROCESS)],
    "csharp": [(r"\bProcess\.Start\b|\bProcessStartInfo\b", _PROCESS),
               (r"\[\s*(DllImport|LibraryImport)\b|\bNativeLibrary\b"
                r"|\bGetDelegateForFunctionPointer\b", _NATIVE)],
    "php": [(r"(?<![\w>$:])(exec|shell_exec|system|passthru|proc_open|popen|pcntl_exec)\s*\(",
             _PROCESS),
            (r"`[^`\n]+`", "runs a shell command (backticks)"),
            (r"\bFFI\s*::|(?<![\w>$:])dl\s*\(", _NATIVE)],
    "cpp": _C, "c": _C,
    "rust": [(r"\bCommand::new\b|\bstd::process::Command\b", _PROCESS),
             (r'\bextern\s+"C"', _NATIVE),
             (r"\b(asm|global_asm)!\s*\(", "embeds assembly")],
    "golang": [(r'"os/exec"|\bsyscall\.Exec\b', _PROCESS),
               (r'\bimport\s+"C"', "calls C through cgo")],
    "swift": [(r"\bProcess\(\)|\bNSTask\b", _PROCESS),
              (r"@_silgen_name|\bdlopen\s*\(", _NATIVE)],
    "dart": [(r"\bProcess\.(run|start|runSync)\b", _PROCESS),
             (r"\bdart:ffi\b", _NATIVE)],
    "ruby": [(r"`[^`\n]+`|%x[({\[]|(?<![\w.])(system|spawn|exec)\s*[( ]|\bIO\.popen\b|\bOpen3\b",
              _PROCESS),
             (r"\bFiddle\b|\bFFI::", _NATIVE)],
    "elixir": [(r"\bSystem\.cmd\b|:os\.cmd\b|\bPort\.open\b", _PROCESS),
               (r":erlang\.load_nif\b|@on_load\b", "loads a native function (NIF)")],
    "erlang": [(r"\bos:cmd\b|\bopen_port\b", _PROCESS),
               (r"\berlang:load_nif\b", "loads a native function (NIF)")],
    "racket": [(r"\((system|system\*|process|process\*|subprocess)\s", _PROCESS),
               (r"\bffi/unsafe\b", _NATIVE)],
}
FOREIGN_CODE["python"] = FOREIGN_CODE["python3"]
_FOREIGN_RE = {lang: [(re.compile(p), why) for p, why in pats]
               for lang, pats in FOREIGN_CODE.items()}


# Comments do not run, so they are removed before the scan: block comments, and
# line comments that start a line or follow a space (so "http://" and Ruby's
# "#{...}" stay).
_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_LINE = {"//": re.compile(r"(^|[ \t;{}()])//.*$", re.M),
         "#": re.compile(r"(^|[ \t])#(?![{\[!]).*$", re.M),
         "%": re.compile(r"(^|[ \t])%.*$", re.M),
         ";": re.compile(r"(^|[ \t]);.*$", re.M)}
_COMMENTS = {"python3": ["#"], "python": ["#"], "ruby": ["#"], "elixir": ["#"],
             "php": ["/*", "//", "#"], "erlang": ["%"], "racket": [";"]}


def _strip_comments(code: str, language: str) -> str:
    for kind in _COMMENTS.get(language, ["/*", "//"]):
        code = _BLOCK.sub(" ", code) if kind == "/*" else _LINE[kind].sub(r"\1", code)
    return code


def foreign_code(code: str | None, language: str) -> list[str]:
    """What in `code` runs code of another language, as reasons (no duplicates)."""
    if not code:
        return []
    code = _strip_comments(code, language)
    found: list[str] = []
    for rx, why in _FOREIGN_RE.get(language, ()):
        if rx.search(code) and why not in found:
            found.append(why)
    return found


def code_flags(session: Session, competition_id: int | None = None) -> dict[int, list[str]]:
    """attempt_id -> reasons for every accepted attempt of a non-reference,
    non-deleted submission (of one competition, if given) whose code runs
    another language."""
    from .ranking_api import REFERENCE_KIND       # avoid an import cycle
    reference = {a.id for a in session.exec(
        select(Agent).where(Agent.kind == REFERENCE_KIND)).all()}
    q = (select(Attempt.id, Attempt.code, Submission.language, Submission.agent_id)
         .join(Submission, Attempt.submission_id == Submission.id)
         .where(Attempt.final_status == "Accepted")
         .where(Submission.deleted_at.is_(None)))
    if competition_id is not None:
        q = q.where(Submission.competition_id == competition_id)
    out: dict[int, list[str]] = {}
    for aid, code, lang, agent_id in session.exec(q).all():
        if agent_id in reference:
            continue
        reasons = foreign_code(code, lang)
        if reasons:
            out[aid] = reasons
    return out


def invalid_attempt_ids(session: Session) -> set[int]:
    """Attempts that an admin labelled invalid (SolutionReview), and attempts
    with a harness error (the correctness oracle accepted them, but the harness
    failed to measure them). They stay accepted, so they still count for pass@k,
    but they enter no performance ranking or comparison and set no
    golden-baseline value."""
    return set(session.exec(select(SolutionReview.attempt_id).where(
        SolutionReview.verdict == "invalid")).all()) | set(session.exec(
        select(Attempt.id).where(Attempt.harness_error.is_not(None))).all())


def harness_errors(session: Session) -> list[dict]:
    """Attempts of non-deleted submissions with a harness error, newest first."""
    q = (select(Attempt, Submission, Problem)
         .join(Submission, Attempt.submission_id == Submission.id)
         .join(Problem, Attempt.problem_id == Problem.id)
         .where(Attempt.harness_error.is_not(None))
         .where(Submission.deleted_at.is_(None)))
    rows = [{"attempt_id": a.id, "submission_id": s.id, "competition_id": s.competition_id,
             "problem_slug": p.title_slug, "language": s.language,
             "model_name": s.model_name, "harness_error": a.harness_error,
             "logged_at": a.logged_at.isoformat() if a.logged_at else None}
            for a, s, p in session.exec(q).all()]
    rows.sort(key=lambda r: (r["logged_at"] or "", r["attempt_id"]), reverse=True)
    return rows
