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
from typing import Callable

from sqlmodel import Session, select

from . import leetcode_dist
from .db import engine
from . import arena_local_runner as _arena
from .models import (
    Attempt, Baseline, BaselineRefreshState,
    Competition, CompetitionProblem, Harness, Machine,
    Problem, Run, Submission, SubmissionStatus,
)
from .measurement_profile import config_hash, get_or_create_profile

def _resolve_leetbot() -> Path:
    """Locate leetbot.py across the layouts we actually deploy to:
      1. ARENA_LEETBOT — explicit override for ops.
      2. /app/leetbot.py — container layout (Dockerfile copies it there).
      3. <repo_root>/leetbot.py — source-tree dev runs.
      4. /leetbot.py — legacy fallback path (older container images).

    /app/leetbot.py MUST be tried before the source-tree fallback: inside
    the container, `Path(__file__).parent.parent.parent.parent` computes
    to `/`, so `src_layout` accidentally resolves to `/leetbot.py`. If we
    picked that first, subprocess cwd would be `/` — where `/app` is a
    directory but not a Python package (the real package is `/app/app/`)
    — and leetbot's `from app.leetcode_accounts import …` would silently
    fail. That would fall back to the single-account .env path, using the
    stale LEETCODE_SESSION from container env, and the pool rotation
    would appear broken with "no signed-in LeetCode account available"
    even though the pool is healthy.

    Returns the first existing candidate; if nothing exists yet we fall
    back to the source-tree default so error messages name the path a
    developer recognises rather than `/leetbot.py`.
    """
    override = os.environ.get("ARENA_LEETBOT")
    if override:
        return Path(override)
    src_layout = Path(__file__).parent.parent.parent.parent / "leetbot.py"
    candidates = (Path("/app/leetbot.py"), src_layout, Path("/leetbot.py"))
    for cand in candidates:
        if cand.is_file():
            return cand
    return src_layout


LEETBOT = _resolve_leetbot()


def _resolve_effective_harness(session: "Session", sub: Submission) -> Harness | None:
    """Which Harness this submission dispatches against.

    Every Submission carries its own harness_id (the API enforces
    `harness_slug` required on POST). We used to fall back to the
    competition's default when Submission.harness_id was NULL, but
    Competition is now decoupled from Harness — the field no longer
    exists in the CRUD API and legacy rows have been backfilled to
    `leetcode` (id=1). Any remaining NULL after that migration means
    a Submission got written by a code path that bypassed the API
    contract — surfacing None here forces the dispatcher onto the
    legacy leetbot fallback (which is `leetcode` by construction),
    matching what those rows would have inherited before.
    """
    if sub.harness_id is not None:
        h = session.get(Harness, sub.harness_id)
        if h is not None:
            return h
    return None


def _is_remote_runner_harness(h: Harness | None) -> bool:
    """True when the harness dispatches via a remote HTTP runner rather
    than leetbot. Requires both an arena-local-ish kind AND a configured
    endpoint — a placeholder harness row with kind='arena-local' but no
    config still falls through to the leetbot path so operators can
    stage a new harness without dispatching submissions to a broken URL."""
    if h is None:
        return False
    if h.kind not in ("arena-local", "custom"):
        return False
    cfg = h.config or {}
    return bool(cfg.get("endpoint"))


def _upsert_machine(session: "Session", desc: dict) -> Machine:
    """Find-or-create the Machine row for a runner-reported descriptor.
    Identity is (name, hostname) — a stable pair for a given box. Non-key
    attributes (cpu_model, cores, energy_source, os) are refreshed on match
    so a hardware/OS detail correction lands without a manual edit."""
    name = desc.get("name") or "unknown"
    hostname = desc.get("hostname")
    q = select(Machine).where(Machine.name == name)
    if hostname is not None:
        q = q.where(Machine.hostname == hostname)
    m = session.exec(q).first()
    if m is None:
        m = Machine(
            name=name, hostname=hostname,
            cpu_model=desc.get("cpu_model"), cores=desc.get("cores"),
            energy_source=desc.get("energy_source"), os=desc.get("os"),
        )
        session.add(m)
        session.commit()
        session.refresh(m)
    else:
        m.cpu_model = desc.get("cpu_model") or m.cpu_model
        m.cores = desc.get("cores") or m.cores
        m.energy_source = desc.get("energy_source") or m.energy_source
        m.os = desc.get("os") or m.os
        session.add(m)
    return m


def _stamp_submission_provenance(
    session: "Session", sub: Submission,
    tools: set[str], machines: list[dict],
    configs: list[dict] | None = None,
) -> None:
    """Enforce ONE energy tool + ONE machine per submission (Parts 2+3).

    A submission dispatches to a single runner, so its runs must all report
    the same energy tool and machine. If the runner reported more than one
    distinct tool/machine (should not happen — surfaced here rather than
    silently averaging across incomparable measurements), we refuse to
    stamp a mixed value and log the conflict; the per-run numbers are still
    persisted, but the submission-level tool/machine stays unset so no one
    mistakes it for a single-provenance measurement.

    Also guards re-dispatch: if the submission was already stamped with a
    machine, a later batch of attempts measured on a DIFFERENT machine is a
    conflict (energy across machines is not comparable) — we log it and keep
    the original stamp."""
    # Energy tool
    if len(tools) == 1:
        tool = next(iter(tools))
        if sub.energy_tool and sub.energy_tool != tool:
            print(f"[dispatch] sub {sub.id}: energy_tool conflict — already "
                  f"{sub.energy_tool!r}, new runs report {tool!r}; keeping "
                  f"original", file=sys.stderr)
        else:
            sub.energy_tool = tool
    elif len(tools) > 1:
        print(f"[dispatch] sub {sub.id}: MULTIPLE energy tools {sorted(tools)} "
              f"in one submission — leaving energy_tool unset", file=sys.stderr)

    # Machine — dedupe reported descriptors by (name, hostname)
    uniq: dict[tuple, dict] = {}
    for d in machines:
        uniq[(d.get("name"), d.get("hostname"))] = d
    if len(uniq) == 1:
        desc = next(iter(uniq.values()))
        m = _upsert_machine(session, desc)
        if sub.machine_id and sub.machine_id != m.id:
            print(f"[dispatch] sub {sub.id}: machine conflict — already "
                  f"machine_id={sub.machine_id}, new runs on machine_id={m.id}; "
                  f"keeping original", file=sys.stderr)
        else:
            sub.machine_id = m.id
    elif len(uniq) > 1:
        print(f"[dispatch] sub {sub.id}: runs span MULTIPLE machines "
              f"{sorted(k[0] for k in uniq)} — leaving machine_id unset",
              file=sys.stderr)

    # Measurement profile — (machine x tool x config). When the submission
    # resolved to a single machine + tool, dedupe the reported configs; if
    # they agree too, upsert the profile and stamp every run of the submission
    # with it (mirrors the single-tool / single-machine rule above).
    if configs and sub.machine_id and sub.energy_tool:
        uniq_cfg = {config_hash(sub.energy_tool, c): c
                    for c in configs if isinstance(c, dict)}
        if len(uniq_cfg) == 1:
            cfg = next(iter(uniq_cfg.values()))
            profile = get_or_create_profile(
                session, sub.machine_id, sub.energy_tool, cfg)
            run_rows = session.exec(
                select(Run).join(Attempt).where(Attempt.submission_id == sub.id)
            ).all()
            for r in run_rows:
                r.measurement_profile_id = profile.id
                session.add(r)
        elif len(uniq_cfg) > 1:
            print(f"[dispatch] sub {sub.id}: runs span MULTIPLE configs "
                  f"— leaving measurement_profile unset", file=sys.stderr)

    # Unique measurement-run identifier: a stable opaque handle for THIS
    # submission's measurement, distinct from the autoincrement id, for
    # correlating with external measurement logs. Set once, only for
    # submissions that were actually measured (tool or machine present).
    if sub.run_uid is None and (tools or uniq):
        import uuid
        sub.run_uid = uuid.uuid4().hex
    session.add(sub)


def _resolve_effective_correctness_oracle(
    session: "Session", sub: Submission,
) -> "CorrectnessOracle | None":
    """Which CorrectnessOracle judges this submission.

    Just Submission.correctness_oracle_id — no competition fallback,
    matching the decoupled harness resolution above. When NULL,
    correctness comes from the perf harness's own response (LC does
    both; arena-local's perfarena_handler runs `make validate` inside
    `make measure`).
    """
    from .models import CorrectnessOracle  # local import — avoid cycles
    if sub.correctness_oracle_id is not None:
        o = session.get(CorrectnessOracle, sub.correctness_oracle_id)
        if o is not None:
            return o
    return None


def _is_remote_validate_oracle(o: "CorrectnessOracle | None") -> bool:
    """True when the oracle judges via a remote HTTP /validate call
    (rather than piggybacking on the perf harness's own verdict, which
    is what kind='leetcode' means). Same endpoint-presence rule as the
    harness variant so a half-configured oracle row can't silently
    misroute a live sweep."""
    if o is None:
        return False
    if o.kind not in ("arena-local", "custom"):
        return False
    cfg = o.config or {}
    return bool(cfg.get("endpoint"))


# Statuses that are transport/quota failures, NOT a real correctness verdict.
# Mirrors the loop-local set used on the remote-runner path.
_TRANSIENT_STATUSES = frozenset({
    "Rate limited", "Auth failed", "Dispatch timeout", "Dispatch error",
    "Dispatch parse error", "LeetCode transient error", "dispatch_warning",
    "Unknown",
})


def _unwrap_ruby_class_for_leetcode(code: str) -> str:
    """LeetCode's Ruby judge calls the solution method at TOP LEVEL (its driver
    does `ret = some_method(param)`), so a method the model tucked inside
    `class Solution ... end` is invisible and raises
    `undefined method '...' for main:Object`. Models routinely wrap non-design
    solutions in a class (the system prompt asks for the "Solution shape"),
    which is semantically fine but fails LeetCode's harness — while our own
    arena harness.rb accepts it via `Solution.new`. Lift the body of a single
    wrapping class to top level so the method is callable on both oracles.

    DESIGN problems (LeetCode instantiates the class itself) are left alone; a
    `def initialize` constructor is the tell."""
    lines = code.split("\n")
    def _indent(l): return len(l) - len(l.lstrip())
    ci = next((i for i, l in enumerate(lines)
               if l.lstrip().startswith("class ") and l.lstrip()[6:7].isupper()), None)
    if ci is None:
        return code                                   # already top-level
    if any(l.lstrip().startswith("def initialize") for l in lines):
        return code                                   # design class -> keep
    indent = _indent(lines[ci])
    ei = next((i for i in range(len(lines) - 1, ci, -1)
               if lines[i].strip() == "end" and _indent(lines[i]) == indent), None)
    if ei is None:
        return code
    body = lines[ci + 1:ei]
    dents = [_indent(l) for l in body if l.strip()]
    d = min(dents) if dents else 0
    body = [l[d:] if len(l) >= d else l for l in body]
    return "\n".join(lines[:ci] + body + lines[ei + 1:]).strip() + "\n"


def _strip_c_main_for_leetcode(code: str) -> str:
    """LeetCode's C judge appends its OWN main()-bearing driver to solution.c, so
    a model-added `int main(...) { ... }` (test scaffolding) collides with it ->
    "redefinition of 'main' [solution.c]". LeetCode C solutions are function-only,
    so strip every top-level main via brace matching (mirrors harness_c's
    arena-side strip, which runs too late here because the leetcode gate rejects
    before the arena harness is ever reached)."""
    import re as _re
    while True:
        m = _re.search(r'\b(?:int|void)\s+main\s*\([^)]*\)\s*\{', code)
        if not m:
            break
        depth, j = 0, m.end() - 1
        while j < len(code):
            if code[j] == '{':
                depth += 1
            elif code[j] == '}':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        code = code[:m.start()] + code[j + 1:]
    return code.strip() + "\n"


def _unwrap_ts_class_for_leetcode(code: str) -> str:
    """LeetCode TypeScript expects a TOP-LEVEL `function name(...)`, but models
    wrap the solution in `class Solution { name(...) {...} }` (the "Solution
    class" prompt) -> LeetCode's driver can't find the function
    (`TS2304: Cannot find name '...'`). Lift the class body to top level,
    converting each method to a function. Design classes (they have a
    `constructor`) are left alone."""
    import re as _re
    lines = code.split("\n")
    ci = next((i for i, l in enumerate(lines)
               if _re.match(r'\s*(?:export\s+)?class\s+Solution\b', l)), None)
    if ci is None:
        return code
    if any(_re.match(r'\s*constructor\s*\(', l) for l in lines):
        return code                                   # design class -> keep
    indent = len(lines[ci]) - len(lines[ci].lstrip())
    ei = next((i for i in range(len(lines) - 1, ci, -1)
               if lines[i].strip() == "}" and (len(lines[i]) - len(lines[i].lstrip())) == indent), None)
    if ei is None:
        return code
    body = lines[ci + 1:ei]
    dents = [len(l) - len(l.lstrip()) for l in body if l.strip()]
    d = min(dents) if dents else 0
    body = [l[d:] if len(l) >= d else l for l in body]
    out = []
    for l in body:                                    # dedented -> methods sit at col 0
        m = _re.match(r'^(?:public |private |protected |static |async |readonly )*'
                      r'([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\(', l)
        if m and not l.lstrip().startswith(("function ", "//", "/*", "*",
                                            "return", "if", "for", "while", "switch")):
            l = "function " + l[m.start(1):]
        # sibling-method calls used `this.` inside the class; now that the
        # methods are top-level functions (a non-design solution has no instance
        # state), `this.` just resolves to the function name.
        out.append(l.replace("this.", ""))
    return "\n".join(lines[:ci] + out + lines[ei + 1:]).strip() + "\n"


def _strip_ts_node_defs(code: str) -> str:
    """LeetCode's TS judge pre-defines TreeNode / ListNode; a model that ALSO
    defines them triggers `TS2300: Duplicate identifier`. Strip each top-level
    `class <Name> { ... }` (brace-matched). The model's own references
    (`root: TreeNode | null`) still resolve against the judge's definitions.
    `Node` is deliberately NOT stripped — it's problem-specific (a model may
    define its own helper `Node` the judge doesn't provide)."""
    import re as _re
    for name in ("TreeNode", "ListNode"):
        m = _re.search(r'^[ \t]*class\s+' + name + r'\b', code, _re.M)
        if not m:
            continue
        bpos = code.index("{", m.start())
        depth, j = 0, bpos
        while j < len(code):
            if code[j] == "{":
                depth += 1
            elif code[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        code = (code[:m.start()] + code[j + 1:])
    return code


def _normalize_for_leetcode(language: str, code: str) -> str:
    """Per-language LeetCode-judge shape fixes, one dispatch point."""
    if language == "ruby":
        return _unwrap_ruby_class_for_leetcode(code)
    if language == "c":
        return _strip_c_main_for_leetcode(code)
    if language == "typescript":
        return _unwrap_ts_class_for_leetcode(_strip_ts_node_defs(code))
    return code


def _leetbot_validate(problem: "Problem", language: str, code: str,
                      session: "Session") -> dict:
    """Correctness-only LeetCode check: ONE leetbot submission, return just the
    verdict. Perf fields are discarded — performance comes from the perf
    harness. This is the LeetCode side of the oracle-independent correctness
    gate (arena-local uses its HTTP /validate endpoint instead)."""
    # normalization now happens centrally in _real_dispatch_repeated, so every
    # leetbot submission (this gate + the direct measure path) is covered.
    try:
        runs = _real_dispatch_repeated(problem, language, code, repeats=1,
                                       session=session)
    except Exception as e:  # noqa: BLE001
        return {"status_msg": "dispatch_warning", "system_failure": True,
                "raw_response": {"note": f"leetbot correctness gate error: {e}"}}
    r = runs[0] if runs else {}
    status = r.get("status_msg", "Unknown")
    return {
        "status_msg": status,
        "total_correct": r.get("total_correct"),
        "total_testcases": r.get("total_testcases"),
        "raw_response": r.get("raw_response") or r.get("leetcode_full_response") or {},
        # A system failure (LC outage / transient) must NOT read as a real
        # verdict — the caller keeps the attempt pending, same as /validate.
        "system_failure": status in _TRANSIENT_STATUSES,
    }


def _correctness_gate(session: "Session", sub: Submission, code: str,
                      problem: "Problem",
                      oracle: "CorrectnessOracle | None") -> dict | None:
    """Oracle-independent correctness check. Returns a verdict dict
    (status_msg, raw_response, ...), or None when the oracle can't gate.
      * remote /validate oracle (arena-local) -> HTTP /validate
      * leetcode oracle                        -> one leetbot submission
    Perf is measured separately by the perf harness, only when this is
    Accepted. Skipping correctness = no correctness oracle on the submission.

    A single call is enough; it is retried ONLY on a transient/timeout failure
    (never on a real verdict), up to _CORRECTNESS_GATE_RETRIES extra tries."""
    if oracle is None:
        return None

    def _one() -> "dict | None":
        if _is_remote_validate_oracle(oracle):
            return _arena.validate_via_arena_local(
                problem, sub.language, code, oracle,
                meta={"submission_id": sub.id},
            )
        if oracle.kind == "leetcode":
            return _leetbot_validate(problem, sub.language, code, session)
        return None

    last: "dict | None" = None
    for i in range(_CORRECTNESS_GATE_RETRIES + 1):
        vr = _one()
        if vr is None:
            return None
        if not vr.get("system_failure"):
            return vr                        # real verdict — done, no retry
        last = vr
        if i < _CORRECTNESS_GATE_RETRIES:
            print(f"[dispatch] correctness gate transient "
                  f"({vr.get('status_msg')}) — retry "
                  f"{i + 1}/{_CORRECTNESS_GATE_RETRIES}", file=sys.stderr)
    return last                              # exhausted — return the transient


# Per-ACCOUNT inter-submission gap. Drives `pick_for_submission(min_gap_s=
# INTER_DELAY)` — the picker refuses to return an account whose
# `last_used_at + INTER_DELAY` is still in the future, falling through to
# the next-LRU account instead. With a multi-account pool this works as
# rotation: the wait_seconds the picker returns is just whatever the
# LRU account still needs to cool down to ITS own 120 s mark, NOT the
# wall-clock gap between submissions globally.
#
# History:
#   * 12s tripped the heuristic instantly on multi-problem submissions.
#   * 60s held up for a while but recently started exhausting the whole
#     pool mid-sweep — LC tightened its frontend.
#   * 120s (original) paced single-account dispatch slower than a focused
#     human; full 99-problem sweep ran in ~3.3h instead of ~100 min.
#   * 96s (-20%) was a brief reduction tried with a multi-account pool;
#     reverted because rotation already amortises the wait — a tighter
#     PER-ACCOUNT cap doesn't actually speed up the wall-clock sweep
#     much when 5 accounts share the burst, and dropping it raises the
#     per-account hit rate the LC anti-bot heuristic samples on.
#   * 120s (current) — restored. The wall-clock pacing now comes from
#     rotation, not from this cap; the cap is purely "don't let any one
#     account submit more than once per 120 s".
# Override via ARENA_DISPATCH_INTER_DELAY when needed.
INTER_DELAY = float(os.environ.get("ARENA_DISPATCH_INTER_DELAY", "120"))
MAX_RETRIES = int(os.environ.get("ARENA_DISPATCH_MAX_RETRIES", "3"))
# Flat rate-limit / transient-HTTP retry backoff. The semantic changed
# from "ceiling on exponential" to "fixed wait between retries". We
# kept the env-var name for back-compat.
#
# Why flat (not exponential): exponential growth (5, 10, 20, 40 …) was
# either fast (BACKOFF_CAP_S=0 → all retries in milliseconds, looked
# like one big burst to LC's per-IP throttle and got everyone 429'd) or
# slow (BACKOFF_CAP_S=90 → retries spanned 5+ min of wall time and
# every dispatch felt sluggish). A FLAT backoff means each retry waits
# exactly `BACKOFF_CAP_S` seconds (±20% jitter) — spaced enough that
# consecutive requests fall in different per-IP throttle windows,
# without compounding into multi-minute waits.
#
# Default 8 s: empirically the per-IP throttle window LC enforces is
# ~5-10 s; 8 s with jitter lands the next retry just outside the window.
# Set to 0 to retry instantly (matches old burst behaviour); set to
# something like 30 if LC's throttle is biting deeper.
BACKOFF_CAP_S = float(os.environ.get("ARENA_DISPATCH_BACKOFF_CAP_S", "8"))
# Raised from 600 → 1200 to accommodate the TLE-retry worst case: a
# genuinely-TLE solution can drive REPEATS × (1 + TLE_RETRIES) submissions,
# each carrying judge time + retry/repeat delays. Normal cases finish in
# well under a minute; this is just the ceiling before we declare the
# leetbot subprocess hung.
SUBPROCESS_TIMEOUT = int(os.environ.get("ARENA_DISPATCH_SUBPROC_TIMEOUT", "1200"))
REPEATS = max(1, int(os.environ.get("ARENA_DISPATCH_REPEATS", "3")))
# Correctness gate retries: retry the SINGLE correctness call ONLY on a
# transient/timeout failure (never on a real verdict). Default: 1 retry.
_CORRECTNESS_GATE_RETRIES = max(0, int(os.environ.get("ARENA_CORRECTNESS_RETRIES", "1")))


def _repeats_for_harness(h: "Harness | None") -> int:
    """Perf repeats for THIS harness. arena-local already averages within each
    measure (the per function call loop), so a harness may lower the count via
    config['repeats']; LeetCode keeps the global count (each repeat is a
    separate submission)."""
    if h is not None:
        n = (h.config or {}).get("repeats")
        if isinstance(n, int) and n >= 1:
            return n
    return REPEATS
# On a Time Limit Exceeded verdict, leetbot re-submits up to this many extra
# times before accepting the TLE — TLE is judge-load-flaky and a borderline
# solution often passes on retry. Applied per repeat (so the worst case is
# REPEATS × (1 + TLE_RETRIES) LeetCode submissions for a genuinely-TLE
# solution). Bumped SUBPROCESS_TIMEOUT may be needed if both are large.
TLE_RETRIES = int(os.environ.get("ARENA_DISPATCH_TLE_RETRIES", "3"))
# Seconds between leetbot's --repeat=N submissions of the SAME solution.
# leetbot's own default is 12 s; we set it explicitly so the value is
# visible at the dispatch layer (rather than hidden inside the subprocess's
# argparse default) and tunable per deployment. History: 12 → 16 (+30%)
# after observing the 5-repeat burst occasionally tripping LC's per-
# account per-minute throttling on slower problems; then 25 → 20 after
# the per-account picker shifted the pacing budget across the pool
# rather than concentrating it on one account; 20 → 16 (-20%) on the
# same logic, since the 5-account pool's per-account share of the
# 5-repeat burst is just one submission per account (no contention).
# Now 16 → 21 — modest bump to give LC's per-IP throttle a touch more
# headroom between the three same-code submits, since concurrent sweeps
# (java + python3 + c + rust + scala from one IP) have been hot enough
# lately that even rotated accounts hit IP-side 429s on every dispatch.
# Note: the dispatcher adds ±20% jitter on top of this, so the realised
# gap is ~16.8-25.2 s. Tighten only on accounts LC has aged into trust.
REPEAT_DELAY = float(os.environ.get("ARENA_DISPATCH_REPEAT_DELAY", "21"))
# Per-submission LC poll ceiling — how long we wait for ONE of the 5
# repeats to come back with a verdict. Anything slower than this is
# treated as a system-level dispatch error (no Run row, dispatch_warning
# stamped, sweep retries next run). 60s was leetbot's pre-platform
# default, raised to 180s after observing legitimate verdicts arrive in
# the 60-150s range on slow judge windows.
POLL_TIMEOUT = float(os.environ.get("ARENA_DISPATCH_POLL_TIMEOUT", "180"))


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
    """Backwards-compatible boolean wrapper around `_rate_limit_evidence`.
    Kept for call sites that only need yes/no; new code should prefer
    the evidence helper so the matched HTTP code + line can be logged."""
    return _rate_limit_evidence(proc) is not None


def _rate_limit_evidence(proc: subprocess.CompletedProcess) -> str | None:
    """Return a short excerpt of the line in leetbot's output that
    triggered our rate-limit classification, or None if not rate-limited.

    CRITICAL: only matches "429" in actual HTTP-status CONTEXT — not as
    a bare digit substring anywhere. The previous "429 in lowered blob"
    test caught false positives on LeetCode verdicts that happened to
    include a test-case integer containing the digits 4-2-9 (e.g. a
    failing testcase array with `429422629`). The dispatcher then mis-
    classified a perfectly-good Wrong Answer / Runtime Error verdict as
    a rate-limit failure, retried 3 times, got the same testcase back
    each time, and stamped the attempt "Rate limited" — *with no
    actual rate limit anywhere*. The user's solutions were dispatching
    fine; the dispatcher was destroying their verdicts.

    Patterns we DO accept:
      * "http 429"               — httpx.HTTPStatusError shape
      * "429 too many requests"  — full HTTP 429 status line
      * "status 429" / "status: 429" / "status_code: 429"
      * "rate limit" / "rate limited" / "ratelimited"  — explicit text
      * "_RateLimited" / "[leetbot] rate limited" — our own markers
      * "too many requests"      — http 429 body without the digits

    Patterns we DON'T accept:
      * bare "429" anywhere in the blob (was the bug)
      * any digit substring at all
    """
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # Patterns we accept as rate-limit evidence. ALL require some kind
    # of HTTP / human-readable context — NEVER bare digits, NEVER a
    # Python class name like `_RateLimited`. False positives we've
    # actually hit and explicitly reject:
    #   * "429" appearing in a LeetCode failing-testcase array
    #     (`[985372746, 429422629, ...]` triggered the "429" substring)
    #   * "ratelimited" (no space) matching the Python class name
    #     `_RateLimited` printed inside a traceback source line
    #     (`raise _RateLimited(account_name)`)
    # Real rate limit signals ALWAYS include human-readable phrasing
    # ("rate limit"/"rate limited"/"too many requests") OR an explicit
    # HTTP-status code in context — they never look like a bare class
    # name or a bare digit.
    PATTERNS = (
        "http 429",
        "status 429",
        "status: 429",
        "status_code: 429",
        "status_code 429",
        "429 too many",
        "rate limit",                   # also catches "rate limited"
        "rate-limit",                   # hyphenated variant
        "too many requests",
    )
    # Lines we IGNORE even when they would otherwise match — these are
    # Python source-code snippets surfaced by an unfiltered traceback.
    # The actual triggering message will be on a different stderr line.
    PYTHON_SOURCE_NEEDLES = (
        "raise _ratelimited",          # `raise _RateLimited(...)` lowercased
        "except _ratelimited",
        "class _ratelimited",
        "_ratelimited.__",
    )
    for line in out.splitlines():
        low = line.lower()
        if any(needle in low for needle in PYTHON_SOURCE_NEEDLES):
            # Pure Python source code in a traceback — not LC's response.
            continue
        if any(p in low for p in PATTERNS):
            trimmed = line.strip()
            # Truncate so the log line stays grep-friendly; the full
            # output is still available via the run row's raw_response /
            # the dispatch_warning text further downstream.
            if len(trimmed) > 240:
                trimmed = trimmed[:237] + "..."
            return trimmed
    return None


# LeetCode hiccups produce real HTTP failures even on well-formed submits.
# 400 specifically is observed when their judge frontend is overloaded; 5xx
# covers the actual outages and the Cloudflare-style 521/522/524 family.
# We deliberately DO NOT include 401/403/404 here — those are durable
# auth/path problems that retrying just multiplies. `_classify_subproc_failure`
# keeps handling those.
_TRANSIENT_HTTP_PATTERNS = (
    "http 400", "http 408", "http 425", "http 429",
    "http 500", "http 502", "http 503", "http 504",
    "http 520", "http 521", "http 522", "http 523", "http 524",
)


def _transient_http_match(proc: subprocess.CompletedProcess) -> str | None:
    """Return the matched 'http NNN' substring if leetbot reported a
    transient HTTP failure we want to retry, else None. Lowercase the haystack
    once and scan — leetbot emits these in stderr via the RuntimeError shape
    `submit failed: HTTP {code} content-type=...`, so the substring is stable
    across hosts."""
    blob = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    for pat in _TRANSIENT_HTTP_PATTERNS:
        if pat in blob:
            return pat
    return None


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
        # Look back further than just the last line — leetbot's tracebacks
        # frequently end with a blank line, and an empty "tail" leaves the
        # operator staring at the unhelpful "Dispatch error: " with no
        # explanation. Take the last non-empty 3 lines and join them.
        stderr_lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
        snippet = (" | ".join(stderr_lines[-3:]) or "no stderr output")[:400]
        return "Dispatch error", f"leetbot exited {proc.returncode}: {snippet}"
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
    """Pre-flight: is at least ONE account in the pool able to talk to LC?

    Earlier this shelled out to `leetbot.py --whoami` — fragile because
    leetbot has to re-import `app.leetcode_accounts` from the subprocess
    (cwd / sys.path / env-var differences caused it to fall through to
    the .env-credentials path and exit 2). That false negative would
    stamp every pending attempt with "Auth failed" and SKIP the dispatch,
    even when the pool was perfectly healthy — which is exactly what
    happened to sub 56/58 and caused the "queued forever" behaviour the
    operator hit.

    The new check is in-process: iterate the pool, try each account's
    whoami GraphQL probe (the same `leetcode_accounts.whoami` the
    /admin/leetcode-accounts/probe endpoint uses — verified live to
    work). Return ok=True as soon as ONE account answers; the dispatcher
    only needs to know "is there ANY usable account right now", not
    which one. Only return False when EVERY account fails.
    """
    from . import leetcode_accounts as _lca
    usable = [a for a in _lca.load_accounts()
              if a.cookie_header and not a.is_exhausted()]
    if not usable:
        return False, ("no accounts in the pool are both cookie-bearing AND "
                       "outside the 24h exhaustion window")
    last_msg = "no accounts tried"
    # Network/DNS hiccups (Docker resolver flap, host network changing,
    # cloudflare's edge briefly 502'ing) can make every whoami probe fail
    # with the same shape — "Temporary failure in name resolution",
    # "Connection refused", "Read timed out". None of those are auth
    # failures. If we let the pre-flight stamp `dispatch_warning =
    # "Auth failed: …"` on every pending attempt, the sweep gives up
    # on cookies that are actually fine. So: distinguish transport
    # errors (which we treat as transient → bail OUT of the pre-flight
    # and let the dispatch itself try, which has its own retry budget
    # and will surface the same error with proper rate-limit semantics)
    # from real "your cookie was rejected" failures.
    TRANSIENT_NETWORK_SIGNALS = (
        "temporary failure in name resolution",  # DNS hiccup
        "name or service not known",             # DNS unresolvable
        "connection refused",
        "connection reset",
        "connection timed out",
        "read timed out", "read timeout",
        "no route to host",
        "errno -2", "errno -3",                  # gaierror codes
        "remoteprotocolerror", "readtimeout",
        "httpx.connecterror", "httpx.readerror",
        "ssl: ", "[ssl:",
    )
    n_transient = 0
    for acc in usable:
        try:
            ok, info = _lca.whoami(acc)
        except Exception as e:                                     # noqa: BLE001
            err_blob = repr(e).lower()
            if any(s in err_blob for s in TRANSIENT_NETWORK_SIGNALS):
                n_transient += 1
                last_msg = f"{acc.name}: transient network error {e!r}"
            else:
                last_msg = f"{acc.name}: probe raised {e!r}"
            continue
        if ok:
            return True, f"{info} (via account {acc.name!r})"
        last_msg = f"{acc.name}: {info}"
    # Every probe failed AND every failure looked transport-flavoured →
    # don't stamp "Auth failed" on the attempts. Return ok=True with a
    # diagnostic note; the dispatcher caller (which uses this only as a
    # cheap "should I bother") will proceed to actual leetbot dispatch,
    # which has its own retry-with-backoff loop better suited to a
    # DNS / network hiccup than a global pre-flight bail.
    if n_transient == len(usable) and n_transient > 0:
        return True, (f"pre-flight skipped — {n_transient} accounts hit "
                      f"transient network errors (last: {last_msg}); the "
                      f"dispatcher's retry loop will handle it")
    return False, (f"every account failed the pre-flight probe; "
                   f"last error: {last_msg}")


def _normalize_runs(parsed: dict | None) -> list[dict]:
    """leetbot prints a single run dict (repeat=1) or {"runs":[...]} (repeat>1)."""
    if parsed is None:
        return []
    if isinstance(parsed.get("runs"), list):
        return parsed["runs"]
    return [parsed]


def _resolve_dispatch_target(problem: Problem, code: str,
                             session: Session) -> tuple[str, str]:
    """For a Problem that may be an anti-contamination VARIANT, resolve the
    pair (lc_title_slug, code) we should actually submit to LeetCode.

    On canonical problems this is a no-op — returns the problem's own
    title_slug and the code unmodified. On variants we:
      * redirect to the ORIGINAL problem's title_slug (LC only knows that
        one — submitting under the variant's slug 404s);
      * rewrite the code by replacing every occurrence of the variant's
        `function_name` with `original_function_name` so LC's judge can
        find the entry point.

    Substitution is done with simple `str.replace` not regex word-boundary
    matching — function names in our rewrites are intentionally distinctive
    enough (camelCase, multi-word) that the false-positive risk is low and
    the predictability of the transform matters for auditability.
    """
    if problem.original_problem_id is None:
        return problem.title_slug, code
    original = session.get(Problem, problem.original_problem_id)
    if original is None:
        # Defensive: the variant references a missing original. Submit
        # under the variant's own slug — LC will 404, dispatch records the
        # error, an operator notices. Better than silently submitting bad
        # code under a wrong slug.
        return problem.title_slug, code
    if (problem.function_name
            and problem.original_function_name
            and problem.function_name != problem.original_function_name):
        code = code.replace(problem.function_name, problem.original_function_name)
    return original.title_slug, code


def _real_dispatch_single(title_slug: str, language: str, path: str,
                          sub_env: dict[str, str]) -> list[dict]:
    """ONE leetbot subprocess for ONE LC submission (--repeat 1).

    Owns the rate-limit retry loop — on 429 / transient HTTP / Cloudflare
    52x it backs off and re-invokes leetbot up to `MAX_RETRIES` times.
    Returns a list-of-one run dict on success (parsed leetbot JSON), or
    a list-of-one synthetic failure dict on a classified subprocess
    error / retry budget exhaustion.

    Account rotation on rate-limit: when a 429 comes back, before
    backing off we rotate `LEETCODE_FORCE_ACCOUNT` to a different
    (non-exhausted, not-yet-tried-this-call) account in the pool. The
    IP throttle isn't per-account but the per-COOKIE throttle is, so
    a fresh cookie can sometimes slip through where the previous one
    couldn't. When the pool runs out of fresh cookies we fall back to
    retrying with the current account (so the budget still completes
    cleanly rather than dying early).
    """
    from . import leetcode_accounts as _lca
    backoff = 5.0
    proc: subprocess.CompletedProcess | None = None
    # Track every account we've used in this single-LC-submit call so
    # we don't pick the same one again across retries.
    tried_accounts: set[str] = set()
    initial_account = sub_env.get("LEETCODE_FORCE_ACCOUNT")
    if initial_account:
        tried_accounts.add(initial_account)
    for attempt_n in range(MAX_RETRIES + 1):
        try:
            proc = subprocess.run(
                [
                    sys.executable, str(LEETBOT),
                    title_slug, language, path,
                    "--repeat", "1",
                    "--poll-timeout", str(POLL_TIMEOUT),
                    "--tle-retries", str(TLE_RETRIES),
                ],
                capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
                cwd=str(LEETBOT.parent),
                env=sub_env,
            )
        except subprocess.TimeoutExpired:
            return _failure_runs(1, "Dispatch timeout",
                                 f"leetbot exceeded {SUBPROCESS_TIMEOUT}s")
        # Account-usage accounting: previously this marked the account
        # as used IMMEDIATELY after the subprocess returned, regardless
        # of outcome. That was wrong for 429s — LC didn't actually
        # accept the submission, no quota was spent, the cookie is
        # genuinely usable for another attempt without violating LC's
        # per-account anti-abuse heuristic. Marking it anyway forced
        # the OUTER picker to wait the full 120 s cooldown on the
        # next dispatch, even though the only LC interaction was a
        # rejected request.
        #
        # New rule: defer the mark_used decision until we've classified
        # the response below. We only mark when LC actually engaged
        # (real verdict or auth-fail / parse error — both indicate LC
        # accepted the request and got something back).
        # Retryable failures fall into two buckets, handled identically
        # (same backoff curve, same retry budget): rate limits and
        # transient HTTP errors (400/408/5xx/Cloudflare 52x). Both
        # signal "LeetCode's frontend hiccupped; try again in a bit".
        # CHECK FIRST regardless of attempt_n — otherwise the final
        # iteration falls through to `_classify_subproc_failure` which
        # mis-labels a still-rate-limited leetbot as "Dispatch error"
        # (the operator sees that in dispatch_warning and assumes
        # something broke when actually LC is just throttling).
        rl_evidence = _rate_limit_evidence(proc)
        rate_limited = rl_evidence is not None
        transient = None if rate_limited else _transient_http_match(proc)
        if rate_limited or transient:
            if attempt_n < MAX_RETRIES:
                # Try rotating to a different account BEFORE backing off
                # — a fresh cookie can slip past a per-cookie throttle
                # that the previous one couldn't, and there's no point
                # waiting 8 s only to retry with the exact same cookie
                # LC just 429'd.
                rotated_to: str | None = None
                try:
                    candidates = [
                        a for a in _lca.load_accounts()
                        if a.cookie_header and not a.is_exhausted()
                        and a.name not in tried_accounts
                    ]
                    if candidates:
                        # Prefer the LEAST-recently-used among untried
                        # candidates — same LRU semantics the picker
                        # uses for fresh dispatches, just without the
                        # cooldown gate (we're past politeness; we're
                        # actively being throttled).
                        candidates.sort(
                            key=lambda a: a.last_used_at or "",
                        )
                        rotated_to = candidates[0].name
                        sub_env["LEETCODE_FORCE_ACCOUNT"] = rotated_to
                        tried_accounts.add(rotated_to)
                except Exception as e:                                 # noqa: BLE001
                    print(f"[dispatch] account rotation lookup failed: "
                          f"{e!r}; staying on current account",
                          file=sys.stderr)
                # NOTE: backoff is FLAT now (not exponential) — see the
                # BACKOFF_CAP_S doc comment for the rationale.
                raw = backoff * (2 ** attempt_n)
                sleep = min(BACKOFF_CAP_S, raw) * (1.0 + random.uniform(-0.2, 0.2))
                # Tag the log line with the rotation decision so the
                # operator can see whether we tried a fresh cookie or
                # stayed on the throttled one (pool exhausted of fresh
                # cookies). Include the evidence string too — same as
                # before — for "why did we decide this was a 429".
                rot_tag = (f"rotating to account {rotated_to!r}"
                           if rotated_to
                           else "no fresh accounts left in pool, retrying current")
                if rate_limited:
                    print(f"[dispatch] rate limited (evidence: {rl_evidence!r}); "
                          f"{rot_tag}; backing off {sleep:.1f}s "
                          f"(attempt {attempt_n + 1}/{MAX_RETRIES})",
                          file=sys.stderr)
                else:
                    print(f"[dispatch] transient HTTP error {transient}; "
                          f"{rot_tag}; backing off {sleep:.1f}s "
                          f"(attempt {attempt_n + 1}/{MAX_RETRIES})",
                          file=sys.stderr)
                time.sleep(sleep)
                continue
            # Budget exhausted but the failure was still rate-limit /
            # transient HTTP — say so honestly so the operator can tell
            # apart a real dispatcher bug from "LC just throttled us".
            # Include the matched evidence so the SPA's dispatch_warning
            # column shows the actual HTTP shape leetbot saw.
            if rate_limited:
                return _failure_runs(
                    1, "Rate limited",
                    f"hit LeetCode rate limit after {MAX_RETRIES} retries "
                    f"(last seen: {rl_evidence})",
                    proc,
                )
            return _failure_runs(
                1, "LeetCode transient error",
                f"hit {transient} on every one of {MAX_RETRIES + 1} attempts",
                proc,
            )
        # Past the rate-limit / transient retry branch above means LC
        # actually engaged with this account — either it returned a
        # real verdict OR it rejected the cookie for a reason that's
        # NOT throttling (auth dead, parse error, …). Mark the
        # account as used so the per-account cooldown is honest.
        current_acct = sub_env.get("LEETCODE_FORCE_ACCOUNT")
        if current_acct:
            try:
                _lca.mark_used(current_acct)
            except Exception as e:                                     # noqa: BLE001
                print(f"[dispatch] failed to mark_used {current_acct!r}: {e!r}",
                      file=sys.stderr)
        runs = _normalize_runs(_parse_leetbot_stdout(proc.stdout))
        if not runs:
            classified = _classify_subproc_failure(proc)
            if classified is not None:
                status_msg, note = classified
                return _failure_runs(1, status_msg, note, proc)
            return _failure_runs(1, "Dispatch parse error",
                                 "leetbot produced no JSON we could parse",
                                 proc)
        return runs[:1]                     # exactly ONE run per call
    # Unreachable — every path inside the loop returns. Kept as a
    # defensive net only.
    assert proc is not None
    return _failure_runs(1, "Dispatch error",
                         "retry loop exited without returning",
                         proc)


def _real_dispatch_repeated(problem: Problem, language: str, code: str, repeats: int,
                            session: Session | None = None,
                            force_account: str | None = None,
                            on_run_completed: "Callable[[dict, int], None] | None" = None,
                            ) -> list[dict]:
    """Submit `code` `repeats` times via leetbot, return the list of run dicts.

    Changed: instead of one `leetbot --repeat N` subprocess returning all
    N runs at once after ~3-5 min, we now run N separate subprocesses
    (`--repeat 1` each), sleeping `REPEAT_DELAY` between them and
    invoking `on_run_completed(run, index_1_based)` after each. The
    callback lets the outer dispatcher persist a Run row + commit
    incrementally, so the SPA's "runs landed" counter advances live
    (0/3 → 1/3 → 2/3 → 3/3) while the dispatch is still in flight.

    `session` is the SQLModel session for resolving variant problems to
    their original. Optional for back-compat with older callers — without
    it the variant resolution is skipped (canonical problems work fine).

    `force_account` pins a specific pool account name for this whole
    sequence of N subprocesses (all `repeats` LC submissions use it).
    Forwarded to leetbot via the LEETCODE_FORCE_ACCOUNT env var. None =
    leetbot falls back to its own pick_account() ordering.
    """
    if session is not None:
        title_slug, code = _resolve_dispatch_target(problem, code, session)
    else:
        title_slug = problem.title_slug
    # LeetCode-judge shape fixes, applied at THE choke point so every leetbot
    # submission is covered (correctness gate AND the direct measure path), not
    # just _leetbot_validate. Ruby/C/TS use top-level defs; models wrap them in
    # `class Solution` / add a `main` (the "Solution class" prompt), which the
    # judge rejects.
    code = _normalize_for_leetcode(language, code)
    # Per-subprocess env override for account pinning. We copy os.environ
    # so the parent process's env isn't mutated mid-flight (the API and
    # the dispatcher run on different threads — a shared mutation would
    # be racy).
    sub_env = os.environ.copy()
    if force_account:
        sub_env["LEETCODE_FORCE_ACCOUNT"] = force_account
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(code)
        path = f.name
    # leetbot's own --repeat=N path has a deterministic-failure short-
    # circuit: when one run returns Wrong Answer / Compile Error, re-running
    # the SAME code against the SAME judge would just produce the SAME
    # verdict — pure waste of LC quota + wall time. The refactor to
    # one-call-per-repeat lost that optimisation, so we re-implement it
    # here at the loop level — but ONLY for truly-deterministic verdicts.
    #
    # Why TLE / RE / MLE / OLE are NOT in this set:
    #   * Time Limit Exceeded — flaky on judge load; LC's per-run runtime
    #     varies. Already handled by leetbot's TLE_RETRIES inside one run.
    #   * Runtime Error — can be GC-pause, NPE-on-heap-state, race in the
    #     judge harness; same code may pass on re-run.
    #   * Memory Limit Exceeded / Output Limit Exceeded — borderline solutions
    #     drift across runs (heap fragmentation, output-buffer rounding).
    #
    # Counting these as "definitive" was biasing the total LC-submission
    # tally downward: an RE attempt accrued n_runs=1 instead of REPEATS,
    # so the submission-level "we ran 3 × N codes" expectation became
    # "we ran ~N + a-few-extras" when the model failed often. Restored to
    # 3-run behaviour for every flaky verdict; WA/CE stay short-circuited
    # because re-running them genuinely is pure waste.
    _DEFINITIVE_FAILURES = frozenset({
        "Wrong Answer",
        "Compile Error",
    })
    try:
        all_runs: list[dict] = []
        for i in range(repeats):
            if i > 0:
                # Same `REPEAT_DELAY` between leetbot's --repeat=N submissions
                # of the SAME solution that the old leetbot-side --delay flag
                # used; ±20% jitter matches leetbot's own behaviour so LC's
                # anti-bot heuristic doesn't see exact multiples.
                gap = REPEAT_DELAY * (1.0 + random.uniform(-0.2, 0.2))
                time.sleep(gap)
            run_list = _real_dispatch_single(title_slug, language, path, sub_env)
            run = run_list[0] if run_list else {}
            all_runs.append(run)
            if on_run_completed is not None:
                try:
                    on_run_completed(run, i + 1)
                except Exception as e:                                 # noqa: BLE001
                    # NEVER let a callback error abort the dispatch — the
                    # callback is for progress visibility, not correctness.
                    print(f"[dispatch] on_run_completed callback raised "
                          f"{e!r} for run {i + 1}/{repeats}; continuing",
                          file=sys.stderr)
            # Short-circuit on deterministic failure (matches leetbot's
            # original --repeat=N behaviour).
            status_msg = run.get("status_msg")
            if status_msg in _DEFINITIVE_FAILURES and i + 1 < repeats:
                print(f"[dispatch] {status_msg} on run {i + 1}/{repeats} is "
                      f"deterministic — skipping remaining "
                      f"{repeats - i - 1} repeat(s)", file=sys.stderr)
                break
        return all_runs
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


def _aggregate_attempt(a: Attempt, runs: list[dict], *, partial: bool = False) -> None:
    """Fill the per-attempt avg/best columns from its N run dicts.

    `partial=True` is called from the per-run on_run_completed callback
    while the dispatch loop is mid-flight — we update the live
    metrics (n_runs / n_accepted_runs / avg_runtime_ms / avg_memory_mb /
    percentiles) so the SPA shows incremental progress (1/3 → 2/3 → 3/3),
    but we DO NOT set `final_status`. That marker is the sweep client's
    "this attempt is done" signal, so setting it mid-flight would make
    `_poll_for_attempt` return on the FIRST run and skip the remaining
    repeats. The post-loop caller passes `partial=False` to stamp the
    real verdict after the dispatch loop exits."""
    statuses = [r.get("status_msg", "Unknown") for r in runs]
    accepted = [r for r in runs if r.get("status_msg") == "Accepted"]
    a.n_runs = len(runs)
    a.n_accepted_runs = len(accepted)
    if not partial:
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
    # Runtime energy (Joules) — third perf axis. Same shape as runtime_ms.
    # Only harnesses that instrument execution report it; LeetCode never
    # populates the key, so `en` stays empty and the aggregate columns stay
    # None on LC-scored attempts. `best_runtime_energy_j` uses `min` — less
    # energy is better, matching the runtime_ms convention.
    en = [r["runtime_energy_j"] for r in accepted if r.get("runtime_energy_j") is not None]
    enp = [r["runtime_energy_percentile"] for r in accepted
           if r.get("runtime_energy_percentile") is not None]

    a.avg_runtime_ms = _mean(rt)
    a.best_runtime_ms = min(rt) if rt else None
    a.avg_memory_mb = _mean(mem)
    a.best_memory_mb = min(mem) if mem else None
    a.avg_runtime_percentile = _mean(rtp)
    a.best_runtime_percentile = max(rtp) if rtp else None
    a.avg_memory_percentile = _mean(memp)
    a.best_memory_percentile = max(memp) if memp else None
    a.avg_runtime_energy_j = _mean(en)
    a.best_runtime_energy_j = min(en) if en else None
    a.avg_runtime_energy_percentile = _mean(enp)
    a.best_runtime_energy_percentile = max(enp) if enp else None
    # Per function call solve count (B3) — mean iterations across accepted runs.
    its = [r["iterations"] for r in accepted if r.get("iterations") is not None]
    a.avg_iterations = _mean(its)


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

        # Resolve which harness this submission dispatches against BEFORE
        # any pre-flight so we can route the health check + skip the LC
        # credential pool entirely when the operator picked a remote
        # runner. Legacy submissions with no harness_id and no
        # competition-default fall through to the leetbot path unchanged.
        effective_harness = _resolve_effective_harness(session, sub)
        n_repeats = _repeats_for_harness(effective_harness)
        use_remote_runner = _is_remote_runner_harness(effective_harness)
        if use_remote_runner:
            print(f"[dispatch] sub {submission_id}: harness "
                  f"{effective_harness.slug!r} kind={effective_harness.kind} "
                  f"— dispatching to remote runner",
                  file=sys.stderr)

        # Correctness oracle resolution runs alongside harness resolution
        # so /validate can gate /measure per attempt. See
        # `_resolve_effective_correctness_oracle` for the layering rules.
        effective_correctness = _resolve_effective_correctness_oracle(session, sub)
        use_remote_validate = _is_remote_validate_oracle(effective_correctness)
        # Oracle-independent correctness gate: check correctness FIRST (via the
        # oracle's own mechanism — /validate for arena-local, one leetbot
        # submission for leetcode), then measure perf only if Accepted. Fires
        # whenever a correctness oracle is set AND perf runs on a SEPARATE
        # remote runner. When perf is leetbot itself its verdict already covers
        # correctness, so no separate gate. No correctness oracle on the
        # submission = skip the gate (the sweep's --skip-correctness omits it).
        use_correctness_gate = (
            effective_correctness is not None
            and use_remote_runner
            and (use_remote_validate or effective_correctness.kind == "leetcode")
        )
        if use_correctness_gate:
            _how = "/validate" if use_remote_validate else "leetbot submission"
            print(f"[dispatch] sub {submission_id}: correctness oracle "
                  f"{effective_correctness.slug!r} kind={effective_correctness.kind} "
                  f"— will check correctness ({_how}) before /measure per attempt",
                  file=sys.stderr)

        # Explicit-non-LC harness with missing config → hard fail, do NOT
        # silently fall through to leetbot. If the operator picked
        # `arena-local` on the submission or the competition, we must
        # honour that choice — the alternative is misrouting real
        # arena-local sweeps to LeetCode without telling anyone, which
        # is exactly the bug that made this comment necessary.
        if (real and pending and effective_harness is not None
                and effective_harness.kind in ("arena-local", "custom")
                and not use_remote_runner):
            cfg_missing = not (effective_harness.config or {}).get("endpoint")
            warn = (
                f"Harness {effective_harness.slug!r} (kind={effective_harness.kind}) "
                f"has no runnable endpoint configured. Set "
                f"`config.endpoint` on /harnesses (and `auth_token` when the "
                f"runner requires one), then re-run — the attempts are still "
                f"pending because no Run rows were written. "
            ) if cfg_missing else (
                f"Harness {effective_harness.slug!r} refused a health check. "
            )
            print(f"[dispatch] {warn}", file=sys.stderr)
            for a in pending:
                a.dispatch_warning = warn
                session.add(a)
            session.commit()
            return

        # Pre-flight: in real mode, check the harness's health ONCE before
        # burning through every pending attempt with the same failure. If
        # the runner (leetbot's LC pool OR a remote /healthz) is dead we
        # stamp a dispatch_warning on each pending attempt and SKIP
        # writing Run rows entirely — those would just pollute pass@k
        # denominators with non-verdicts. Because attempts are filtered
        # into `pending` by "no Run rows yet", the next dispatch call
        # (after credentials/runner are fixed) will pick them right back
        # up — the dispatch_warning column also acts as a retry signal
        # for the polling client.
        auth_error: str | None = None
        if real and pending and use_remote_runner:
            ok, msg = _arena.probe_healthz(effective_harness)
            if not ok:
                auth_error = msg
                print(f"[dispatch] remote runner health check failed: {msg}",
                      file=sys.stderr)
                warn = (
                    f"Runner unreachable: {msg}. Fix via the SPA's admin "
                    f"→ Harnesses page (set config.endpoint / auth_token, "
                    f"or start the runner process), then re-run the sweep "
                    f"— the attempts will be retried automatically because "
                    f"no Run rows were recorded."
                )
                for a in pending:
                    a.dispatch_warning = warn
                    session.add(a)
                session.commit()
                return
        # Correctness oracle pre-flight — same shape as the harness one.
        # If a remote validate oracle is bound and its /healthz is down,
        # bail before burning attempts. Runs BEFORE the LC auth check
        # because if a validate oracle is going to gate every attempt
        # anyway, an LC auth error would come out after the validate
        # failure and just add noise.
        if real and pending and use_remote_validate and not auth_error:
            ok, msg = _arena.probe_healthz(effective_correctness)
            if not ok:
                auth_error = msg
                print(f"[dispatch] correctness oracle health check failed: "
                      f"{msg}", file=sys.stderr)
                warn = (
                    f"Correctness oracle unreachable: {msg}. Fix on "
                    f"/oracles (config.endpoint, config.auth_token) or "
                    f"unbind it from the competition/submission, then "
                    f"re-run — no Run rows were written so attempts stay "
                    f"pending."
                )
                for a in pending:
                    a.dispatch_warning = warn
                    session.add(a)
                session.commit()
                return

        if real and pending and not use_remote_runner and not auth_error:
            ok, msg = _check_leetcode_auth()
            if not ok:
                auth_error = msg
                print(f"[dispatch] LeetCode auth check failed: {msg}",
                      file=sys.stderr)
                # Stamp the warning across every pending attempt and bail.
                # Submission status stays in `dispatching` so the SPA UI
                # shows it as still in-flight rather than mis-labelling it
                # as judged.
                warn = (
                    f"Auth failed: {msg}. Likely cause: every LeetCode "
                    f"credential in the pool is expired, revoked, or all "
                    f"accounts are within their 24h exhaustion window. Fix "
                    f"via the SPA's admin → 'LeetCode accounts' page (paste "
                    f"a fresh Cookie: header), then re-run the sweep — the "
                    f"attempts will be retried automatically because no Run "
                    f"rows were recorded."
                )
                for a in pending:
                    a.dispatch_warning = warn
                    session.add(a)
                session.commit()
                return

        # Cumulative-throttle backoff: when consecutive attempts keep
        # failing with system-level dispatch_warning (rate-limited, auth,
        # transient HTTP, …) the per-retry 8 s flat backoff inside
        # `_real_dispatch_single` alone isn't enough to break LC's per-IP
        # throttle. Every CONSECUTIVE_FAIL_THRESHOLD failing attempts we
        # insert an extra `CONSECUTIVE_FAIL_SLEEP_S` seconds before the
        # next attempt to let the throttle window genuinely clear. The
        # counter resets on the first successful attempt (real verdict
        # landed) so a single bad cell doesn't punish the rest of the
        # submission's pending set.
        CONSECUTIVE_FAIL_THRESHOLD = 3
        CONSECUTIVE_FAIL_SLEEP_S = 50.0
        consecutive_failures = 0

        # Measurement provenance accumulators (Parts 2+3): every remote run
        # reports the energy tool + machine it was measured on. A submission
        # dispatches to exactly ONE runner, so these should collapse to a
        # single tool / single machine — we assert that after the loop and
        # stamp the submission. LeetCode dispatches never populate these, so
        # LC-scored submissions keep energy_tool NULL + machine_id NULL
        # (= "unknown machine").
        runner_tools: set[str] = set()
        runner_machines: list[dict] = []
        runner_configs: list[dict] = []

        for idx, a in enumerate(pending):
            # Pause before THIS attempt if we've just crossed the
            # consecutive-fail threshold (3, 6, 9, …). Doing it before
            # the dispatch (rather than after a fail) means a short
            # burst of failures recovers without the operator waiting
            # for the next "good" attempt to trigger the backoff.
            if (consecutive_failures > 0
                    and consecutive_failures % CONSECUTIVE_FAIL_THRESHOLD == 0):
                print(f"[dispatch] sub {submission_id}: "
                      f"{consecutive_failures} consecutive dispatch failures; "
                      f"sleeping {CONSECUTIVE_FAIL_SLEEP_S:.0f}s for LC's "
                      f"per-IP throttle to clear before next attempt",
                      file=sys.stderr)
                time.sleep(CONSECUTIVE_FAIL_SLEEP_S)
            problem = session.get(Problem, a.problem_id)
            # Tracks whether this attempt represents a system-level dispatch
            # failure that should produce ZERO Run rows (vs an actual LC
            # verdict like Wrong Answer / TLE which we DO want to persist).
            dispatch_warn: str | None = None
            # When the /validate gate rejects an attempt, we jump past the
            # measure branches with `runs` already populated (a single
            # synthetic verdict Run) — this flag tells the downstream
            # post-processing that the correctness path already owns
            # the Run row so it should NOT be double-persisted, only
            # aggregated.
            validate_gate_rejected = False
            # Correctness verdict from the gate (leetbot / remote /validate) —
            # carried onto the perf run so a LeetCode-validated, arena-measured
            # attempt shows the real test-case count instead of ?/?.
            gate_verdict: dict | None = None
            try:
                if (real and use_correctness_gate and not a.prefailed_reason):
                    # Correctness gate: check correctness BEFORE spending a
                    # /measure iteration. If not Accepted we persist one
                    # Run row with the verdict (perf fields NULL) and
                    # skip the measure branch entirely — saves a lot of
                    # time on Wrong Answer / Runtime Error solutions.
                    # Oracle-independent: /validate for arena-local, a leetbot
                    # submission for leetcode (perf still comes from the perf
                    # harness afterwards).
                    vr = _correctness_gate(
                        session, sub, a.code, problem, effective_correctness,
                    )
                    if vr.get("system_failure"):
                        # Transport / auth / parse failure — treat like a
                        # leetbot outage. Stamp dispatch_warning, write
                        # zero Run rows, attempt stays pending. Setting
                        # both `dispatch_warn` and a marker flag lets us
                        # jump past every measure branch without extra
                        # control flow — the flag also keeps the outer
                        # `except Exception` from overwriting our
                        # carefully-constructed warning.
                        note = (vr.get("raw_response") or {}).get("note") or ""
                        dispatch_warn = (
                            f"{vr['status_msg']}"
                            + (f": {note}" if note else "")
                        )
                        runs = []
                        validate_gate_rejected = True   # skip measure
                        print(f"[dispatch] attempt {a.id}: /validate → "
                              f"SYSTEM FAIL ({vr['status_msg']}) — skipping "
                              f"/measure, attempt stays pending",
                              file=sys.stderr)
                    if vr.get("status_msg") != "Accepted":
                        # REAL correctness verdict = "not accepted". Persist
                        # one Run row with the verdict, perf fields NULL,
                        # correctness_oracle_id stamped. Skip /measure.
                        session.add(Run(
                            attempt_id=a.id, run_index=1,
                            status_msg=vr["status_msg"],
                            runtime_ms=None, memory_mb=None,
                            runtime_percentile=None, memory_percentile=None,
                            runtime_energy_j=None,
                            runtime_energy_percentile=None,
                            # K/N reference cases the oracle got through before
                            # the verdict (so Wrong Answer shows "45/60", not ?/?).
                            total_correct=vr.get("total_correct"),
                            total_testcases=vr.get("total_testcases"),
                            raw_response=vr.get("raw_response") or {
                                "note": "correctness oracle rejected",
                            },
                            harness_id=None,       # no /measure happened
                            correctness_oracle_id=effective_correctness.id,
                        ))
                        runs = [{
                            "status_msg": vr["status_msg"],
                            "runtime_ms": None, "memory_mb": None,
                            "runtime_percentile": None, "memory_percentile": None,
                            "runtime_energy_j": None,
                            "runtime_energy_percentile": None,
                            "total_correct": vr.get("total_correct"),
                            "total_testcases": vr.get("total_testcases"),
                            "raw_response": vr.get("raw_response"),
                            "leetcode_full_response": None,
                        }]
                        validate_gate_rejected = True
                        print(f"[dispatch] attempt {a.id}: /validate → "
                              f"{vr['status_msg']} — skipping /measure",
                              file=sys.stderr)
                    else:
                        # Accepted — remember the verdict so its test-case
                        # count + oracle attribution land on the perf run.
                        gate_verdict = vr
                        print(f"[dispatch] attempt {a.id}: /validate → "
                              f"Accepted — proceeding to /measure",
                              file=sys.stderr)

                if validate_gate_rejected:
                    # Fall through to persist-runs/aggregate below with
                    # the single-Run row we just wrote.
                    pass
                elif a.prefailed_reason:
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
                elif real and use_remote_runner:
                    # Arena-local (or a custom HTTP runner) — one POST per
                    # attempt returns all N repeats. No credential pool, no
                    # inter-submission delay (the runner box is ours).
                    _SYSTEM_FAIL = {
                        "Rate limited", "Auth failed",
                        "Dispatch timeout", "Dispatch error",
                        "Dispatch parse error",
                        "LeetCode transient error",
                    }
                    runs_so_far: list[dict] = []
                    def _persist_run_remote(result: dict, idx_1based: int) -> None:
                        runs_so_far.append(result)
                        status_msg = result.get("status_msg", "Unknown")
                        is_real_verdict = status_msg not in _SYSTEM_FAIL
                        if is_real_verdict:
                            # Capture measurement provenance (energy tool +
                            # machine) so the submission can be stamped with a
                            # single tool/machine after the loop (Parts 2+3).
                            if is_real_verdict:
                                _rp = result.get("energy_tool")
                                if _rp:
                                    runner_tools.add(_rp)
                                _rm = result.get("machine")
                                if isinstance(_rm, dict) and _rm.get("name"):
                                    runner_machines.append(_rm)
                                _rc = result.get("config")
                                if isinstance(_rc, dict):
                                    runner_configs.append(_rc)
                            session.add(Run(
                                attempt_id=a.id,
                                run_index=idx_1based,
                                submission_id_external=result.get("submission_id_external"),
                                status_msg=status_msg,
                                runtime_ms=result.get("runtime_ms"),
                                memory_mb=result.get("memory_mb"),
                                runtime_percentile=result.get("runtime_percentile"),
                                memory_percentile=result.get("memory_percentile"),
                                runtime_energy_j=result.get("runtime_energy_j"),
                                runtime_energy_percentile=result.get("runtime_energy_percentile"),
                                # Per function call metrics (Part 4) — looped curated methodology.
                                iterations=result.get("iterations"),
                                energy_j_per_op=result.get("energy_j_per_op"),
                                time_us_per_op=result.get("time_us_per_op"),
                                # Arena-local /measure reports no test-case
                                # count; fall back to the correctness gate's
                                # verdict (e.g. LeetCode 43/43) when present.
                                total_correct=(
                                    result.get("total_correct")
                                    if result.get("total_correct") is not None
                                    else (gate_verdict.get("total_correct")
                                          if gate_verdict else None)),
                                total_testcases=(
                                    result.get("total_testcases")
                                    if result.get("total_testcases") is not None
                                    else (gate_verdict.get("total_testcases")
                                          if gate_verdict else None)),
                                raw_response=result.get("raw_response") or result,
                                # Stamp the harness so /submissions/{id}
                                # aggregation can attribute runs to the
                                # oracle that produced them, matching
                                # what the import path already does.
                                harness_id=(effective_harness.id
                                            if effective_harness else None),
                                # When a separate correctness oracle is
                                # bound (e.g. arena-local /validate paired
                                # with LC /measure — hypothetical, but
                                # supported), stamp it too. When the perf
                                # harness itself owns correctness (typical
                                # arena-local), effective_correctness may
                                # be None or point at the same runner —
                                # we still stamp None here since the
                                # arena-local /measure response already
                                # carries the correctness verdict inside
                                # status_msg and we don't want to imply a
                                # separate oracle judged it.
                                correctness_oracle_id=(
                                    effective_correctness.id
                                    if (use_remote_validate or gate_verdict is not None)
                                    else None),
                            ))
                            real_so_far = [r for r in runs_so_far
                                           if r.get("status_msg") not in _SYSTEM_FAIL]
                            _aggregate_attempt(a, real_so_far, partial=True)
                            session.add(a)
                        # Same commit-with-retry pattern as the LC branch.
                        from sqlalchemy.exc import OperationalError, PendingRollbackError
                        for retry in range(3):
                            try:
                                session.commit(); break
                            except OperationalError as oe:
                                if "locked" not in str(oe).lower() or retry == 2:
                                    raise
                                session.rollback()
                                time.sleep(0.5 + 0.5 * retry)
                                break
                            except PendingRollbackError:
                                session.rollback()
                                time.sleep(0.5)
                        tail = ("verdict" if is_real_verdict
                                else f"fail (not persisted) — "
                                     f"{(result.get('raw_response') or {}).get('note') or 'no detail'}")
                        print(f"[dispatch] attempt {a.id}: "
                              f"run {idx_1based}/{n_repeats} → {status_msg} ({tail})",
                              file=sys.stderr)

                    runs = _arena.dispatch_via_arena_local(
                        problem, sub.language, a.code, n_repeats,
                        harness=effective_harness,
                        on_run_completed=_persist_run_remote,
                        meta={"submission_id": sub.id, "attempt_id": a.id},
                    )
                elif real:
                    # Per-account pacing: instead of a fixed cross-attempt
                    # sleep, pick the account whose `last_used_at +
                    # INTER_DELAY` is soonest. With N pool accounts, the
                    # effective inter-submission cap shrinks by ~N× — a
                    # 4-account pool at INTER_DELAY=120 s can submit
                    # roughly every 30 s instead of every 120 s, because
                    # every other submission goes through a sibling
                    # account that's already cooled down.
                    from . import leetcode_accounts as _lca
                    pick = _lca.pick_for_submission(min_gap_s=INTER_DELAY)
                    chosen_name: str | None = None
                    if pick is None:
                        # Pool drained mid-sweep (every account exhausted
                        # AND the pre-flight didn't catch it because the
                        # exhaustion arrived between this attempt and the
                        # previous). Same dispatch_warning treatment as
                        # the pre-flight failure path.
                        runs = _failure_runs(
                            REPEATS, "Auth failed",
                            "All LeetCode pool accounts exhausted or "
                            "missing cookies. Refresh credentials at "
                            "/admin/leetcode-accounts.",
                        )
                    else:
                        chosen, wait_s = pick
                        chosen_name = chosen.name
                        if wait_s > 0:
                            # Add the same ±20% jitter the old fixed-delay
                            # path used so a pool of fresh accounts doesn't
                            # submit at exact multiples — LC's anti-bot
                            # heuristic likes a touch of noise.
                            wait_jit = wait_s * (1.0 + random.uniform(-0.2, 0.2))
                            print(f"[dispatch] account {chosen.name!r}: "
                                  f"waiting {wait_jit:.1f}s (idx={idx})",
                                  file=sys.stderr)
                            time.sleep(wait_jit)

                        # Live-progress callback: persist each LC submission's
                        # Run row as it lands so the SPA's "runs landed"
                        # counter advances live (0/3 → 1/3 → 2/3 → 3/3)
                        # instead of waiting for all REPEATS to finish.
                        # We do a partial _aggregate_attempt after each run
                        # so n_runs / n_accepted_runs / avg_runtime_ms /
                        # avg_memory_mb update incrementally too — that way
                        # /api/submissions/<id>/attempts shows real progress
                        # numbers while the dispatch is mid-flight.
                        # Errors inside this callback are swallowed by
                        # `_real_dispatch_repeated` so a transient DB hiccup
                        # doesn't abort the dispatch.
                        _SYSTEM_FAIL = {
                            "Rate limited", "Auth failed",
                            "Dispatch timeout", "Dispatch error",
                            "Dispatch parse error",
                            "LeetCode transient error",
                        }
                        runs_so_far: list[dict] = []
                        def _persist_run(result: dict, idx_1based: int) -> None:
                            runs_so_far.append(result)
                            judged_lang = result.get("lang")
                            if judged_lang and judged_lang != sub.language:
                                result["original_status_msg"] = result.get("status_msg")
                                result["status_msg"] = "Language mismatch"
                            status_msg = result.get("status_msg", "Unknown")
                            is_real_verdict = status_msg not in _SYSTEM_FAIL
                            # Only persist a Run row for ACTUAL LC verdicts —
                            # preserves the original "all-system-failures
                            # gets dispatch_warning, no Run rows" semantics
                            # so the per-attempt counter doesn't tick on a
                            # rate-limited-and-given-up call.
                            if is_real_verdict:
                                session.add(Run(
                                    attempt_id=a.id,
                                    run_index=idx_1based,
                                    submission_id_external=result.get("submission_id"),
                                    status_msg=status_msg,
                                    runtime_ms=result.get("runtime_ms"),
                                    memory_mb=result.get("memory_mb"),
                                    runtime_percentile=result.get("runtime_percentile"),
                                    memory_percentile=result.get("memory_percentile"),
                                    total_correct=result.get("total_correct"),
                                    total_testcases=result.get("total_testcases"),
                                    raw_response=result,
                                    leetcode_full_response=result.get("leetcode_full_response"),
                                ))
                                real_so_far = [r for r in runs_so_far
                                               if r.get("status_msg") not in _SYSTEM_FAIL]
                                # partial=True: update n_runs and the
                                # avg/best metrics live, but DON'T set
                                # final_status — the sweep treats that
                                # as "dispatch is done", so we leave it
                                # null until the post-loop pass.
                                _aggregate_attempt(a, real_so_far, partial=True)
                                session.add(a)
                            # SQLite write-lock contention: with 5 parallel
                            # dispatchers all `session.commit()`-ing per LC
                            # submit, the 30 s busy_timeout occasionally
                            # blows AND the session ends up in
                            # PendingRollbackError. Without explicit
                            # recovery, the rest of the per-attempt loop
                            # silently aborts and the operator sees "2/3
                            # runs" mysteries. Retry-on-lock with
                            # rollback-then-replay handles both cases.
                            from sqlalchemy.exc import OperationalError, PendingRollbackError
                            for retry in range(3):
                                try:
                                    session.commit()
                                    break
                                except OperationalError as oe:
                                    if "locked" not in str(oe).lower() or retry == 2:
                                        raise
                                    print(f"[dispatch] attempt {a.id} run "
                                          f"{idx_1based}/{REPEATS}: SQLite "
                                          f"locked on commit; rolling back + "
                                          f"retrying ({retry + 1}/3)",
                                          file=sys.stderr)
                                    session.rollback()
                                    time.sleep(0.5 + 0.5 * retry)
                                    # Re-attach the writes that the rollback
                                    # discarded. The Run row we created is
                                    # gone from the session after rollback —
                                    # we'd need to add it again, but `a` is
                                    # also expired so we'd be working blind.
                                    # Simpler: just defer the persist to the
                                    # FINAL aggregate pass after the loop.
                                    runs_so_far[-1] = result      # keep in buffer
                                    break
                                except PendingRollbackError:
                                    session.rollback()
                                    time.sleep(0.5)
                            # Include the underlying reason on failure
                            # lines — for a real verdict we already have
                            # the status_msg, but for "Rate limited" /
                            # "Dispatch error" / etc. the reason explains
                            # *why* (e.g. "hit LeetCode rate limit after
                            # 3 retries (last seen: HTTP 429 ...)") so
                            # the operator doesn't have to cross-reference
                            # with the upstream backoff lines or pull the
                            # full dispatch_warning text from the SPA.
                            if is_real_verdict:
                                tail = "verdict"
                            else:
                                note = result.get("note") or "no detail"
                                # Trim so log stays one-liner-ish; full
                                # text remains on the synthetic Run's
                                # raw_response / on the attempt's
                                # dispatch_warning when set.
                                if len(note) > 200:
                                    note = note[:197] + "..."
                                tail = f"fail (not persisted) — {note}"
                            print(f"[dispatch] attempt {a.id}: "
                                  f"run {idx_1based}/{n_repeats} → {status_msg} ({tail})",
                                  file=sys.stderr)

                        runs = _real_dispatch_repeated(
                            problem, sub.language, a.code, n_repeats,
                            session=session, force_account=chosen.name,
                            on_run_completed=_persist_run,
                        )
                        # Mark last_used_at on the SPECIFIC account we
                        # picked — even if the submission ultimately
                        # got a system-failure status, the LC quota
                        # (or attempt at it) was spent.
                        try:
                            _lca.mark_used(chosen.name)
                        except Exception as e:                       # noqa: BLE001
                            print(f"[dispatch] failed to mark_used "
                                  f"{chosen.name!r}: {e}",
                                  file=sys.stderr)
                    # If _real_dispatch_repeated only produced SYSTEM-level
                    # failure statuses — never a real LC verdict — treat
                    # the whole attempt as a dispatch failure and record
                    # NOTHING. Stamping dispatch_warning leaves the attempt
                    # in the `pending` set (no Run rows) so the next
                    # dispatch call retries it cleanly from scratch.
                    # Real LC verdicts (Accepted, Wrong Answer, TLE, …)
                    # always coexist with valid runtime_ms / memory_mb
                    # and are NOT in this set.
                    SYSTEM_FAILURE_STATUSES = {
                        "Auth failed",
                        "LeetCode transient error",
                        "Dispatch timeout",
                        "Dispatch error",
                        "Dispatch parse error",
                        "Rate limited",
                    }
                    statuses = {r.get("status_msg") for r in runs}
                    if statuses and statuses.issubset(SYSTEM_FAILURE_STATUSES):
                        first = next(iter(statuses))
                        # The diagnostic could live in either field
                        # depending on where the failure was synthesised:
                        #   * `error_log` — set by leetbot when LC's own
                        #     judge surfaced an error message.
                        #   * `note`      — set by `_failure_runs` (used for
                        #     rate-limit retries exhausted, auth failures,
                        #     dispatch parse errors, …).
                        # Earlier this only checked `error_log`, so every
                        # _failure_runs path stamped the unhelpful
                        # "no diagnostic text" warning even when `note`
                        # had a perfectly good explanation ("hit LeetCode
                        # rate limit after 4 retries", "leetbot exited
                        # 2: <traceback>", …). Read both, prefer
                        # whichever is non-empty.
                        note = (runs[0].get("error_log") if runs else None) \
                               or (runs[0].get("note") if runs else None)
                        # Avoid the "Dispatch error: Dispatch error" tautology
                        # when neither diagnostic field had content OR the
                        # diagnostic matches the status_msg. The operator
                        # should always see something distinctive in the
                        # second half — either real diagnostic text or an
                        # explicit "no detail" marker.
                        if not note or note == first:
                            note = "no diagnostic text from leetbot subprocess"
                        dispatch_warn = f"{first}: {note}"
                else:
                    runs = _fake_dispatch_repeated(REPEATS, sub.language, a.code)
            except Exception as e:  # noqa: BLE001
                dispatch_warn = f"Dispatch error: {e}"
                runs = []

            # System-level dispatch failure → don't write Run rows; stamp
            # the warning and continue. The attempt stays in `pending` for
            # the next dispatch call. final_status stays None so the
            # leaderboard / aggregates ignore it.
            if dispatch_warn is not None:
                a.dispatch_warning = dispatch_warn
                session.add(a)
                # Track the consecutive-failure counter for the cross-
                # attempt throttle backoff (see top of this loop). A
                # system-level dispatch_warn means we never landed any
                # real LC verdict, so this attempt counts toward the
                # "LC's per-IP throttle is still hot" signal.
                consecutive_failures += 1
                continue

            # Successful dispatch (or a deliberate prefailed_reason synth) —
            # clear any stale warning from a previous run so the UI doesn't
            # keep showing "Auth failed" alongside a freshly-judged verdict.
            if a.dispatch_warning is not None:
                a.dispatch_warning = None
            # A real LC verdict landed (Accepted / Wrong Answer / TLE / …
            # OR the prefailed_reason synth, which also represents an
            # intentionally-decided outcome and not "we couldn't reach
            # LC"). Either way the throttle isn't currently blocking us
            # so we reset the counter — a single bad cell shouldn't
            # punish the rest of the submission's pending set.
            consecutive_failures = 0

            # NOTE: Run rows for the real-dispatch path were already
            # written by `_persist_run` (the on_run_completed callback)
            # one at a time as each LC submission landed. The fake-
            # dispatch and prefailed-reason paths still need a one-shot
            # write here because they don't go through the callback.
            real_path_used_callback = real and isinstance(runs, list) and runs
            if not real_path_used_callback:
                # Language-conformance guard (same as before — keep it
                # in sync with the callback's version).
                for result in runs:
                    judged_lang = result.get("lang")
                    if judged_lang and judged_lang != sub.language:
                        result["original_status_msg"] = result.get("status_msg")
                        result["status_msg"] = "Language mismatch"
                for i, result in enumerate(runs, start=1):
                    session.add(Run(
                        attempt_id=a.id,
                        run_index=i,
                        submission_id_external=result.get("submission_id"),
                        status_msg=result.get("status_msg", "Unknown"),
                        runtime_ms=result.get("runtime_ms"),
                        memory_mb=result.get("memory_mb"),
                        runtime_percentile=result.get("runtime_percentile"),
                        memory_percentile=result.get("memory_percentile"),
                        total_correct=result.get("total_correct"),
                        total_testcases=result.get("total_testcases"),
                        raw_response=result,
                        leetcode_full_response=result.get("leetcode_full_response"),
                    ))
                _aggregate_attempt(a, runs)
                session.add(a)
            else:
                # Real path: callback already wrote each Run row and
                # ran a partial aggregate. Run one final aggregate pass
                # over the COMPLETE set so any partial-only metrics
                # (e.g. avg_runtime_percentile from N-1 vs N) settle
                # to their final values.
                _aggregate_attempt(a, runs)
                session.add(a)
            # Commit after EACH attempt so the SQLite write lock is held
            # only as long as the actual flush — not the whole pending-
            # loop duration (which spans multiple subprocess.run calls,
            # each up to POLL_TIMEOUT × REPEATS seconds). With WAL +
            # busy_timeout=30s the API would otherwise see "database is
            # locked" whenever a sweep tries to POST a new attempt while
            # the dispatch worker is mid-loop.
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
        # Runtime-energy accumulators — populated only when the harness that
        # judged this attempt measured execution energy (LeetCode never does).
        avg_en, best_en, avg_enp, best_enp = [], [], [], []
        avg_it: list[float] = []       # per function call solve counts (B3)
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
            if best.avg_iterations is not None:                 avg_it.append(best.avg_iterations)
            if best.avg_runtime_energy_j is not None:            avg_en.append(best.avg_runtime_energy_j)
            if best.best_runtime_energy_j is not None:           best_en.append(best.best_runtime_energy_j)
            if best.avg_runtime_energy_percentile is not None:   avg_enp.append(best.avg_runtime_energy_percentile)
            if best.best_runtime_energy_percentile is not None:  best_enp.append(best.best_runtime_energy_percentile)

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

        # Recompute the denominator using the submission's COMPETITION scope
        # (its own problem set), not "every Problem row in the DB". Without
        # this, pass@k turns into solved/(canonical+variants) and reports
        # 45/198 for a canonical-99 sweep. Legacy submissions with
        # competition_id NULL fall back to the canonical universe.
        if sub.competition_id is not None:
            comp_pids = {
                cp.problem_id for cp in session.exec(
                    select(CompetitionProblem).where(
                        CompetitionProblem.competition_id == sub.competition_id
                    )
                ).all()
            }
            sub.total_problems = len(comp_pids)
        else:
            canonical = session.exec(
                select(Problem).where(Problem.original_problem_id.is_(None))
            ).all()
            sub.total_problems = len(canonical)
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
        # Runtime energy rollup — stays None on LC-scored submissions since
        # every accumulator is empty (LeetCode's payload has no energy key).
        sub.avg_iterations = _mean(avg_it)
        sub.avg_runtime_energy_j = _mean(avg_en)
        sub.best_runtime_energy_j = _mean(best_en)
        sub.avg_runtime_energy_percentile = _mean(avg_enp)
        sub.best_runtime_energy_percentile = _mean(best_enp)
        sub.time_human_avg = _mean(t_h_avg)
        sub.time_human_expert = _mean(t_h_exp)
        sub.mem_human_avg = _mean(m_h_avg)
        sub.mem_human_expert = _mean(m_h_exp)
        # Stamp the single energy tool + machine this submission was measured
        # on (Parts 2+3). No-op for LeetCode-scored submissions (accumulators
        # stay empty) so those keep energy_tool NULL + machine_id NULL.
        _stamp_submission_provenance(session, sub, runner_tools, runner_machines,
                                     runner_configs)
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
