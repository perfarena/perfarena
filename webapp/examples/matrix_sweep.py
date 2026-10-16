#!/usr/bin/env python3
"""Matrix sweep: submit one model attempt per (prompt × language × problem) tuple.

For each combination of:
  * a registered prompt template (--prompts NAME[,NAME] · default: all)
  * a supported language (--languages SLUG[,SLUG] · default: all 14)
  * a seeded problem (--slugs SLUG[,SLUG] · default: all in sampled_problems.json)

the script:
  1. Fetches the problem + its language-specific starter snippet.
  2. Builds the prompt by expanding the registered template's placeholders
     using the same shared helpers `submit_with_local_model.py` uses
     (--hints / --target / {{code_template}} fill correctly).
  3. Calls the configured Ollama model to generate code, runs the same
     language-conformance fingerprint check the single-shot script does
     (skips wrong-language emissions instead of poisoning the DB).
  4. Submits via PerfArena. All tuples sharing the same (prompt, language)
     cell are bundled into ONE submission via --append-if-existing semantics
     (k=1) — matches the experiment-cell taxonomy the matcher already
     enforces.
  5. Polls until the new attempt's dispatch finishes — REQUIRED, otherwise
     the next iteration starts a parallel dispatch and LeetCode's
     anti-bot heuristic fires on rapid breadth-first traffic from one IP.
  6. Records the outcome to a resumable progress log.

PACING (this is what keeps LeetCode happy)
------------------------------------------
LeetCode's anti-bot heuristic punishes BREADTH-FIRST traffic (many problems
in quick succession) but tolerates DEPTH-FIRST (same problem hit repeatedly,
which is what a human iterating on their solution looks like).

Iteration order is therefore slug (outer) → language → prompt (inner):
all prompts and languages on one problem run back-to-back, then we cross
the slow problem-boundary delay just once before the next slug. With 99
slugs and 14 langs × 3 prompts = 42 combinations per slug, the sweep
pays the long cooldown 98 times instead of 99 × 42 - 1 ≈ 4,157 times.

Three layers of delay:

  * `--inter-tuple-delay`   (default 5 s)  — between tuples that share the
    same (problem, language) and differ only in model or prompt. Short:
    LC is lenient about repeat hits on one problem, and all --models share
    one account so this keeps them coordinated on the same problem.
  * `--inter-problem-delay` (default 10 s) — when the (problem, language)
    pair changes. SHORT now: the backend's per-account picker handles
    per-account cooldowns server-side, so when the pool has 2+ accounts
    a problem hop usually rotates to a fresh credential without waiting.
    The 10 s is per-IP-throttle insurance. Bump up when the pool is
    single-account (no rotation possible — the per-account cooldown
    becomes the bottleneck again).
  * `ARENA_DISPATCH_INTER_DELAY` on the backend (default 60 s) — gap
    between attempts of different problems WITHIN one dispatch_submission
    call. Enforced server-side, additive with the above when applicable.

`--poll-seconds` is the per-tuple dispatch-wait budget (default 300 s for
LeetCode, 1800 s for arena-local CLBG, which measures far longer). The wait
covers the whole server-side dispatch: correctness validate then every
measure run. When a tuple's verdict doesn't land in this window we skip it
(logged as `poll_timeout`, auto-retried on the next sweep run by the
resume classifier — the server keeps the attempt pending and the
stale-marker / dispatcher recovery thread will eventually re-poll it
from the SPA's perspective). Set the budget shorter than the real measure
time and every slow CLBG attempt times out client-side and is re-dispatched
while the first attempt still finishes Accepted, so duplicate attempts pile
up in the system.

RESUMABILITY
------------
Progress lands in --output (default `sweep_log.json`), atomically rewritten
after every tuple. Re-running with the same output file SKIPS tuples already
marked `complete` (and `failed` unless --retry-failed). Safe to ^C and
resume.

QUOTA ESTIMATE
--------------
3 prompts × 14 languages × 99 problems × ~2 min/tuple ≈ 5–6 days continuous,
~12,500 LeetCode submissions. The credential pool we built handles rotation,
but you'll need at least ~50 pool accounts to stay under everyone's daily
caps. Use --max-tuples first to right-size.

USAGE
-----
    # Smoke-test 10 tuples to validate the matrix logic
    python matrix_sweep.py --base-url http://localhost:8000 \\
        --api-key arena_xxx --ollama-url http://localhost:11434 \\
        --model qwen2.5:7b --max-tuples 10

    # One prompt + one language only (single experimental cell)
    python matrix_sweep.py --prompts experiment-system \\
        --languages python3 --max-tuples 99

    # Full sweep of two cells across all 99 problems (~6 hours @ REPEATS=1)
    python matrix_sweep.py --prompts experiment-system,standard-code-only \\
        --languages python3 --temperature 0.2

    # Subset by slug
    python matrix_sweep.py --slugs two-sum,wildcard-matching
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

# Reuse all the codegen + submission plumbing from the single-shot script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from submit_with_local_model import (                          # noqa: E402
    LANGUAGE_DISPLAY,
    MEASURABLE_LANGUAGES,
    PROVIDERS, Provider,
    _looks_like,
    _is_clbg_statement,
    extract_compile_flags, sanitize_compile_flags,
    build_code_template_block, build_hints_block,
    build_optimization_directive,
    call_cost_usd, estimate_code_tokens,
    compute_trace_hash, fetch_prompt,
    find_existing_submission, http_json,
    make_provider, _inference_timeout,
)
# Iterative-refinement helpers — same `{{round_instruction}}` /
# `{{rounds_history}}` semantics the iterative_refine.py driver uses,
# imported here so the sweep's --rounds path is byte-for-byte compatible
# with the single-problem driver. Keeps prompts that depend on those
# placeholders rendering identically across the two workflows.
from iterative_refine import (                                  # noqa: E402
    build_round_instruction, build_rounds_history,
    distill_judge_result,
)


# ---------- helpers ---------------------------------------------------------

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _now_hms() -> str:
    """Local wall-clock HH:MM:SS for the progress logs — lets the operator see
    when each call actually happened (e.g. how long a slow local generation took
    between two lines), not just the elapsed-since-start offset."""
    return dt.datetime.now().strftime("%H:%M:%S")


def _resolve_compile_flags(arg_value: "str | None", language: str) -> str:
    """Resolve --compile-flags for one language. Accepts either a single flag
    string (applied to any language) or a per-language map
    `cpp=-O3 -std=c++17;rust=-C opt-level=3`. Map form is detected when the first
    `;`-segment's key (before `=`) is a known language slug — raw flags start
    with `-`, so `-march=native` is never misread as a map. Returns "" for a
    language absent from the map, or when unset."""
    if not arg_value:
        return ""
    segs = [s for s in arg_value.split(";") if s.strip()]
    first_key = segs[0].split("=", 1)[0].strip() if (segs and "=" in segs[0]) else ""
    if first_key in LANGUAGE_DISPLAY:      # per-language map
        for s in segs:
            k, _, v = s.partition("=")
            if k.strip() == language:
                return v.strip()
        return ""
    return arg_value.strip()               # single string for the run's language


def _tuple_key(model: str, prompt_name: str, language: str, slug: str) -> str:
    return f"{model}|{prompt_name}|{language}|{slug}"


def _cell_key(model: str, prompt_name: str, language: str) -> str:
    # One submission per experiment cell = (model, prompt, language). The
    # server's find_existing_submission matches on exactly these (+ hints /
    # target / version), so this mirrors how attempts get bundled.
    return f"{model}|{prompt_name}|{language}"


def _load_progress(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _save_progress(path: Path, progress: dict) -> None:
    """Atomic write so a Ctrl-C mid-flush doesn't corrupt the resume state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(progress, indent=2, sort_keys=True))
    tmp.replace(path)


# ---------- generated-code cache --------------------------------------------
#
# Persist the LLM-generated code per tuple so a sweep resume after a partial
# / failed dispatch DOESN'T re-pay the OpenAI call. Cache layout:
#
#     <output.parent>/sweep_cache/
#         <sha256(tuple_key)[:16]>.json
#
# Each file holds {"tuple_key": ..., "code": ..., "trace": ..., "saved_at": ...}.
# Lifecycle:
#   * SAVED after the prefetcher's `_provider.generate(...)` returns successfully.
#   * LOADED at the top of `_Prefetcher._generate` — if a hit, we skip the LLM
#     call entirely and reuse the cached code+trace.
#   * DROPPED when the main loop records `status="complete"` for a tuple
#     (i.e. LC returned a real verdict — Accepted, Wrong Answer, TLE, …).
#     The verdict is the answer; we no longer need the code.
#
# We do NOT drop on "failed" / "dispatch_failed" / "poll_timeout" — those are
# precisely the cases the operator asked us to cache for. A resume picks the
# cached code straight back up.

import hashlib

def _cache_dir(output_path: Path) -> Path:
    return output_path.parent / "sweep_cache"


def _extra_gen_knobs(args) -> dict:
    """Generation-API params beyond the always-present temperature, declared
    here ONCE. Each is included only when it deviates from the model/provider
    default, so a normal run stays byte-identical to legacy runs. Whatever this
    returns flows to two places automatically:

      * the code-cache salt (`_cache_salt`) — a different value gets its own
        cache file, so it never reuses a mismatched generation;
      * the submission `model_params` — the run becomes a distinct, traceable
        submission.

    To add a new knob (top_p, top_k, seed, …) wire it to both places at once by
    adding a single entry here."""
    knobs: dict = {}
    if getattr(args, "no_think", False):
        knobs["think"] = False
    return knobs


def _cache_salt(args) -> str:
    """Generation-affecting knobs that are NOT part of tuple_key
    (model|prompt_name|language|slug) but DO change the produced code. Folded
    into the cache FILENAME hash so a run with different knobs never reuses a
    mismatched generation. Kept out of the human-readable tuple_key so the
    progress log stays legible."""
    parts = [
        f"target={getattr(args, 'target', None) or ''}",
        f"hints={int(bool(getattr(args, 'hints', False)))}",
        f"temp={getattr(args, 'temperature', None)}",
        f"rounds={getattr(args, 'rounds', None)}",
    ]
    # Extra generation knobs (think, and any future top_p / seed / …). Appended
    # only when non-default, so a normal run's filename matches legacy runs.
    for k, v in _extra_gen_knobs(args).items():
        parts.append(f"{k}={v}")
    return "|".join(parts)


def _cache_key_filename(tuple_key: str, salt: str = "") -> str:
    """Stable 16-hex-char filename for a tuple_key plus a generation-knob salt
    (see _cache_salt). Collision risk in the 16-char prefix is negligible at any
    realistic sweep size."""
    h = hashlib.sha256((tuple_key + "\x1f" + salt).encode("utf-8")).hexdigest()
    return f"{h[:16]}.json"


def _save_cached_code(output_path: Path, tuple_key: str,
                      code: str, trace: dict, salt: str = "") -> None:
    """Persist the freshly-generated code for a tuple so the next resume
    can skip the LLM call. Atomic via tmp + rename. Silent on failure —
    a cache write loss costs an LLM call but never blocks the sweep."""
    cdir = _cache_dir(output_path)
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        path = cdir / _cache_key_filename(tuple_key, salt)
        payload = {
            "tuple_key": tuple_key,
            "code": code,
            "trace": trace,
            "saved_at": _now_iso(),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(path)
    except OSError as e:
        print(f"  ⚠ cache save failed for {tuple_key!r}: {e}",
              file=sys.stderr)


def _load_cached_code(output_path: Path,
                      tuple_key: str, salt: str = "") -> tuple[str, dict] | None:
    """Return (code, trace) from the cache for this tuple_key, or None
    when no usable cache entry exists. A malformed cache file is treated
    as a miss — we never want a bad cache to block a sweep."""
    cdir = _cache_dir(output_path)
    path = cdir / _cache_key_filename(tuple_key, salt)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        code = payload.get("code")
        trace = payload.get("trace") or {}
        if not isinstance(code, str) or not code.strip():
            return None
        return code, trace
    except (OSError, json.JSONDecodeError):
        return None


def _drop_cached_code(output_path: Path, tuple_key: str, salt: str = "") -> None:
    """Delete the cached code for a tuple — called when a verdict has
    landed and the code is no longer needed."""
    cdir = _cache_dir(output_path)
    path = cdir / _cache_key_filename(tuple_key, salt)
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        print(f"  ⚠ cache delete failed for {tuple_key!r}: {e}",
              file=sys.stderr)


def _rejected_log_path(output_path: Path) -> Path:
    """Append-only log of generations that were NOT cached as a usable
    attempt (wrong-language or empty output). Sibling of sweep_log.json and
    the sweep_cache/ dir, deliberately OUTSIDE the success cache so the
    resumable code cache and --submit-from-cache never read it."""
    return output_path.parent / "generate_rejected.jsonl"


def _record_rejected_generation(output_path: Path, tuple_key: str,
                                model: str, prompt_name: str,
                                language: str, slug: str, status: str,
                                code: str | None, trace: dict | None,
                                seconds: float) -> None:
    """Persist a rejected generation (wrong-language / 0-char) for later
    inspection. These are expensive failures — a long generation that yielded
    nothing usable — and they are otherwise dropped, especially in
    --generate-only mode which never submits. We keep the model's full
    `raw_response` (and `reasoning` when a provider returns it separately) so
    the operator can see WHY the output was unusable. Appended as one JSON line
    so the full retry history survives. Never caches the code (so the
    "re-generate next resume" behavior is untouched), and never raises — a log
    failure must not break the sweep."""
    trace = trace or {}
    record = {
        "rejected_at": _now_iso(),
        "tuple_key": tuple_key,
        "model": model,
        "prompt": prompt_name,
        "language": language,
        "slug": slug,
        "status": status,
        "chars": len(code or ""),
        "seconds": round(seconds, 1),
        "extracted_code": code,
        "reasoning": trace.get("reasoning"),
        "raw_response": trace.get("raw_response"),
    }
    try:
        path = _rejected_log_path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"  ⚠ rejected-generation log failed for {tuple_key!r}: {e}",
              file=sys.stderr)


def _reconcile_progress(progress: dict, base: str, api_key: str | None,
                        deep: bool = False) -> dict:
    """Drop progress entries whose submission was deleted server-side.

    The progress log is a CLIENT-only record of what we believe completed
    (so resumability survives server outages). But if the user deletes a
    submission in the SPA, the log still claims those tuples are done —
    next run silently skips them. Reconcile by probing each unique
    submission_id against `GET /api/submissions/{id}`:

      * 200  → submission still exists; keep its entries (and, optionally,
                drop entries whose slug isn't covered by any of its
                attempts — handles the per-attempt delete case too).
      * 404  → submission gone; drop ALL its entries from the progress log.
      * other → network blip or 5xx: warn and keep the entry (better to
                under-skip than to silently re-process when the server is
                healthy but just glitchy this second).

    Idempotent: returns a NEW dict and the caller decides whether to
    write it back. No-op when the log has no submission_ids yet.
    """
    # Unique submission ids referenced by the log. Entries without a
    # submission_id (failed before submit) are passed through untouched —
    # nothing to reconcile against.
    ids = sorted({rec.get("submission_id") for rec in progress.values()
                  if rec.get("submission_id")})
    if not ids:
        return progress

    print(f"  reconciling progress log against {len(ids)} submission(s)…")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    alive: dict[int, set[str] | None] = {}  # sub_id → set of slugs still attempted
    gone: set[int] = set()

    # ONE bulk call for the live submission-id set instead of N per-id GETs.
    # GET /api/submissions returns only non-deleted submissions, so any id in
    # our log that's absent from it was deleted server-side. This handles the
    # common case (whole-submission deletion) in a single request.
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}/api/submissions",
                                     headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=60) as r:
            live = json.loads(r.read() or "[]")
        live_ids = {s.get("id") for s in live if isinstance(s, dict)}
        gone = {sid for sid in ids if sid not in live_ids}
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"  WARN: bulk GET /api/submissions → {e}; keeping ALL progress "
              f"entries (skipping reconcile this run)", file=sys.stderr)
        return progress

    # Per-attempt (slug-coverage) pruning needs each submission's runs, so it
    # costs one GET per still-alive submission — opt-in via --deep-reconcile-
    # progress. Off by default: the bulk check above already covers deletion of
    # whole submissions, which is what a server-side purge does.
    if deep:
        for sid in (i for i in ids if i not in gone):
            url = f"{base.rstrip('/')}/api/submissions/{sid}"
            try:
                with urllib.request.urlopen(
                        urllib.request.Request(url, headers=headers, method="GET"),
                        timeout=15) as r:
                    data = json.loads(r.read() or "{}")
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                print(f"  WARN: GET /submissions/{sid} → {e}; keeping its "
                      f"entries", file=sys.stderr)
                alive[sid] = None      # sentinel: unknown coverage, skip pruning
                continue
            runs_in = data.get("runs") or []
            alive[sid] = {r.get("problem_slug")
                          for r in runs_in if r.get("problem_slug")}

    if not gone and not alive:
        print(f"    nothing to prune (all {len(ids)} submissions still present)")
        return progress

    pruned: dict = {}
    dropped_by_reason: dict[str, int] = {"submission_gone": 0, "attempt_gone": 0}
    for k, rec in progress.items():
        sid = rec.get("submission_id")
        # Reconciliation only acts on "complete" entries — those are
        # the ones that BLOCK the next sweep run from retrying. A
        # status="failed"/"skipped"/"dispatch_failed"/"poll_timeout"
        # entry is already non-blocking: the resume filter decides
        # whether to retry based on the failure reason, and the only
        # thing reconciliation could do here is delete the breadcrumb
        # that lets the classifier make that decision.
        #
        # The "todo placeholder" submit-rejection bug surfaced this:
        # an HTTP 400 from /api/submissions/{id}/attempts returned no
        # Run row server-side (because the attempt was never created),
        # so the slug-coverage check below decided the attempt had
        # been deleted and dropped the entry. On the next sweep run
        # the resume filter had nothing to skip, the tuple re-entered
        # todo, the same code was regenerated and the same 400 came
        # back, ad infinitum.
        status = rec.get("status")
        if status != "complete":
            pruned[k] = rec
            continue
        if sid in gone:
            dropped_by_reason["submission_gone"] += 1
            continue
        covered = alive.get(sid)
        if covered is not None and isinstance(covered, set):
            slug = k.rsplit("|", 1)[-1]    # _tuple_key: "model|prompt|lang|slug"
            if slug not in covered:
                dropped_by_reason["attempt_gone"] += 1
                continue
        pruned[k] = rec

    total_dropped = sum(dropped_by_reason.values())
    print(f"    dropped {total_dropped} stale entry/entries  "
          f"(submission deleted: {dropped_by_reason['submission_gone']}; "
          f"attempt deleted: {dropped_by_reason['attempt_gone']})")
    if gone:
        print(f"    gone submission_ids: {sorted(gone)}")
    return pruned


def _generation_failure_verdict(err: str | None) -> str | None:
    """Verdict to RECORD for a failed generation, or None to leave it for a
    retry. A timeout (the hard cap or the provider's read timeout) is the
    model failing to answer in time: `generation_timeout`. An infrastructure
    error (rate limit, gateway, dropped connection, tunnel) is not the model's
    fault, so it stays a retryable `failed` tuple. Any other error is
    `generation_error`. A recorded failure is a failed attempt of the model,
    which pass@1 counts, instead of a missing cell."""
    r = (err or "").lower()
    if "gateway timeout" in r:
        return None
    if "timed out" in r or "timeout" in r:
        return "generation_timeout"
    infra = ("http 408", "http 425", "http 429", "http 431", "http 5",
             "gateway", "connection reset", "connection refused",
             "connection error", "remote end closed", "remotedisconnected",
             "incompleteread", "broken pipe", "eof occurred", "ssl:", "ngrok",
             "service unavailable", "rate limit", "too many requests",
             "request headers are too large", "temporarily unavailable")
    if any(s in r for s in infra):
        return None
    return "generation_error"


def _is_transient_generator_failure(reason: str | None) -> bool:
    """Classify a `failed` entry's reason text as transient (auto-retry-able)
    vs permanent (stuck unless the operator passes --retry-failed).

    Transient: anything that smells like an HTTP gateway / capacity /
    network hiccup. We saw these in the wild:
      * "ollama: [openai] OpenAI HTTP 431: Request headers are too large"
        — happens when the prompt + few-shot context bloats over the
        limit on one call but is fine on the next.
      * "ollama: Ollama HTTP 503: ngrok gateway error" / "404 endpoint
        offline" — the ngrok tunnel flapped.
      * "ollama: Ollama HTTP 429" — provider-side rate limit.
      * connection-reset / timeout — generic network flake.

    Permanent: deterministic generator errors that won't get better on
    retry (the matrix_sweep already records these as a different status
    where possible, so this branch is mostly defensive):
      * "ollama: model not found" — the model name is wrong.
      * "wrong_language_extracted" — recorded as a different status.

    Heuristic: HTTP 4xx (except 401/403/404-missing-model) and 5xx are
    transient; everything else falls through to permanent. We err on the
    side of treating things as transient — re-running a permanent failure
    just wastes one provider call, but stranding a transient one costs
    the operator a hole in their submission matrix (the original bug
    that prompted this classifier)."""
    if not reason:
        return False
    r = reason.lower()
    # Provider transport classes — match a few canonical phrasings.
    transient_substrings = (
        "http 408", "http 429", "http 431", "http 425",
        "http 500", "http 502", "http 503", "http 504", "http 5",
        "gateway error", "gateway timeout",
        "connection reset", "connection refused", "connection error",
        # TLS / keep-alive mid-stream death. urllib3's `RemoteDisconnected`,
        # http.client's `IncompleteRead`, and the bare-string form that
        # urllib raises when an ELB drops a long-lived connection. These
        # are textbook transient (the next call lands on a different
        # backend and works) — flagging them as permanent stranded ~9
        # tuples per sweep for the operator and was the root cause of
        # `submission#79 stuck at 90/99 attempts`.
        "remote end closed connection",
        "remotedisconnected",
        "incompleteread",
        "broken pipe", "eof occurred",
        "ssl: bad_record_mac",                # transient TLS desync
        "timeout", "timed out", "temporarily unavailable",
        "ngrok",                           # err_ngrok_* family is always transient
        "request headers are too large",   # 431 verbose form
        "service unavailable",
        "rate limit",
        "too many requests",
    )
    if any(s in r for s in transient_substrings):
        return True
    # Reasoning-SKU temperature error: HTTP 400 with the OpenAI message
    # "temperature does not support … Only the default (1) value is
    # supported." used to be a permanent-failure trap because the
    # operator was passing `--temperature 0.2` to a gpt-5* / o-series
    # model. submit_with_local_model.py now strips the temperature
    # param automatically for those SKUs, so a re-run will succeed
    # cleanly — treat it as transient so the next sweep run auto-picks
    # up the 297 tuples that previously got marked failed under this
    # exact error without the operator needing `--retry-failed`.
    if "temperature" in r and "only the default" in r:
        return True
    # 4xx that *we know* are caller-permanent (bad model name, auth dead)
    # leak through as non-transient. Don't add 401/403 to the transient
    # list; those need operator intervention, not a retry.
    return False


def _should_skip(progress: dict, key: str, retry_failed: bool,
                 *,
                 current_competition: str | None = None,
                 sub_competition_by_id: dict[int, str | None] | None = None) -> bool:
    """Resume-filter predicate. Decides whether a tuple key can be safely
    skipped because progress.json already records a terminal outcome for it.

    Competition-awareness:
      The tuple key is (model, prompt, lang, slug) — it does NOT include
      the competition slug, so the same (lang, slug) attempted under two
      different competitions shares one progress entry. When the existing
      `complete` entry references a submission that belongs to a
      DIFFERENT competition than the one we're running now, treating it
      as "done" would silently skip a legitimately new attempt — the
      exact bug that left subs 77/78/79 at 90/99 instead of 99/99 after
      a normal-99 sweep landed first. So when `current_competition` is
      provided AND the recorded submission_id maps to a different
      competition, we return False so the tuple re-enters the todo list.

    `sub_competition_by_id` is a small cache the caller builds from
    `/api/me/submissions` once per sweep — pass None to fall back to
    the legacy competition-agnostic behaviour."""
    rec = progress.get(key)
    if rec is None:
        return False
    status = rec.get("status")
    if status == "complete":
        # Competition mismatch → re-attempt under the right competition.
        # An entry with no submission_id (rare; legacy records) falls
        # through to the unconditional-skip branch to preserve old
        # behaviour for ambiguous data.
        if current_competition is not None and sub_competition_by_id is not None:
            rec_sid = rec.get("submission_id")
            if rec_sid is not None:
                rec_comp = sub_competition_by_id.get(int(rec_sid), "<unknown>")
                # "<unknown>" means we couldn't find the submission on the
                # server (deleted, or different user). Skip honoured to
                # preserve legacy behaviour — _reconcile_progress is the
                # right place to drop those stale entries.
                if rec_comp not in ("<unknown>", current_competition, None):
                    return False
        return True
    if status == "skipped":
        return True       # skipped reasons are permanent (no snippet, etc.)
    if status == "failed":
        # Look at the failure CLASS, not just the status word. Transient
        # generator failures (HTTP 5xx, 429, 431, ngrok flap, …) should
        # auto-retry on the next sweep run — they were the cause of the
        # original "submission 49 has 98/99 attempts" bug, where one
        # OpenAI 431 left a permanent hole until the operator noticed.
        # Permanent generator failures (wrong model name, wrong language
        # extracted, …) still require --retry-failed.
        reason = rec.get("reason") or ""
        if _is_transient_generator_failure(reason):
            return False
        # Legacy: older versions of this script recorded dispatch failures
        # (LC auth dead, dispatch worker stall) under status="failed" with
        # a reason starting "dispatch_warning:". The current code path
        # uses status="dispatch_failed" for the same situation, and we
        # auto-retry that. Treat the legacy form the same — otherwise an
        # operator who fixed their credential pool would still see holes
        # from the broken-pool era until they remembered --retry-failed.
        if reason.startswith("dispatch_warning:"):
            return False
        # "submit (round N):" prefix → HTTP failure when POSTing the
        # attempt to the platform. RECOVERABLE only when the status code
        # is 5xx or 429 (server flake / rate limit). 4xx other than 429
        # — including the validator's "code contains a placeholder marker
        # ('todo')" 400 rejection — is a DETERMINISTIC rejection: the
        # same code submitted again will get the same 400. Auto-retrying
        # those just burns provider calls every sweep restart, which is
        # exactly the loop the operator hit.
        if reason.startswith("submit (round"):
            recoverable_codes = ("http 500", "http 502", "http 503",
                                 "http 504", "http 429", "http 408")
            r_low = reason.lower()
            if any(code in r_low for code in recoverable_codes):
                return False
            # 4xx (other than 429): stuck unless the operator passes
            # --retry-failed (e.g. they've fixed the prompt and want the
            # model to retry the cell with a clean state).
        return not retry_failed
    if status in ("dispatch_failed", "poll_timeout"):
        # SYSTEM-level failures we know are recoverable: the credential pool
        # was dead, LeetCode hiccupped, the dispatch worker stalled. The
        # backend left these attempts in `pending` (no Run rows, dispatch
        # warning set) — so the next sweep run can re-submit and let
        # dispatch retry from scratch. Auto-retried so the operator
        # doesn't have to remember --retry-failed for these cases.
        return False
    return False          # in-progress / unknown → retry


def _is_pool_exhaustion_warning(warn: str) -> bool:
    """Heuristic: does this dispatch_warning indicate the LC credential
    pool is dry (rather than e.g. a transient HTTP 5xx)? The server-side
    warning text we stamped includes "Auth failed:" when the pre-flight
    whoami fails, which is the symptom of every account being either
    expired or within its cooldown."""
    if not warn:
        return False
    needle = warn.lower()
    return (
        "auth failed" in needle
        or "exhaust" in needle
        or "all pool accounts" in needle
        or "leetcode session expired" in needle
    )


# One-shot wait (seconds) when the perf harness / correctness oracle host is
# unreachable, before re-dispatching the same attempt. The harness is a single
# bare-metal box, and a transient outage (a restart, a network blip, the
# operator fixing config on the SPA) usually clears within a couple of minutes.
# We pause once and retry rather than immediately burning the tuple as
# dispatch_failed, which would waste the generation cost already paid.
_HARNESS_DOWN_RETRY_WAIT_S = 180


def _is_harness_unreachable_warning(warn: str) -> bool:
    """Heuristic: does this dispatch_warning mean the perf harness / correctness
    oracle host was DOWN (unreachable, timed out, connection refused), rather
    than a per-attempt failure? The server stamps text such as "Runner
    unreachable: transport error hitting http://…/healthz: timed out" when it
    cannot contact the runner at all."""
    if not warn:
        return False
    needle = warn.lower()
    return (
        "runner unreachable" in needle
        or "harness unreachable" in needle
        or "oracle unreachable" in needle
        or "/healthz" in needle
        or "connection refused" in needle
        or "transport error" in needle
    )


def _redispatch_and_repoll(base: str, api_key: str, sub_id: int, slug: str,
                           round_idx: int, poll_seconds: int) -> dict | None:
    """Ask the server to re-dispatch the submission's pending attempts, then
    re-poll for the same round. Returns the retried attempt dict, or None on
    poll timeout. Shared by the inline-recovery branches (pool exhaustion,
    harness unreachable)."""
    try:
        http_json("POST", f"{base}/api/admin/submissions/{sub_id}/dispatch",
                  token=api_key, body={})
    except SystemExit as e:
        print(f"  !! couldn't trigger re-dispatch: {e}")
    return _poll_for_attempt(base, api_key, sub_id, slug,
                             round_attempt_idx=round_idx, timeout_s=poll_seconds)


def _wait_for_pool_or_fail(base: str, api_key: str, *, max_wait_s: int = 8 * 3600,
                           ) -> str:
    """Inspect the LC credential pool. Return one of:

      * "available"  — at least one account is currently available; caller
                       can immediately trigger a re-dispatch.
      * "waited"     — pool was dry but had a rollover time; we slept
                       until then + a 60 s buffer. Caller should re-dispatch.
      * "empty"      — pool has 0 accounts. No rollover possible. Caller
                       must abort (or wait for operator intervention).
      * "unknown"    — couldn't reach the admin endpoint (not admin, server
                       down, etc.). Caller falls back to old behaviour.

    `max_wait_s` caps the inline sleep so we don't park the sweep for an
    arbitrary amount of time on a misconfigured pool.
    """
    try:
        pool = http_json("GET", f"{base}/api/admin/leetcode-accounts",
                         token=api_key)
    except SystemExit:
        return "unknown"
    if not isinstance(pool, dict):
        return "unknown"
    total = pool.get("total", 0)
    available = pool.get("available", 0)
    if total == 0:
        print(f"  ✗ LeetCode credential pool is EMPTY. No accounts at all — "
              f"add one at /admin/leetcode-accounts before resuming the sweep.")
        return "empty"
    if available > 0:
        # Race: the warning was stamped while the pool was dry, but by the
        # time we asked, an account had rolled over. Just retry.
        return "available"
    earliest = pool.get("earliest_available_after")
    if not earliest:
        print(f"  ✗ pool is dry and the server reports no rollover time. "
              f"Likely cause: every cookie is expired/invalid (not "
              f"exhausted-cooldown). Refresh at /admin/leetcode-accounts.")
        return "empty"
    # Parse the ISO timestamp and sleep until then + a buffer. dt.fromisoformat
    # handles the server's UTC-suffixed shape ("…+00:00").
    try:
        earliest_dt = dt.datetime.fromisoformat(earliest)
    except ValueError:
        print(f"  ✗ couldn't parse earliest_available_after={earliest!r}; "
              f"treating as empty pool.")
        return "empty"
    now = dt.datetime.now(dt.timezone.utc)
    if earliest_dt.tzinfo is None:
        earliest_dt = earliest_dt.replace(tzinfo=dt.timezone.utc)
    wait_s = max(0, (earliest_dt - now).total_seconds()) + 60
    if wait_s > max_wait_s:
        print(f"  ✗ pool would only recover in {wait_s/3600:.1f}h — exceeds "
              f"max_wait_s={max_wait_s}s. Add more accounts or wait it out.")
        return "empty"
    mins = wait_s / 60
    print(f"  🛌 LeetCode pool exhausted ({pool['exhausted']}/{total} accounts in "
          f"cooldown). Earliest rollover at {earliest_dt.isoformat()} — "
          f"sleeping ~{mins:.1f} min before retry.")
    # Sleep in chunks so a ^C interrupt lands promptly. Print a heartbeat
    # every ~5 min so the user knows the sweep is still alive.
    target = time.time() + wait_s
    last_heartbeat = time.time()
    while time.time() < target:
        chunk = min(30, target - time.time())
        if chunk <= 0:
            break
        time.sleep(chunk)
        if time.time() - last_heartbeat > 300:
            remaining_m = (target - time.time()) / 60
            print(f"     … still waiting on credential pool "
                  f"(~{remaining_m:.1f} min left)")
            last_heartbeat = time.time()
    print(f"  ✓ pool wait done; re-dispatching the failed attempt server-side.")
    return "waited"


def _pacing_wait_until_account_ready(
    base: str, api_key: str, *,
    floor_seconds: float, ceiling_seconds: float,
) -> tuple[float, str]:
    """Return (seconds_to_sleep, reason) for the next submit.

    Reads the LC account pool's current state from the server, finds the
    LRU account that the dispatcher will pick (`last_used_at + min_gap_s`
    soonest), and computes how long the client should sleep so that
    account is ready by the time the server picks it.

    Why client-side: when the wait is sub-second, this lets us proceed
    immediately. When it's nonzero (e.g. 2 accounts in cooldown, both at
    minute 0 and minute 1 since last use), we sleep here rather than
    queuing the submit at the server with the dispatch worker blocked
    on `time.sleep(wait_s)` inside the picker — keeping the wall-clock
    pacing visible to the operator AND avoiding multiple sweep tuples
    piling up server-side under one big lock.

    Bounds:
      * `floor_seconds` — IP-throttle insurance. We always sleep at least
        this much, even when the pool is fully cool. Mirrors the old
        `--inter-problem-delay` job for the multi-account case.
      * `ceiling_seconds` — safety cap so a misconfigured / single-stuck-
        account pool doesn't park the sweep forever. If the computed
        wait exceeds this, we just sleep the ceiling and let the
        server's own pool-exhaustion handler take over.

    Returns (seconds, reason_str). `reason_str` is a short human-readable
    explanation that the caller prints in the "  … sleeping …" line.
    Gracefully degrades to `(floor_seconds, "pool unreachable; floor")`
    when the admin endpoint is unavailable or the response is malformed
    — that keeps the sweep alive on a permission-misconfigured run.
    """
    # Pool-fully-exhausted sleep: when EVERY account is in cooldown the
    # next submit will burn one provider call to land on a guaranteed
    # dispatch_warning. Cheaper to wait 5 min and re-poll — by then at
    # least one account has usually rolled over (LC's per-account cap is
    # ~30 min by default, but the rollover times spread across the pool
    # so an aligned exhaustion clears in waves). We loop with a hard
    # ceiling of 6 attempts (30 min total) so a genuinely-dead pool
    # eventually surfaces upstream instead of parking the sweep forever.
    POOL_RECHECK_INTERVAL_S = 300
    POOL_RECHECK_MAX_ATTEMPTS = 6
    pool: dict | None = None
    for poll_attempt in range(POOL_RECHECK_MAX_ATTEMPTS):
        try:
            pool = http_json("GET", f"{base}/api/admin/leetcode-accounts",
                             token=api_key)
        except SystemExit:
            return floor_seconds, "pool unreachable; floor"
        if not isinstance(pool, dict):
            return floor_seconds, "pool unreachable; floor"
        accounts = pool.get("accounts") or []
        if not accounts:
            # Pool has zero accounts at all — operator hasn't seeded any.
            # No amount of waiting will help; let downstream handle it.
            break
        exhausted_now = [a for a in accounts if a.get("is_exhausted")]
        if len(exhausted_now) < len(accounts):
            break                            # at least one is usable; carry on
        # ALL accounts exhausted. Print once on entry + on each recheck so
        # the operator sees the wait is happening.
        if poll_attempt == 0:
            print(f"  ⏸ all {len(accounts)} LeetCode account(s) exhausted "
                  f"({', '.join(a.get('name', '?') for a in exhausted_now[:5])}"
                  f"{'…' if len(exhausted_now) > 5 else ''}); "
                  f"sleeping {POOL_RECHECK_INTERVAL_S}s and re-checking",
                  flush=True)
        else:
            print(f"     … still all exhausted on recheck {poll_attempt}; "
                  f"sleeping another {POOL_RECHECK_INTERVAL_S}s",
                  flush=True)
        time.sleep(POOL_RECHECK_INTERVAL_S)
    else:
        # Loop completed all attempts without finding a usable account.
        # Hand the situation off as a long wait — `_wait_for_pool_or_fail`
        # owns the "wait until earliest_available_after" path with a
        # multi-hour ceiling. Surface this as a long but bounded sleep
        # so the caller's sleep print line shows we're parked here.
        return ceiling_seconds, (
            f"pool exhausted for {POOL_RECHECK_MAX_ATTEMPTS} consecutive "
            f"checks ({POOL_RECHECK_INTERVAL_S * POOL_RECHECK_MAX_ATTEMPTS}s); "
            f"deferring to server-side recovery"
        )
    assert pool is not None
    min_gap_s = float(pool.get("min_gap_s") or 0.0)
    if min_gap_s <= 0:
        # Older backend that doesn't surface min_gap_s — fall back to the
        # IP-throttle floor and let the server pace per-account itself.
        return floor_seconds, f"server min_gap_s=0; floor"
    now = dt.datetime.now(dt.timezone.utc)
    # Of all NOT-exhausted accounts, compute when each will be ready for
    # its next dispatch. The picker on the server picks min(ready_at),
    # so the wall-clock wait is min over the pool.
    candidates: list[tuple[float, str]] = []
    for acc in pool.get("accounts") or []:
        if acc.get("is_exhausted"):
            continue
        last = acc.get("last_used_at")
        name = acc.get("name") or "?"
        if not last:
            # Never used — picker will return this with wait=0.
            candidates.append((0.0, name))
            continue
        try:
            t = dt.datetime.fromisoformat(last)
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            candidates.append((0.0, name))    # parse failure: assume ready
            continue
        ready_at = t + dt.timedelta(seconds=min_gap_s)
        candidates.append((max(0.0, (ready_at - now).total_seconds()), name))
    if not candidates:
        # Pool empty / all exhausted. Server's pool-exhaustion handler will
        # catch this on the actual dispatch; we just keep the floor here.
        return floor_seconds, "no ready accounts; floor"
    candidates.sort()
    wait_raw, picked = candidates[0]
    # Below the IP floor: just sleep the floor.
    if wait_raw <= floor_seconds:
        if wait_raw == 0:
            return floor_seconds, f"picker→{picked} ready; IP-floor only"
        return floor_seconds, (
            f"picker→{picked} ready in {wait_raw:.0f}s; IP-floor governs"
        )
    # Honest pacing — the picker's chosen account needs more than the
    # floor. Cap at ceiling so a wedged pool doesn't sink the sweep.
    if wait_raw > ceiling_seconds:
        return ceiling_seconds, (
            f"picker→{picked} needs {wait_raw:.0f}s (capped at "
            f"ceiling {ceiling_seconds:.0f}s)"
        )
    return wait_raw, f"picker→{picked} cooldown {wait_raw:.0f}s"


def _poll_for_attempt(base: str, api_key: str, sub_id: int, slug: str,
                      round_attempt_idx: int, timeout_s: int) -> dict | None:
    """Block until the round-`round_attempt_idx` attempt for `slug` in
    submission `sub_id` has its dispatch fully settled. Returns the
    attempt dict or None on timeout.

    "Fully settled" means one of:
      * `final_status` is non-null — the dispatcher's post-loop aggregate
        ran, meaning all REPEATS landed (or short-circuited on a
        deterministic failure verdict like Wrong Answer / Compile Error
        which makes further repeats pointless). The dispatcher only
        writes `final_status` AFTER the per-repeat loop exits, so
        seeing this is the correct "I'm done" signal — `n_runs > 0`
        alone is not, because the dispatcher's per-run progress
        callback ticks n_runs after EACH LC submit (so the SPA's
        1/3 → 2/3 → 3/3 counter can advance live).
      * `dispatch_warning` is set — the dispatcher gave up before any
        verdict could land (auth dead, LC frontend persistently 5xx,
        every retry got 429'd, …). No point waiting further; the
        caller will print the warning and treat the attempt as failed.

    `round_attempt_idx` must match the Attempt.round_index assigned by the
    server. The server may judge round R-1 before round R gets dispatched,
    so we explicitly filter on round_index instead of just "the most recent
    judged attempt for the slug" — otherwise a fast prior round could be
    returned while the later round is still queueing."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        atts = http_json("GET", f"{base}/api/submissions/{sub_id}/attempts",
                         token=api_key)
        same_round = [
            a for a in atts
            if a["problem_slug"] == slug
            and (a.get("round_index") or 0) == round_attempt_idx
        ]
        if same_round:
            # If multiple appended attempts share a round_index (shouldn't
            # happen but defends against client races), pick the freshest.
            best = max(same_round, key=lambda a: a["attempt_id"])
            # `status` on the API response mirrors `attempt.final_status`
            # (or "queued"/"Pending" when not yet set / no runs yet). The
            # dispatcher only stamps final_status AFTER all REPEATS land
            # (or short-circuit on deterministic failure), so a non-
            # transient status here means "dispatch is fully done".
            status = best.get("status")
            if status and status not in ("queued", "Pending", "Dispatch failed"):
                return best
            # Server-side dispatch decided not to record anything (system
            # error, not a verdict). Surface it immediately — the caller
            # will print the warning and treat the attempt as failed.
            # "Dispatch failed" is the SPA's label for an attempt with
            # `dispatch_warning` set + n_runs=0, so we check both.
            if best.get("dispatch_warning") or status == "Dispatch failed":
                return best
        time.sleep(5)
    return None


def _find_inflight_attempt(base: str, api_key: str, sub_id: int, slug: str,
                           round_idx: int) -> dict | None:
    """Return the server's still-in-flight attempt for (sub_id, slug, round_idx),
    or None.

    Makes retries idempotent. A slow CLBG measure can still be running
    server-side after a prior run's `_poll_for_attempt` gave up at
    `--poll-seconds`; re-submitting the tuple then creates a DUPLICATE attempt
    (the "poll_timeout -> re-dispatch -> duplicates piled up even though the first
    attempt finished Accepted" failure documented in main()). Before submitting,
    the caller asks this whether an attempt is already in flight and, if so,
    resumes polling it instead of creating a second one.

    "In flight" means the attempt exists, has no terminal verdict yet (status
    `queued`/`Pending`), was NOT prefailed by the client, and the dispatcher has
    NOT given up (no `dispatch_warning`). A prefailed or dispatch-warned attempt
    returns None, so the caller submits a fresh one for those; the server's
    stale-marker stamps `dispatch_warning` on a genuinely stuck attempt, which
    also frees it to be resubmitted."""
    try:
        atts = http_json("GET", f"{base}/api/submissions/{sub_id}/attempts",
                         token=api_key)
    except SystemExit:
        return None
    cand = [
        a for a in atts
        if a.get("problem_slug") == slug
        and (a.get("round_index") or 0) == round_idx
        and not a.get("dispatch_warning")
        and not a.get("prefailed_reason")
        and a.get("status") in ("queued", "Pending")
    ]
    if not cand:
        return None
    return max(cand, key=lambda a: a["attempt_id"])


# ---------- generation prefetcher -------------------------------------------
#
# The expensive serial dependency per tuple is:
#   pacing sleep  →  LLM generation  →  LeetCode dispatch + poll
# Generation talks to Ollama; the sleep + dispatch + poll only touch LeetCode.
# Those two pipelines are independent, so we can generate tuple N+1's code in
# a background thread WHILE the main thread is paying the rate-limit cooldown
# and waiting on tuple N's verdict — turning the cooldown into useful work.
#
# The prefetcher walks `todo` in order, generates for each tuple, and stuffs
# the result into a bounded queue. The main loop pops one tuple per iteration
# and proceeds with pacing + submission; meanwhile the worker is already on
# tuple N+1 (or N+2, depending on --prefetch). The queue's bounded size caps
# how far ahead we let it run so we don't generate code for many tuples that
# a Ctrl-C would throw away.


@dataclass
class _Prefetched:
    """Result the prefetcher hands the main loop for ONE tuple.

    `status` ∈ {"ok", "cell_covered", "no_code_snippet", "wrong_language",
                "fetch_err", "ollama_err"}. For "ok" both `code` and `trace`
    are populated; `problem` is the fetched problem dict (kept so the main
    loop doesn't re-fetch). For error/skip statuses the relevant subset of
    fields may be None and `err` carries a human-readable reason."""
    tup: tuple
    problem: dict | None = None
    code: str | None = None
    trace: dict | None = None
    status: str = "ok"
    err: str | None = None


def _call_with_hard_timeout(fn, timeout_s: float, label: str):
    """Run `fn()` under a HARD wall-clock cap and abandon it if it overruns.

    `--inference-timeout` sets urllib's per-read socket timeout, but OpenRouter
    streams keepalive whitespace during a long generation, which resets that
    timer on every trickle so it never fires. The call then blocks far past the
    nominal timeout. This wrapper runs the call in a daemon thread and stops
    waiting after `timeout_s`, so the flag becomes a real total bound. The
    abandoned thread keeps running in the background until its own socket read
    returns; the sweep just moves on without it."""
    box: dict = {}

    def _run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:            # capture SystemExit from http helpers too
            box["error"] = e

    t = Thread(target=_run, daemon=True, name=f"gen-{label}")
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"exceeded {timeout_s:.0f}s wall-clock cap")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class _Prefetcher:
    """Background generator that stays up to `depth` tuples ahead of main.

    Single worker thread, bounded queue, daemon=True so a Ctrl-C in main
    immediately tears it down. The worker re-reads `cell_covered` (a dict
    owned by main) at the moment it processes each tuple so it can skip
    generation for tuples main would skip anyway — Python's GIL makes the
    dict read safe; staleness only ever causes a wasted generation, never a
    missed submission."""

    def __init__(self, *, todo: list, args, base: str, cell_covered: dict,
                 cell_key_fn, depth: int, provider: Provider):
        self._todo = todo
        self._args = args
        self._base = base
        self._cell_covered = cell_covered
        self._cell_key_fn = cell_key_fn
        self._depth = depth
        # The LLM backend is built ONCE in main() and shared with the
        # prefetcher so both threads use the same client state (auth,
        # base_url, etc.) and the same trace_dict shape.
        self._provider = provider
        # depth == 0 → synchronous fallback: main thread generates on demand.
        # depth >= 1 → background worker pre-generates up to `depth` ahead.
        if depth > 0:
            self._q: Queue = Queue(maxsize=depth)
            self._stop = Event()
            self._thread = Thread(target=self._run, daemon=True,
                                  name="matrix-sweep-prefetcher")
        else:
            self._sync_iter = iter(self._todo)

    def start(self) -> None:
        if self._depth > 0:
            self._thread.start()

    def next(self) -> _Prefetched | None:
        """Return the next tuple's result, or None at end. Blocks the main
        loop only while the background worker hasn't caught up (depth > 0),
        or while generation runs synchronously (depth == 0)."""
        if self._depth > 0:
            return self._q.get()
        try:
            tup = next(self._sync_iter)
        except StopIteration:
            return None
        try:
            return self._generate(tup)
        except Exception as e:                                      # noqa: BLE001
            return _Prefetched(tup=tup, status="ollama_err", err=str(e))

    def shutdown(self) -> None:
        """Signal worker to stop + drain so a pending put() unblocks. No-op
        in synchronous mode."""
        if self._depth == 0:
            return
        self._stop.set()
        # Drain any pending items so the worker's blocking put() returns.
        while True:
            try:
                self._q.get_nowait()
            except Empty:
                break

    def _run(self) -> None:
        for tup in self._todo:
            if self._stop.is_set():
                return
            try:
                item = self._generate(tup)
            except Exception as e:                                  # noqa: BLE001
                item = _Prefetched(tup=tup, status="ollama_err", err=str(e))
            self._q.put(item)
        self._q.put(None)        # sentinel

    def _generate(self, tup: tuple) -> _Prefetched:
        model, prompt, language, slug = tup
        # Cheap skip: if main already covered (cell, slug), don't burn the
        # GPU. cell_covered is the live dict main mutates; a stale miss
        # just costs one extra generation.
        cell = self._cell_key_fn(model, prompt["name"], language)
        if slug in self._cell_covered.get(cell, set()):
            return _Prefetched(tup=tup, status="cell_covered")
        try:
            problem = http_json("GET", f"{self._base}/api/problems/{slug}")
        except SystemExit as e:
            return _Prefetched(tup=tup, status="fetch_err", err=str(e))
        snippets = problem.get("code_snippets") or {}
        tpl_text = prompt["system_prompt"] + "\n" + prompt["user_prompt_template"]
        needs_template = "{{code_template}}" in tpl_text
        # CLBG benchmarks ship no Solution-shape starter snippet — they are whole
        # programs, not a method to fill in. The client swaps the WHOLE prompt to
        # the standalone-program contract at generate time (_apply_clbg_shape),
        # so {{code_template}} is never used for them. Don't skip a CLBG cell for
        # a "missing" snippet it is never supposed to have; let it fall through
        # and generate under the CLBG shape.
        is_clbg = _is_clbg_statement(problem.get("content") or "")
        if needs_template and not is_clbg and not snippets.get(language):
            return _Prefetched(tup=tup, problem=problem,
                               status="no_code_snippet")
        # Cache hit short-circuit: if we already generated code for this
        # tuple on a prior run that didn't reach a verdict (dispatch
        # failed, poll timed out, sweep got Ctrl-C'd, etc.) — reuse it.
        # The main loop drops the cache entry as soon as LC returns a
        # real verdict, so cached code only ever represents an "I still
        # owe LC a re-submit" tuple. We still re-fetch `problem` above
        # so any anti-cheat / placeholder-marker validation done at
        # submit time uses the current statement; the only thing we
        # skip is the OpenAI call. We also re-validate the cached
        # code's language fingerprint (a cached blob that doesn't look
        # like the target language is dropped — could happen if the
        # cache survives across a --languages change).
        if getattr(self._args, "refresh_cache", False):
            # --refresh-cache: purge any prior generation for this tuple BEFORE
            # loading, so the load below misses and the model is re-called. This
            # regenerates fresh code (and re-caches it) instead of reusing stale
            # cached output for the run's model.
            _drop_cached_code(self._args.output, _tuple_key(
                model, prompt["name"], language, slug), _cache_salt(self._args))
        cached = _load_cached_code(self._args.output, _tuple_key(
            model, prompt["name"], language, slug), _cache_salt(self._args))
        if cached is not None:
            code, trace = cached
            if _looks_like(code, language):
                print(f"  ↺ cache hit: reusing prior generation for "
                      f"{language}/{slug} (no LLM call)",
                      flush=True)
                return _Prefetched(tup=tup, problem=problem,
                                   code=code, trace=trace, status="ok")
            else:
                _drop_cached_code(self._args.output, _tuple_key(
                    model, prompt["name"], language, slug), _cache_salt(self._args))
        # --submit-from-cache: never call the model. A cache miss (or a cache
        # entry that failed the language fingerprint above) becomes a skip, so
        # the submit phase only ships what was generated earlier.
        if getattr(self._args, "submit_from_cache", False):
            return _Prefetched(tup=tup, problem=problem, status="cache_miss")
        extras = {
            "{{optimization_directive}}": build_optimization_directive(self._args.target),
            "{{hints_block}}":            build_hints_block(self._args.hints, problem.get("hints")),
            "{{code_template}}":          build_code_template_block(snippets, language),
            "{{round_instruction}}":      "",
            "{{rounds_history}}":         "",
        }
        # Announce the call BEFORE it blocks. Generation is the one step here
        # with no bounded feedback: a fresh call to a slow/thinking model can
        # run for minutes (up to the inference timeout), and with the main loop
        # parked on the prefetch queue the sweep otherwise looks frozen right
        # after startup. Printing the tuple + provider makes a slow generation
        # visible and tells the operator exactly which call is in flight.
        hard_cap = _inference_timeout()
        print(f"  ⏳ [prefetch] generating {language}/{slug} via "
              f"{self._provider.name} ({model}), ≤{hard_cap}s…", flush=True)
        _gen_t0 = time.time()
        try:
            code, trace = _call_with_hard_timeout(
                lambda: self._provider.generate(
                    model, problem["content"], slug,
                    {"temperature": self._args.temperature}, language,
                    registry_prompt=prompt, extras=extras,
                ),
                hard_cap, f"{language}/{slug}",
            )
        except TimeoutError as e:
            # HARD wall-clock cap hit — the generation is still running in a
            # background thread but we stop waiting so the sweep progresses.
            print(f"  ✗ [prefetch] generation TIMED OUT for {language}/{slug} "
                  f"({e}); moving on", flush=True)
            return _Prefetched(tup=tup, problem=problem,
                               status="ollama_err",
                               err=f"[{self._provider.name}] generation "
                                   f"timed out after {hard_cap}s")
        except SystemExit as e:
            # Keep the historical "ollama_err" status label so resume-log
            # filters still work; the provider name is in the error string
            # for triage.
            print(f"  ✗ [prefetch] generation FAILED for {language}/{slug} "
                  f"after {time.time() - _gen_t0:.0f}s: {e}", flush=True)
            return _Prefetched(tup=tup, problem=problem,
                               status="ollama_err",
                               err=f"[{self._provider.name}] {e}")
        print(f"  ✓ [prefetch] generated {language}/{slug} in "
              f"{time.time() - _gen_t0:.0f}s "
              f"({len(code or '')} chars)", flush=True)
        if not _looks_like(code, language):
            # Wrong-language output: don't cache — re-generating may
            # give us a clean attempt next time. Caching the broken
            # code would just preserve the bug across resumes. We DO log
            # the raw response to the rejected-generation side file so an
            # expensive empty/wrong-language generation stays inspectable,
            # especially under --generate-only (which never submits, so the
            # server never sees it either).
            _record_rejected_generation(
                self._args.output,
                _tuple_key(model, prompt["name"], language, slug),
                model, prompt["name"], language, slug,
                "wrong_language", code, trace, time.time() - _gen_t0,
            )
            return _Prefetched(tup=tup, problem=problem, code=code, trace=trace,
                               status="wrong_language")
        # SUCCESS — persist for re-use on the next resume, in case this
        # tuple's LC dispatch fails to land a verdict and the operator
        # restarts the sweep.
        _save_cached_code(self._args.output,
                          _tuple_key(model, prompt["name"], language, slug),
                          code, trace, _cache_salt(self._args))
        return _Prefetched(tup=tup, problem=problem, code=code, trace=trace,
                           status="ok")


# ---------- matrix enumeration ----------------------------------------------

def _resolve_prompts(base: str, names_arg: str | None) -> list[dict]:
    """Resolve --prompts NAME[,NAME] to full prompt dicts. Default: every
    template in the registry."""
    all_prompts = http_json("GET", f"{base}/api/prompts")
    if not names_arg:
        return all_prompts
    requested = [n.strip() for n in names_arg.split(",") if n.strip()]
    by_name = {p["name"]: p for p in all_prompts}
    missing = [n for n in requested if n not in by_name]
    if missing:
        avail = ", ".join(by_name.keys())
        raise SystemExit(f"prompts not in registry: {missing}. Available: {avail}")
    return [by_name[n] for n in requested]


def _resolve_languages(arg: str | None) -> list[str]:
    # Default to the harness-measurable set only (MEASURABLE_LANGUAGES), so a
    # sweep never burns attempts on a language the harness cannot score.
    # Explicit --languages may still name any supported language.
    if not arg:
        return sorted(MEASURABLE_LANGUAGES)
    out = [l.strip() for l in arg.split(",") if l.strip()]
    bad = [l for l in out if l not in LANGUAGE_DISPLAY]
    if bad:
        raise SystemExit(f"unsupported languages: {bad}. "
                         f"Supported: {sorted(LANGUAGE_DISPLAY)}")
    not_measurable = [l for l in out if l not in MEASURABLE_LANGUAGES]
    if not_measurable:
        print(f"WARNING: {not_measurable} are not measurable by the perf "
              f"harness yet — those attempts will fail the correctness gate.",
              file=sys.stderr)
    return out


def _interleave_by_level(problems: list[dict]) -> list[str]:
    """Return slugs in a deterministic round-robin across Easy/Medium/Hard,
    sorted alphabetically within each level.

    Why interleave: the source order of /api/problems happens to group
    Easy first, so any sweep aborted mid-run only has rankings data for
    cheap problems. Round-robin guarantees a representative mix of
    difficulty after ANY prefix of the sweep, so a partial sweep is still
    informative about a model's hard-problem performance.

    Determinism: alphabetical within each level + fixed level cycle order
    (Easy → Medium → Hard) means the same competition always produces
    the same slug ordering. Important for resume correctness — though
    even without it the resume log keys off slug not position, so
    re-ordering is safe across runs.
    """
    buckets: dict[str, list[str]] = {"Easy": [], "Medium": [], "Hard": []}
    other: list[str] = []
    for p in problems:
        lvl = (p.get("level") or "").strip()
        slug = p["title_slug"]
        if lvl in buckets:
            buckets[lvl].append(slug)
        else:
            other.append(slug)
    # Sort each bucket so the order is stable across runs regardless of
    # whatever order the server happened to return rows in.
    for lvl in buckets:
        buckets[lvl].sort()
    other.sort()
    # Round-robin pull one from each level in turn until every bucket is
    # empty. Levels that exhaust early just stop contributing — the
    # interleave continues with the remaining ones.
    interleaved: list[str] = []
    order = ["Easy", "Medium", "Hard"]
    indices = {lvl: 0 for lvl in order}
    while True:
        progressed = False
        for lvl in order:
            i = indices[lvl]
            if i < len(buckets[lvl]):
                interleaved.append(buckets[lvl][i])
                indices[lvl] = i + 1
                progressed = True
        if not progressed:
            break
    # Trailing "other" bucket — problems with no recognised level. Rare
    # (every seeded problem has Easy/Medium/Hard) but keep them included.
    return interleaved + other


def _resolve_slugs(base: str, arg: str | None, competition: str | None,
                   *, order: str = "interleave") -> list[str]:
    """Resolve the list of problem slugs the sweep will target. Explicit
    `--slugs` always wins; otherwise we list problems FILTERED by the
    chosen competition (default `leetcode-99-perf`).

    `order` controls how slugs are sequenced when `--slugs` isn't given:
      * "interleave" (default) — round-robin Easy / Medium / Hard so any
                                 prefix of the sweep is a representative
                                 mix of difficulty.
      * "alpha"               — alphabetical by slug.
      * "as-is"               — whatever order the server returns
                                 (legacy behaviour; happens to start with
                                 every Easy in current seeds).

    Without the competition filter we'd grab every Problem row in the DB,
    which now includes 99 canonical + 99 rewritten = 198. The submitted
    batch would then claim to be a 198-problem sweep with pass@k
    denominator 198 — exactly the bug the per-competition scope fixes."""
    if arg:
        return [s.strip() for s in arg.split(",") if s.strip()]
    url = f"{base}/api/problems"
    if competition:
        from urllib.parse import quote
        url += f"?competition={quote(competition)}"
    problems = http_json("GET", url)
    if order == "alpha":
        return sorted(p["title_slug"] for p in problems)
    if order == "as-is":
        return [p["title_slug"] for p in problems]
    return _interleave_by_level(problems)


# ---------- main loop -------------------------------------------------------

def main() -> None:
    # Force line-buffered stdout/stderr. When the sweep's output is piped or
    # redirected (tee, a log file, nohup), Python block-buffers stdout, so
    # progress prints pile up invisibly and the run looks frozen at the last
    # flushed line (typically "prefetcher: background"). Line buffering makes
    # every progress line appear as it happens, TTY or not.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(
        description="Matrix sweep over (prompt × language × problem).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__,
    )
    ap.add_argument("--base-url",  default=os.environ.get("ARENA_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--api-key",   default=os.environ.get("ARENA_API_KEY"))
    ap.add_argument("--provider", default="ollama", choices=sorted(PROVIDERS),
                    help="LLM backend for every model in --models. ollama "
                         "(default) uses --ollama-url; openai uses "
                         "--openai-base-url + --openai-api-key. One sweep "
                         "invocation runs against exactly one provider.")
    ap.add_argument("--ollama-url",
                    default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
                    type=lambda s: s.rstrip("/"),
                    help="Ollama endpoint. Trailing slashes are stripped — "
                         "some tunnels (ngrok in particular) route the "
                         "resulting `//api/generate` to an empty response, "
                         "silently breaking the sweep with connection blips.")
    ap.add_argument("--openai-base-url",
                    default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                    help="OpenAI-compatible /v1 endpoint. Override for Azure, "
                         "OpenRouter, Together, vLLM, etc.")
    ap.add_argument("--openai-api-key", default=os.environ.get("OPENAI_API_KEY"),
                    help="OpenAI API key (or set OPENAI_API_KEY env var).")
    ap.add_argument("--model",        default="qwen2.5-coder:7b",
                    help="single model name (used when --models isn't given).")
    ap.add_argument("--models", default=None, metavar="NAME[,NAME]",
                    help="comma-separated model names to sweep, all served by "
                         "the SAME provider/backend (the chosen provider "
                         "routes by model name). Adds a model axis to the "
                         "matrix; each (model, prompt, language) is its own "
                         "experiment cell / submission. Overrides --model "
                         "when set.")
    ap.add_argument("--model-version", default=None,
                    help="version tag stamped on every submission (shared "
                         "across all --models; the model NAME already "
                         "distinguishes cells). Defaults to the provider "
                         "name (ollama / openai / …) when omitted.")
    ap.add_argument("--temperature", type=float, default=0)
    ap.add_argument("--no-think", action="store_true",
                    help="Disable model reasoning by sending Ollama's "
                         "`\"think\": false` in the /api/chat body. Use it for "
                         "models that reason by default when you want that off. "
                         "Ollama provider only; ignored for --provider openai. "
                         "Default keeps thinking enabled.")

    # Matrix subsetting
    ap.add_argument("--prompts",   default=None, metavar="NAME[,NAME]",
                    help="comma-separated registry prompt names. Default: all.")
    ap.add_argument("--languages", default=None, metavar="SLUG[,SLUG]",
                    help="comma-separated language slugs. Default: every harness-measurable language (MEASURABLE_LANGUAGES).")
    ap.add_argument("--slugs",     default=None, metavar="SLUG[,SLUG]",
                    help="comma-separated problem slugs. Default: every problem "
                         "in the chosen --competition.")
    ap.add_argument("--competition", default="leetcode-99-perf", metavar="SLUG",
                    help="competition slug to target (default: leetcode-99-perf). "
                         "Sets both the problem-set scope and the pass@k "
                         "denominator — pass@k is computed against the "
                         "competition's problem count, not 'every Problem in "
                         "the DB'. Override to e.g. leetcode-99-rewritten "
                         "for the contamination-defeating variants.")
    ap.add_argument("--harness", required=True, metavar="SLUG",
                    help="REQUIRED. Harness (perf oracle) slug that will "
                         "score every submission this sweep creates. "
                         "Competition and harness are decoupled — the "
                         "competition owns the problem set, the harness "
                         "owns how solutions are measured. Common values: "
                         "`leetcode` (LC's online judge), `arena-local` "
                         "(the local HTTP runner). Auto-append also "
                         "filters on this so a cell scored under one "
                         "harness never mixes with the same cell scored "
                         "under another.")
    ap.add_argument("--correctness-oracle", default=None, metavar="SLUG",
                    help="Correctness oracle slug — REQUIRED unless "
                         "--skip-correctness. Correctness is ALWAYS checked "
                         "first, then perf is measured only if Accepted, "
                         "oracle-independent: the dispatcher checks correctness "
                         "via this oracle (`leetcode` = one LeetCode "
                         "submission; an arena-local slug = the runner's "
                         "/validate), and only on Accepted does the perf "
                         "harness measure. Correctness is no longer done "
                         "implicitly inside /measure, so name it explicitly.")
    ap.add_argument("--skip-correctness", action="store_true",
                    help="Skip the correctness gate entirely — measure perf "
                         "without checking correctness first. Mutually "
                         "exclusive with --correctness-oracle; use for "
                         "perf-only runs where correctness is already known.")
    ap.add_argument("--problem-order", default="interleave",
                    choices=("interleave", "alpha", "as-is"),
                    help="how to order problem slugs across the sweep "
                         "(when --slugs isn't given). 'interleave' (default) "
                         "round-robins Easy/Medium/Hard so any prefix of the "
                         "sweep is a representative mix; 'alpha' sorts by "
                         "slug; 'as-is' is the legacy server-order behaviour "
                         "that happens to start with all Easy.")
    ap.add_argument("--target",    choices=("latency", "memory"), default=None,
                    help="if a prompt uses {{optimization_directive}}, fill it with this.")
    ap.add_argument("--compile-flags", default=None, metavar="FLAGS",
                    help="Per-run compilation flags for compiled/JIT languages. "
                         "Either a single flag string applied to the run's "
                         "language (e.g. \"-O3 -march=native -std=c++17\"), or a "
                         "per-language map \"cpp=-O3 -std=c++17;rust=-C opt-level=3\" "
                         "so a multi-language sweep sends the right flags per "
                         "submission. RAW flags: they fully replace that "
                         "language's default and must be valid for its compiler. "
                         "A distinct flag set is recorded as a separate "
                         "MeasurementProfile (energy compared only within the same "
                         "flags). When a flags-choosing prompt is used, the model's "
                         "own PERFARENA-FLAGS take precedence over this.")
    ap.add_argument("--hints",     action="store_true",
                    help="if a prompt uses {{hints_block}}, include LeetCode hints.")
    ap.add_argument("--no-record-generation-failures", dest="record_generation_failures",
                    action="store_false", default=True,
                    help="leave a timed-out or failed generation as a retryable "
                         "'failed' tuple instead of recording it as a failed attempt "
                         "(generation_timeout / generation_error); infrastructure "
                         "errors such as rate limits are always retried")
    ap.add_argument("--rounds",    type=int, default=1, metavar="N",
                    help="iterative refinement: per cell, give the model up to "
                         "N rounds to produce an accepted solution. Round 0 is "
                         "single-shot; rounds 1..N-1 see the prior round's code "
                         "+ LC verdict via the prompt's {{rounds_history}} "
                         "placeholder. Stops early on first accept. Default 1 "
                         "(single-shot, same as before). Use with one of the "
                         "iterative-refine* prompts so the placeholders render "
                         "their refinement framing.")
    ap.add_argument("--max-tuples", type=int, default=None,
                    help="cap how many tuples to process this run (smoke-test).")

    # Pacing & resumability.
    #
    # LeetCode's anti-bot heuristic is asymmetric: rapid-fire submissions
    # to MANY DIFFERENT problems tripped it in our earlier experiments,
    # but rapid repeats to the SAME problem (humans iterate on a single
    # problem) are tolerated. The sweep exploits that by iterating
    # problem (outer) → language → prompt (inner): within one problem we
    # cycle every prompt/language combo before moving to the next slug,
    # so we incur the heavy cross-problem delay only between problems,
    # not between cells.
    ap.add_argument("--inter-tuple-delay", type=float, default=5.0, metavar="SEC",
                    help="seconds between tuples sharing the same (problem, "
                         "language) that differ only in model or prompt. Short "
                         "by default: LC is lenient about repeat hits on one "
                         "problem, and shared-account models stay coordinated "
                         "on it.")
    ap.add_argument("--inter-problem-delay", type=float, default=10.0, metavar="SEC",
                    help="seconds applied when the (problem, language) pair "
                         "changes. SHORT by default because the server's "
                         "per-account picker (`pick_for_submission` in "
                         "`leetcode_accounts.py`) already paces every LC "
                         "submission by the LRU account's cooldown — when "
                         "the pool has 2+ accounts, rotation lets the server "
                         "dispatch the next problem without waiting. We keep "
                         "10 s here purely as insurance against LC's per-IP "
                         "throttle (which rotation doesn't help with). Raise "
                         "to 60-120 s if the pool is single-account or every "
                         "account is consistently in cooldown. Stacks on top "
                         "of ARENA_DISPATCH_INTER_DELAY (which only applies "
                         "WITHIN one submission's dispatch).")
    ap.add_argument("--poll-seconds", type=int, default=None, metavar="SEC",
                    help="how long to wait for each tuple's dispatch to settle "
                         "before giving up and moving to the next tuple. The "
                         "wait covers the whole server-side dispatch: "
                         "correctness validate THEN every measure run. Default "
                         "depends on the run: 300s for LeetCode, 1800s (30 min) "
                         "for arena-local. A CLBG measure runs 1 warmup + 7 "
                         "measure whole-program executions with ~150s host "
                         "overhead each, so one attempt legitimately takes "
                         "~20 min plus validate. A too-short budget expires "
                         "while the server is still measuring, so the attempt "
                         "gets logged as `poll_timeout` and re-dispatched next "
                         "run while the first one still finishes Accepted, "
                         "piling up duplicate attempts. On timeout the tuple is "
                         "logged as `status=poll_timeout` (auto-retried by the "
                         "resume classifier next run; no operator action "
                         "required) and the sweep continues.")
    ap.add_argument("--output", type=Path,
                    default=Path("data/sweep_log.json"),
                    help="resumable progress log; rerunning skips completed tuples.")
    ap.add_argument("--no-resume", action="store_true",
                    help="Ignore any prior entries in --output (sweep_log.json) "
                         "for the current run's skip decisions. Every tuple "
                         "in the matrix re-enters todo regardless of past "
                         "outcomes. Use with --no-auto-append when you want "
                         "a truly-clean baseline (e.g. re-running --rounds 3 "
                         "over problems a --rounds 1 sweep already marked "
                         "complete with a non-Accepted verdict). The log "
                         "itself is NOT erased — new entries append as "
                         "usual, so a subsequent run without the flag can "
                         "resume from this one.")
    ap.add_argument("--no-auto-append", action="store_true",
                    help="Always create a FRESH submission per (model, "
                         "prompt, language) cell — don't try to auto-attach "
                         "to an existing one from a previous run. Useful "
                         "when you want a clean baseline for a new "
                         "experiment (e.g. re-running --rounds 3 against "
                         "the same slugs a --rounds 1 sweep already "
                         "covered) or when the previous submission's "
                         "budget is exhausted and you don't want to purge "
                         "its rows.")
    ap.add_argument("--append-fully-accepted", action="store_true",
                    help="Also attach to a FULLY-ACCEPTED (100%%-solved) "
                         "submission for a cell instead of creating a fresh "
                         "one. By default a completed submission is treated as "
                         "'done' and skipped by the auto-attach search, so "
                         "re-running a cell the model already aced spawns a "
                         "duplicate 1-attempt submission. Pass this on "
                         "re-measurement passes to avoid that fragmentation: "
                         "the sweep attaches to the existing submission, sees "
                         "every problem already covered, and does nothing for "
                         "that cell. No effect with --no-auto-append.")
    ap.add_argument("--retry-failed", action="store_true",
                    help="also re-attempt previously-failed tuples (default: skip them).")
    ap.add_argument("--no-reconcile-progress", dest="reconcile_progress",
                    action="store_false", default=True,
                    help="skip the startup reconcile that drops progress-log "
                         "entries pointing at deleted submissions/attempts. "
                         "Default: enabled — keeps resume honest after a "
                         "server-side delete. Pass this when running offline.")
    ap.add_argument("--deep-reconcile-progress", dest="deep_reconcile_progress",
                    action="store_true", default=False,
                    help="also prune per-ATTEMPT (slug-level) deletions — costs "
                         "one GET per live submission. Default: off (the bulk "
                         "existence check already catches whole-submission "
                         "deletes in a single request).")
    ap.add_argument("--prefetch", type=int, default=0, metavar="N",
                    help="how many tuples ahead the background generator may "
                         "stay. Default 0 = SYNCHRONOUS: generate one tuple's "
                         "code, submit it, poll for its verdict (up to "
                         "--poll-seconds), and only THEN start the next tuple, so "
                         "no generation runs while a slow attempt is still being "
                         "measured. --prefetch N (N>=1) re-enables the background "
                         "pipeline that pre-generates N tuples ahead to overlap "
                         "generation with the verdict/cooldown wait; useful on the "
                         "rate-limited LeetCode path, but against slow arena-local "
                         "measures it lets generated tuples pile up.")
    ap.add_argument("--dry-run", action="store_true",
                    help="enumerate the matrix and report; no model calls, no submits.")
    ap.add_argument("--generate-only", action="store_true",
                    help="Generate model responses and CACHE them under "
                         "<output-dir>/sweep_cache/, but do NOT submit to the "
                         "platform. Made for slow local models: generate once "
                         "(e.g. overnight), then submit later. Keeps the cache "
                         "(never drops it). Submit the cached generations with "
                         "the SAME args plus --submit-from-cache.")
    ap.add_argument("--submit-from-cache", action="store_true",
                    help="Submit cached generations WITHOUT calling the model. "
                         "A tuple with no cache entry is skipped, never "
                         "regenerated. The cache is kept after submit so the "
                         "same generations can be re-submitted (e.g. to a "
                         "different --harness / --correctness-oracle).")
    ap.add_argument("--refresh-cache", action="store_true",
                    help="Force REGENERATION: drop each tuple's cached "
                         "generation before use, so the model is always "
                         "re-called, then submit the fresh code. The cache key "
                         "includes the model, so this purges only the run's "
                         "--model/--models cached solutions for the selected "
                         "languages / prompts / slugs. NOTE: tuples already "
                         "recorded 'complete' in the progress log are still "
                         "skipped by the resume filter — add --no-resume (or "
                         "--retry-failed) to re-generate AND re-submit those "
                         "too. Mutually exclusive with --submit-from-cache.")
    ap.add_argument("--keep-cache", action="store_true",
                    help="Persist each tuple's generated code in the cache AFTER "
                         "its verdict lands (default: the entry is dropped once a "
                         "verdict is recorded). Keeps the cache reusable across "
                         "re-runs, so re-measuring the same solutions (e.g. after "
                         "deleting submissions to re-run at a new N) reuses the "
                         "cached code instead of re-calling the model. Use "
                         "--refresh-cache to force regeneration when you do want "
                         "fresh code.")
    ap.add_argument("--inference-timeout", type=int, default=None, metavar="SEC",
                    help="Per-request timeout (seconds) for ONE model inference "
                         "call (default 600). Raise it for slow local models that "
                         "take minutes per response; lower it to fail fast. Sets "
                         "ARENA_INFERENCE_TIMEOUT_S for the run.")
    args = ap.parse_args()
    if args.inference_timeout is not None:
        os.environ["ARENA_INFERENCE_TIMEOUT_S"] = str(args.inference_timeout)
    if args.refresh_cache and args.submit_from_cache:
        ap.error("--refresh-cache regenerates via the model, but "
                 "--submit-from-cache never calls the model. Pass at most one.")
    # --correctness-oracle is required UNLESS --skip-correctness. Skipping sends
    # no oracle, so the dispatcher's correctness gate stays off for the run.
    if args.skip_correctness:
        args.correctness_oracle = None
    elif not args.correctness_oracle:
        ap.error("--correctness-oracle is required unless --skip-correctness is passed")

    # Poll-timeout default by run type. The poll covers the WHOLE server-side
    # dispatch (correctness validate THEN all measure runs); the client only
    # stops polling once the attempt reaches a terminal status. LeetCode's judge
    # settles a verdict in ~12s/repeat, so 300s is ample. Arena-local CLBG is
    # far slower: each attempt runs `make measure` = 1 warmup + 7 measure = 8
    # whole-program executions, and each execution carries ~150s of host
    # measurement overhead, so a single CLBG measure legitimately takes ~20 min
    # (observed: reverse-complement/csharp finished Accepted at 1258s), plus the
    # correctness validate before it. The old 900s default expired while the
    # server was still measuring, so every slow CLBG attempt was logged as
    # `poll_timeout` and re-dispatched next run — duplicate attempts piled up in
    # the system even though the first attempt finished Accepted. Default
    # arena-local to 1800s (30 min) so one full validate + measure cycle
    # completes before the client gives up. An explicit --poll-seconds wins.
    if args.poll_seconds is None:
        _touches_lc = (args.correctness_oracle == "leetcode"
                       or "leetcode" in (args.harness or "").lower())
        args.poll_seconds = 300 if _touches_lc else 1800

    if not args.dry_run and not args.api_key:
        raise SystemExit("set --api-key or ARENA_API_KEY (skip with --dry-run)")
    base = args.base_url.rstrip("/")

    # 1. Resolve the matrix
    models = ([m.strip() for m in args.models.split(",") if m.strip()]
              if args.models else [args.model])
    prompts = _resolve_prompts(base, args.prompts)
    languages = _resolve_languages(args.languages)
    slugs = _resolve_slugs(base, args.slugs, args.competition,
                           order=args.problem_order)
    print(f"matrix dimensions:")
    # Endpoint shown depends on which provider this invocation is using —
    # the old hard-coded ollama_url misled people running --provider openai.
    _endpoint_shown = (
        args.openai_base_url if args.provider == "openai"
        else args.ollama_url
    )
    print(f"  models:    {len(models):>4} ({', '.join(models)})  "
          f"[provider={args.provider} via {_endpoint_shown}]")
    print(f"  prompts:   {len(prompts):>4} ({', '.join(p['name'] for p in prompts)})")
    print(f"  languages: {len(languages):>4} ({', '.join(languages)})")
    print(f"  problems:  {len(slugs):>4}  (competition={args.competition}, order={args.problem_order})")
    total = len(models) * len(prompts) * len(languages) * len(slugs)
    print(f"  TOTAL TUPLES: {total}")

    # 2. Enumerate + resume filter.
    #
    # Canonical order: slug (outer) → language → prompt → model (inner).
    # We iterate every model/prompt for ONE (problem, language) before moving
    # on, so cross-(problem,language) hops happen ~len(slugs)×len(languages)
    # times in the whole sweep instead of once per tuple. Staying on one
    # (problem, language) while only the model/prompt changes reads to LeetCode
    # like a single user iterating; hopping problems/languages fast reads like
    # breadth-first scraping and gets throttled.
    #
    # Model is the INNERMOST axis on purpose: all --models share ONE LeetCode
    # account, and we want them coordinated — every model submits its solution
    # for the SAME (problem, language, prompt) back-to-back before we touch a
    # different problem/language. Dispatches are still SERIALIZED at the network
    # level (true parallel submission from one IP is exactly what trips the
    # anti-bot throttle); "at the same time" means same problem in the same
    # tight time window, not literally concurrent requests.
    all_tuples = [(m, p, l, s)
                  for s in slugs for l in languages for p in prompts for m in models]
    progress = _load_progress(args.output)
    if args.no_resume and progress:
        # Operator opted out of resume semantics for THIS run. Clear the
        # in-memory copy so `_should_skip` sees no history and every tuple
        # re-enters todo. The on-disk log is preserved untouched — new
        # entries append during the sweep, and a subsequent run without
        # the flag can pick them up.
        print(f"  --no-resume: ignoring {len(progress)} prior entry/entries "
              f"in {args.output} for this run's skip decisions")
        progress = {}
    # Reconcile the resume log with server state BEFORE the skip filter, so
    # entries pointing at deleted submissions/attempts come back into todo
    # instead of being silently skipped. Persist the pruned log so a future
    # --no-reconcile-progress run still benefits from the cleanup.
    if args.reconcile_progress and progress and not args.dry_run:
        before = len(progress)
        progress = _reconcile_progress(progress, base, args.api_key,
                                        deep=args.deep_reconcile_progress)
        if len(progress) != before:
            _save_progress(args.output, progress)
    # Build (submission_id → competition_slug) so the resume filter can
    # detect cross-competition matches in progress.json — the same
    # (model, prompt, lang, slug) attempted under another competition
    # shouldn't count as "done" for THIS sweep.
    sub_competition_by_id: dict[int, str | None] = {}
    if args.competition and not args.dry_run:
        try:
            my_subs = http_json("GET", f"{base}/api/me/submissions",
                                token=args.api_key)
            for s in my_subs:
                sid = s.get("id") or s.get("submission_id")
                if sid is not None:
                    sub_competition_by_id[int(sid)] = (
                        s.get("competition_slug") or None
                    )
        except SystemExit as e:
            # Non-fatal — fall back to competition-agnostic skip behaviour
            # if we can't reach the API. Operator sees the warning and
            # can retry once auth is fixed.
            print(f"  ⚠ couldn't load /api/me/submissions for resume "
                  f"competition cross-check ({e}); resume filter will "
                  f"ignore competition boundaries this run", file=sys.stderr)

    todo = [t for t in all_tuples
            if not _should_skip(
                progress, _tuple_key(t[0], t[1]["name"], t[2], t[3]),
                args.retry_failed,
                current_competition=args.competition,
                sub_competition_by_id=sub_competition_by_id,
            )]
    print(f"  after resume filter: {len(todo)} todo "
          f"({len(all_tuples) - len(todo)} already done/skipped)")
    if args.max_tuples is not None:
        todo = todo[:args.max_tuples]
        print(f"  capped at --max-tuples: {len(todo)}")

    if args.dry_run:
        print(f"\ndry-run sample (first 15):")
        for m, p, l, s in todo[:15]:
            print(f"  {m:<20}  {p['name']:<26}  {l:<10}  {s}")
        return

    if not todo:
        print("nothing to do.")
        return

    # 3. Crude budget estimate.
    #
    # Per-tuple LeetCode-side cost ≈ REPEATS × 12 s. Orchestrator pacing splits
    # along (problem, language) boundaries: a short delay while we stay on the
    # same (problem, language) — even as the model/prompt changes — and the long
    # cooldown only when the (problem, language) pair itself changes. Because
    # all --models share ONE LeetCode account, this keeps every model submitting
    # to the same (problem, language) back-to-back, so LeetCode sees one user
    # iterating rather than breadth-first scraping across problems/languages.
    # Mirror the backend's default so the planning banner doesn't lie.
    # The backend's REPEATS lives in dispatch.py and is authoritative; this
    # local read is purely for the wall-time estimate printed below.
    repeats = int(os.environ.get("ARENA_DISPATCH_REPEATS", "3"))
    # Same story as ARENA_DISPATCH_REPEATS: backend's value is the source
    # of truth; this is just for the planning banner. Default mirrors
    # dispatch.py's INTER_DELAY (raised 60 → 120 after observing the
    # pool exhausting on tight pacing).
    inter_delay_server = float(os.environ.get("ARENA_DISPATCH_INTER_DELAY", "120"))
    pairs_in_order = [(t[3], t[2]) for t in todo]   # (slug, language) per tuple
    problem_hops = sum(1 for i in range(1, len(pairs_in_order))
                       if pairs_in_order[i] != pairs_in_order[i - 1])
    same_pair_hops = max(0, (len(todo) - 1) - problem_hops)
    # `--rounds N` upper-bounds the cost per cell at N × single-shot; the
    # short-circuit-on-accept means the realised cost is usually closer to
    # `(1 / pass@1) × single-shot` per cell, but the budget call has to
    # assume the worst case.
    rounds_factor = max(1, int(args.rounds))
    # A no-LeetCode run applies NO client pacing (see the pacing block below),
    # and the server's INTER_DELAY is leetcode-only — so zero those out of the
    # estimate and banner instead of quoting delays that never fire.
    touches_leetcode = (args.correctness_oracle == "leetcode"
                        or "leetcode" in (args.harness or "").lower())
    tuple_delay = args.inter_tuple_delay if touches_leetcode else 0.0
    problem_delay = args.inter_problem_delay if touches_leetcode else 0.0
    est_seconds = (
        len(todo) * 12 * repeats * rounds_factor
        + same_pair_hops * tuple_delay
        + problem_hops * problem_delay
    )
    print(f"\nestimated wall time: ~{est_seconds / 3600:.1f}h (worst case if every cell uses all {rounds_factor} round(s))")
    if touches_leetcode:
        print(f"  REPEATS={repeats}  ·  rounds={args.rounds}  ·  inter-tuple={args.inter_tuple_delay}s  "
              f"·  inter-problem={args.inter_problem_delay}s")
        print(f"  (problem,language) hops: {problem_hops}  ·  "
              f"same-pair hops (model/prompt only): {same_pair_hops}")
        print(f"  (server INTER_DELAY={inter_delay_server}s also applies WITHIN "
              f"one submission's dispatch.)")
    else:
        print(f"  REPEATS={repeats}  ·  rounds={args.rounds}  ·  "
              f"no LeetCode pacing (arena-local): no inter-submission delays")
    print(f"output log:          {args.output}")
    print()

    # 4. Build the LLM backend once and reuse it for every (prompt, language,
    # problem) tuple. One sweep invocation runs under one provider — see the
    # --provider flag.
    provider: Provider = make_provider(args)
    print(f"provider:            {provider.name}")
    if not args.model_version:
        args.model_version = provider.name
    # Pre-fetch each model's digest once (identity is per-model, served by
    # the same backend). Cached so we don't re-query per tuple.
    model_digests: dict[str, str | None] = {}
    for m in models:
        d = provider.model_digest(m)
        model_digests[m] = d
        print(f"model digest [{m}]: {d[:24] + '…' if d else '(none)'}")

    # 5. Auto-discover existing submissions per cell.
    #
    # If the same (prompt, language, model, model_version, hints, target)
    # tuple already has an ongoing submission in the system (e.g. from a
    # previous sweep run, an interactive `submit_with_local_model.py` run,
    # or this very script after a Ctrl-C), attach to it instead of creating
    # a duplicate. Also lookup which problems that submission already
    # covers so we don't blow through the per-problem k-budget by trying
    # to re-submit something we already have.
    cell_subs: dict[str, int] = {}                # (model, prompt, language) → sub_id
    cell_covered: dict[str, set[str]] = {}         # (model, prompt, language) → set of slugs already attempted there
    if args.no_auto_append:
        # Operator explicitly asked to bypass auto-attach — leave cell_subs
        # empty so the main loop's `sub_id is None` branch creates a fresh
        # submission per cell on the first attempt.
        print("  auto-append: DISABLED (--no-auto-append) — every cell will "
              "create a fresh submission this run")
    else:
        cells_seen = set()
        for model, prompt, language, _slug in todo:
            cell = _cell_key(model, prompt["name"], language)
            if cell in cells_seen:
                continue
            cells_seen.add(cell)
            existing = find_existing_submission(
                base, args.api_key, model, args.model_version, language,
                prompt_id=prompt["id"],
                include_hints=bool(args.hints),
                optimization_target=args.target,
                # Match the CURRENT sweep's round budget — hardcoding
                # n_rounds=1 here would let a --rounds 3 sweep auto-attach
                # to a legacy single-shot submission, then trip on the
                # server's k×n_rounds budget cap the moment round 1 fires.
                # Different n_rounds = different experimental cell; create
                # a fresh submission for this run rather than re-using an
                # incompatible one.
                n_rounds=args.rounds,
                # Scope auto-append to THIS sweep's competition — otherwise
                # `--competition leetcode-99-rewritten` would attach to the
                # earlier `leetcode-99-perf` submission for the same cell
                # and the backend would 400 every attempt for "problem
                # slugs not in submission's competition".
                competition_slug=args.competition,
                # Also scope by harness — otherwise a --harness arena-local
                # sweep would auto-append to a prior leetcode-harness
                # submission for the same cell and the arena-local runs
                # would mix in one Submission row with LC verdicts. Legacy
                # rows (no harness_slug on /me/submissions) match any
                # requested harness to preserve back-compat.
                harness_slug=args.harness,
                # When set, attach to an already-complete submission instead of
                # letting it fall through to a fresh-submission create (which
                # fragments a re-run of an aced cell into duplicate rows).
                include_fully_accepted=args.append_fully_accepted,
            )
            if existing is None:
                continue
            sid = existing["id"]
            cell_subs[cell] = sid
            # Snapshot which problems are TRULY covered in the existing
            # submission. "Covered" means the attempt has a real LeetCode
            # verdict — at least one Run row AND no dispatch_warning.
            # Auth-failed and dispatch-failed attempts have n_runs=0 + a
            # dispatch_warning; counting them as covered would silently
            # skip the retry the operator just asked for by re-running.
            atts = http_json("GET", f"{base}/api/submissions/{sid}/attempts",
                             token=args.api_key)
            covered: set[str] = set()
            n_failed = 0
            for a in atts:
                has_runs = (a.get("n_runs") or 0) > 0
                has_warning = bool(a.get("dispatch_warning"))
                if has_runs and not has_warning:
                    covered.add(a["problem_slug"])
                else:
                    # Auth-failed, dispatch-failed, or still-pending.
                    # Excluded from `covered` so the sweep re-attempts.
                    n_failed += 1
            cell_covered[cell] = covered
            tail = (f" ({n_failed} prior attempt(s) flagged auth/dispatch-failed "
                    f"will be retried)" if n_failed else "")
            print(f"  auto-append: cell {cell!r} → submission #{sid} "
                  f"({len(covered)} problems with real verdicts; will skip those){tail}")

    # ---- Start the prefetcher (generates code in the background while main
    # is sleeping / dispatching / polling). With --prefetch 0 the same class
    # runs synchronously in the main thread for old-style sequential behavior.
    prefetcher = _Prefetcher(
        todo=todo, args=args, base=base,
        cell_covered=cell_covered, cell_key_fn=_cell_key,
        depth=args.prefetch, provider=provider,
    )
    if args.prefetch > 0:
        print(f"  prefetcher: background, up to {args.prefetch} tuple(s) ahead "
              f"(generation overlaps with LeetCode wait)")
    else:
        print(f"  prefetcher: disabled (--prefetch 0, sequential)")
    prefetcher.start()

    # --generate-only: pull every tuple through the prefetcher (which generates
    # + caches, or reuses an existing cache entry) but never submit. This is the
    # slow half for local models; run it once, then submit with --submit-from-cache.
    if args.generate_only:
        n_gen = n_other = 0
        while True:
            item = prefetcher.next()
            if item is None:
                break
            _m, _p, _l, _s = item.tup
            if item.status == "ok":
                n_gen += 1
                print(f"  [{_now_hms()}] ✓ cached {_l}/{_s}")
            else:
                n_other += 1
                # Surface the ACTUAL reason (HTTP body / exception), not just the
                # status label — a generation failure (bad base_url, 4xx, auth)
                # is otherwise invisible in --generate-only and reads as a
                # silent skip. `err` carries the provider's error text.
                detail = getattr(item, "err", None)
                print(f"  [{_now_hms()}] · skip {_l}/{_s} ({item.status})"
                      + (f": {detail}" if detail else ""))
        print(f"\ngenerate-only: {n_gen} generation(s) cached under "
              f"{_cache_dir(args.output)}; {n_other} skipped, nothing submitted.")
        _rej = _rejected_log_path(args.output)
        if _rej.exists():
            print(f"  wrong-language / empty generations logged for inspection "
                  f"in {_rej}")
        print("Submit them with the SAME command plus --submit-from-cache "
              "(no model calls, cache kept for re-submission).")
        return

    n_ok = n_fail = n_skip = 0
    sweep_start = time.time()
    prev_pair: tuple[str, str] | None = None   # (slug, language) — pacing boundary
    i = 0
    while True:
        item = prefetcher.next()
        if item is None:
            break
        i += 1
        model, prompt, language, slug = item.tup
        key = _tuple_key(model, prompt["name"], language, slug)
        cell = _cell_key(model, prompt["name"], language)
        model_digest = model_digests.get(model)
        elapsed_h = (time.time() - sweep_start) / 3600

        # Print the header BEFORE the pacing sleep so the user sees what
        # tuple we're about to handle. The sleep itself moves below the
        # skip checks so we don't burn 120s of pacing when this iteration
        # never touches LeetCode anyway.
        print(f"\n[{_now_hms()} · {i}/{len(todo)} · t+{elapsed_h:.2f}h] "
              f"model={model:<16} prompt={prompt['name']:<22} "
              f"lang={language:<8} slug={slug}")

        # ---- Branch on prefetcher outcome ----
        # cell_covered may have advanced since the prefetcher checked, so we
        # also confirm here. The other status branches are terminal (recorded
        # to progress + counted as skip/fail) — generation already happened
        # (or was deliberately skipped) on the prefetcher thread.
        #
        # CRUCIAL: skip-only branches `continue` WITHOUT advancing `prev_pair`
        # or sleeping. They never contact LeetCode, so there's no anti-bot
        # pacing requirement, and the pacing-relevant cursor stays anchored
        # to the LAST tuple that actually dispatched. Otherwise we'd pay
        # 120s × (# already-in-submission tuples) for nothing.
        if item.status == "cell_covered" or slug in cell_covered.get(cell, set()):
            existing_sid = cell_subs.get(cell)
            print(f"  ↺ already in submission #{existing_sid}; skipping (no LC contact, no delay)")
            progress[key] = {
                "status": "complete",
                "reason": "already_in_existing_submission",
                "submission_id": existing_sid,
                "at": _now_iso(),
            }
            _save_progress(args.output, progress)
            n_skip += 1
            continue
        if item.status == "fetch_err":
            print(f"  !! fetch problem failed: {item.err} (no LC contact, no delay)")
            progress[key] = {"status": "failed",
                             "reason": f"fetch_problem: {item.err}",
                             "at": _now_iso()}
            _save_progress(args.output, progress)
            n_fail += 1
            continue
        if item.status == "no_code_snippet":
            print(f"  skip: prompt requires {{{{code_template}}}} but no "
                  f"{language!r} snippet for {slug} (no LC contact, no delay)")
            progress[key] = {"status": "skipped",
                             "reason": "no_code_snippet_for_language",
                             "at": _now_iso()}
            _save_progress(args.output, progress)
            n_skip += 1
            continue
        gen_verdict: str | None = None
        if item.status == "ollama_err":
            gen_verdict = (_generation_failure_verdict(item.err)
                           if args.record_generation_failures else None)
            if gen_verdict is None:
                print(f"  !! ollama failed: {item.err} (no LC contact, no delay)")
                progress[key] = {"status": "failed", "reason": f"ollama: {item.err}",
                                 "at": _now_iso()}
                _save_progress(args.output, progress)
                n_fail += 1
                continue
            print(f"  !! generation failed: {item.err} — recording a failed "
                  f"attempt ({gen_verdict})")
            item.code = ""
            item.trace = {
                "prompt": None, "system_prompt": None, "raw_response": "",
                "reasoning": None, "model": model, "provider": provider.name,
                "endpoint": None, "params": {"temperature": args.temperature},
                "created_at": _now_iso(),
                "provider_meta": {"failure": gen_verdict, "error": item.err},
            }
        if item.status == "cache_miss":
            print(f"  skip: --submit-from-cache and no cached generation for "
                  f"{language}/{slug} (no model call, no LC contact)")
            progress[key] = {"status": "skipped",
                             "reason": "no_cached_generation",
                             "at": _now_iso()}
            _save_progress(args.output, progress)
            n_skip += 1
            continue

        # We're past the no-op branches — this tuple WILL contact LeetCode
        # (or at least record an attempt against an existing submission via
        # the auto-append path, which still pings the server).
        #
        # Pacing model (changed): instead of a flat per-hop sleep, ask the
        # server for the current pool state and sleep exactly as long as
        # the LRU account's cooldown demands. Rotation across a multi-
        # account pool means most hops cost only the IP-throttle FLOOR
        # (`--inter-problem-delay`, default 10 s) — but when every
        # account in the pool is within the per-account `min_gap_s`
        # window (e.g. 2-account pool dispatching fast), we honestly
        # wait for one to come ready so we don't pile pending submits
        # on the dispatch worker.
        pair = (slug, language)
        if prev_pair is not None:
            # Floor stays at `--inter-problem-delay` for the (slug, language)
            # hop case; same-pair (model/prompt change) drops to
            # `--inter-tuple-delay` since LC is more lenient about repeat
            # hits on the same problem.
            if pair != prev_pair:
                floor = args.inter_problem_delay
                kind_prefix = "problem/language hop"
            else:
                floor = args.inter_tuple_delay
                kind_prefix = "same problem+language"
            # Ceiling: cap the pool-driven wait at INTER_DELAY-ish (we
            # cap at 1.5× the per-account gap so a fully-cool pool can
            # tolerate small clock skew between accounts).
            ceiling = max(floor, 1.5 * 120.0)
            # The pool-driven pacing exists to respect LeetCode's per-account
            # rate limits. It only applies when this run actually submits to
            # LeetCode — i.e. the correctness oracle is `leetcode` (or a
            # leetcode-kind perf harness). For an arena-local run (correctness
            # measured on the runner, or --skip-correctness) there is NO account
            # pool to pace against, and querying it blocks on an unrelated
            # LeetCode cooldown — up to the 30-min pool-recheck loop — between
            # every submission. A run that never touches LeetCode (arena-local
            # correctness AND a non-leetcode perf harness) has NO rate limit to
            # respect, and matrix_sweep already self-paces by polling each
            # submission's verdict before the next — so it gets NO inter-
            # submission delay at all: not the account-cooldown wait, and not
            # even the IP-throttle floor (`--inter-problem/tuple-delay`).
            touches_leetcode = (
                args.correctness_oracle == "leetcode"
                or "leetcode" in (args.harness or "").lower()
            )
            if touches_leetcode:
                wait_s, reason = _pacing_wait_until_account_ready(
                    base, args.api_key,
                    floor_seconds=floor, ceiling_seconds=ceiling,
                )
            else:
                wait_s, reason = 0.0, "no LeetCode contact; no delay"
            if wait_s > 0:
                tag = " (prefetcher generating next tuple)" if args.prefetch > 0 else ""
                print(f"  … sleeping {wait_s:.0f}s "
                      f"({kind_prefix}; {reason}){tag}", flush=True)
                time.sleep(wait_s)
        # prev_pair is advanced only after the submit ACTUALLY lands
        # (see the post-submit assignment further below). A submit that
        # 400s on sample-budget or 5xxs on a server hiccup never touched
        # LeetCode, so its pair shouldn't anchor the next tuple's delay.
        # status ∈ {"ok", "wrong_language"} both yield a recorded attempt; the
        # wrong-language case ships the malformed output with prefailed_reason
        # so the server records it as a failed attempt without dispatching it
        # to LeetCode. Only the truly-skipped statuses (cell_covered, fetch_err,
        # no_code_snippet, ollama_err) bail above.
        code, trace = item.code, item.trace
        prefailed_reason = ("wrong_language_extracted" if item.status == "wrong_language"
                            else gen_verdict)
        if prefailed_reason:
            print(f"  ⚠ model emitted non-{language} code — recording as "
                  f"failed (raw response preserved in trace)")

        # ---- Iterative-refinement round loop ----
        #
        # round 0 uses the code the prefetcher already generated; rounds 1+
        # are generated INLINE here with {{rounds_history}} filled in from
        # the prior round's code + LC verdict. We stop on first accept,
        # mirroring iterative_refine.py's contract — so a cell that nails
        # round 0 costs the same as today (--rounds 1 / default), and only
        # failed cells pay for additional rounds.
        history: list[dict] = []                 # for build_rounds_history
        previous_attempt_id: int | None = None
        sub_id = cell_subs.get(cell)
        solved_at_round: int | None = None
        final_att: dict | None = None
        round_problem = item.problem             # for re-generation in rounds 1+
        fence = LANGUAGE_DISPLAY.get(language, (language, language))[1]
        snippets = (round_problem or {}).get("code_snippets") or {}
        tuple_failed_unrecoverably = False        # flips on submit/ollama errors mid-loop

        for round_idx in range(args.rounds):
            # Round 0 already has code + trace from the prefetcher. Rounds
            # 1+ regenerate inline with the prior history substituted in.
            if round_idx > 0:
                ri_block = build_round_instruction(round_idx, args.rounds)
                rh_block = build_rounds_history(history, args.target, fence)
                extras_r = {
                    "{{optimization_directive}}": build_optimization_directive(args.target),
                    "{{hints_block}}":            build_hints_block(args.hints, round_problem.get("hints") if round_problem else None),
                    "{{code_template}}":          build_code_template_block(snippets, language),
                    "{{round_instruction}}":      ri_block,
                    "{{rounds_history}}":         rh_block,
                }
                try:
                    code, trace = provider.generate(
                        model,
                        (round_problem or {}).get("content", ""),
                        slug,
                        {"temperature": args.temperature}, language,
                        registry_prompt=prompt, extras=extras_r,
                    )
                except SystemExit as e:
                    print(f"  !! {provider.name} failed at round {round_idx}: {e}")
                    progress[key] = {"status": "failed",
                                     "reason": f"{provider.name} (round {round_idx}): {e}",
                                     "at": _now_iso(),
                                     "rounds_used": round_idx,
                                     "submission_id": sub_id}
                    _save_progress(args.output, progress)
                    n_fail += 1
                    tuple_failed_unrecoverably = True
                    break
                prefailed_reason = (
                    "wrong_language_extracted"
                    if not _looks_like(code, language) else None
                )

            # ---- Resolve compilation flags (per attempt) ----
            # Model-chosen flags (PERFARENA-FLAGS marker in the raw response,
            # from a flags-choosing prompt) take precedence over the operator's
            # --compile-flags; both are sanitized. Stored on the attempt trace so
            # the dispatch applies + records them per attempt.
            _op_flags = sanitize_compile_flags(_resolve_compile_flags(args.compile_flags, language))
            _model_flags = sanitize_compile_flags(extract_compile_flags(trace.get("raw_response") or ""))
            _compile_flags = _model_flags or _op_flags
            if _compile_flags:
                trace["compile_flags"] = _compile_flags
                if _model_flags:
                    trace["compile_flags_source"] = "model"
                    print(f"    ↳ model chose compile flags: {_compile_flags}", flush=True)

            # ---- Build the attempt body ----
            summary = (trace.get("reasoning") or trace.get("raw_response")
                       or "matrix sweep — see structured trace").strip()[:4000] or "x"
            if prefailed_reason and prefailed_reason.startswith("generation_"):
                summary = (f"client flagged {prefailed_reason}: the provider call "
                           "failed, so the model returned no code; the error is in "
                           "trace.provider_meta")
            elif prefailed_reason and len(summary) < 20:
                summary = (f"client flagged {prefailed_reason}; raw response is "
                           "in trace.raw_response")
            elif len(summary) < 20:
                # A terse-but-valid reasoning (e.g. a model that just says
                # "Simple.") is real provenance, not a defect — but the server
                # requires >=20 chars. Keep the model's words and append
                # identifying context so the trace stays meaningful AND passes.
                summary = f"{summary} [{model} · {language}/{slug}; full trace in trace.raw_response]"
            leaf = compute_trace_hash(code, summary, trace)
            # Token counts: pull from the provider's usage block when it
            # was returned (OpenAI does, Ollama-OAI mostly does, raw
            # Ollama doesn't). Fall back to estimation from the response
            # code for `response_tokens` so we always have *something*
            # for the cost columns to display, but leave `prompt_tokens`
            # null when the provider didn't surface it — that null is
            # what tells the SPA "live recomputation isn't possible
            # for this attempt, show the snapshot".
            usage = (trace.get("provider_meta") or {}).get("usage") or {}
            attempt = {"title_slug": slug, "code": code,
                       "inference_trace": summary, "trace": trace,
                       "cost_usd": call_cost_usd(trace),
                       "response_tokens": (
                           int(usage["completion_tokens"])
                           if usage.get("completion_tokens") is not None
                           else estimate_code_tokens(code)
                       ),
                       "prompt_tokens": (
                           int(usage["prompt_tokens"])
                           if usage.get("prompt_tokens") is not None
                           else None
                       ),
                       "wall_clock_ms": trace.get("wall_clock_ms"),
                       "generation_energy_j": trace.get("energy_j"),
                       "round_index": round_idx}
            if previous_attempt_id is not None:
                attempt["previous_attempt_id"] = previous_attempt_id
            if prefailed_reason:
                attempt["prefailed_reason"] = prefailed_reason
            if model_digest:
                attempt["model_digest"] = model_digest

            # ---- Idempotent retry guard ----
            # A prior run may have already submitted THIS round and its slow CLBG
            # measure could still be running server-side. Resubmitting would create
            # a DUPLICATE attempt (the poll_timeout -> re-dispatch -> pile-up
            # failure noted in main()). If an attempt for (sub_id, slug, round_idx)
            # is already in flight, skip the submit and resume polling it below.
            resume_att = (
                _find_inflight_attempt(base, args.api_key, sub_id, slug, round_idx)
                if sub_id is not None else None
            )
            # ---- Submit (create cell submission on first round/cell, append after) ----
            try:
                if resume_att is not None:
                    print(f"  ↻ round {round_idx} for {language}/{slug} already in "
                          f"flight (attempt {resume_att['attempt_id']}); resuming "
                          f"poll, not resubmitting (no duplicate)")
                    cell_covered.setdefault(cell, set()).add(slug)
                elif sub_id is None:
                    created = http_json(
                        "POST", f"{base}/api/submissions", token=args.api_key,
                        body={
                            "model_name": model,
                            "model_version": args.model_version,
                            "language": language,
                            "prompt_template": prompt["system_prompt"],
                            "prompt_id": prompt["id"],
                            "model_params": {
                                "temperature": args.temperature,
                                # Extra generation knobs (think, and any future
                                # top_p / seed / …) — declared once in
                                # _extra_gen_knobs. Present only when non-default;
                                # each also salts the code cache, so a distinct
                                # value is a distinct submission AND a distinct
                                # cache file.
                                **_extra_gen_knobs(args),
                                # Submission-level compile flags (operator
                                # --compile-flags for this language). Per-attempt
                                # model-chosen flags on attempt.trace override
                                # this in dispatch. NOT a generation knob (it
                                # doesn't change the generated code, so it stays
                                # out of the cache salt). Omitted when empty so
                                # legacy submissions are byte-identical.
                                **({"compile_flags": sanitize_compile_flags(
                                        _resolve_compile_flags(args.compile_flags, language))}
                                   if _resolve_compile_flags(args.compile_flags, language) else {}),
                            },
                            "n_samples_per_problem": 1,
                            "n_rounds": args.rounds,
                            "include_hints": bool(args.hints),
                            "optimization_target": args.target,
                            # matrix_sweep's round loop ALWAYS breaks on the
                            # first Accepted (see the Accepted check further
                            # down in this file), so n_rounds is a MAXIMUM,
                            # never a fixed count. Stamp the flag so later
                            # analysis of /submissions/{id} can filter these
                            # correctness-driven runs apart from experiments
                            # that need every round to execute regardless.
                            "stop_on_accept": args.rounds > 1,
                            # Scope the submission to the chosen competition so
                            # total_problems / pass@k use that competition's
                            # size as the denominator (not "every Problem row
                            # in the DB", which would now report 45/198 for
                            # the canonical 99 because of the rewritten 99).
                            "competition_slug": args.competition,
                            # Required — Competition is decoupled from
                            # Harness; every submission carries its own.
                            "harness_slug": args.harness,
                            # Optional correctness oracle override. When
                            # set, dispatcher calls its /validate before
                            # /measure. When null, correctness comes
                            # from the perf harness's response.
                            "correctness_oracle_slug": args.correctness_oracle,
                            "attempts": [attempt],
                        },
                    )
                    sub_id = created["submission_id"]
                    cell_subs[cell] = sub_id
                    cell_covered.setdefault(cell, set()).add(slug)
                    print(f"  ✓ created submission #{sub_id} (cell: {cell}) [round {round_idx}]")
                else:
                    http_json(
                        "POST", f"{base}/api/submissions/{sub_id}/attempts",
                        token=args.api_key, body={"attempts": [attempt]},
                    )
                    cell_covered.setdefault(cell, set()).add(slug)
                    print(f"  ✓ appended round {round_idx} to submission #{sub_id}")
                # The submit landed → the server is now responsible for
                # dispatching to LeetCode. From the pacing-cursor's POV
                # THIS tuple counts as the most-recent LC contact, so
                # advance prev_pair. If the submit had failed (SystemExit
                # below), we'd skip this and the next tuple's delay
                # would still be measured from the previous successful
                # tuple — no wasted 120s after a 400.
                prev_pair = pair
            except SystemExit as e:
                print(f"  !! submit failed at round {round_idx}: {e}")
                progress[key] = {"status": "failed",
                                 "reason": f"submit (round {round_idx}): {e}",
                                 "at": _now_iso(), "leaf_hash": leaf,
                                 "rounds_used": round_idx + 1,
                                 "submission_id": sub_id}
                _save_progress(args.output, progress)
                n_fail += 1
                tuple_failed_unrecoverably = True
                # prev_pair intentionally NOT advanced — see comment above.
                break

            # A client-prefailed attempt (wrong-language / empty generation) was
            # submitted with prefailed_reason, so the server records it as failed
            # WITHOUT dispatching it — there is nothing to measure. Polling it would
            # burn the full --poll-seconds (1800s on arena-local): a runless attempt's
            # status stays "queued" and never reaches a terminal value that
            # _poll_for_attempt recognizes, so the poll always times out. Skip the
            # poll, record a PERMANENT generator failure (status="failed", not
            # "poll_timeout"), and move on — otherwise the resume filter auto-retries
            # it and re-blocks 1800s every sweep. Only when we actually submitted the
            # prefailed code: if resume_att is set we resumed a genuine in-flight
            # attempt and must poll it normally.
            if prefailed_reason and resume_att is None:
                print(f"  → round {round_idx}: {prefailed_reason} — not dispatched, "
                      f"skipping poll")
                progress[key] = {"status": "failed",
                                 "reason": prefailed_reason,
                                 "at": _now_iso(), "leaf_hash": leaf,
                                 "rounds_used": round_idx + 1,
                                 "submission_id": sub_id}
                _save_progress(args.output, progress)
                n_fail += 1
                tuple_failed_unrecoverably = True
                break

            # ---- Poll for THIS round's verdict ----
            att = _poll_for_attempt(base, args.api_key, sub_id, slug,
                                    round_attempt_idx=round_idx,
                                    timeout_s=args.poll_seconds)
            if att is None:
                inflight = _find_inflight_attempt(
                    base, args.api_key, sub_id, slug, round_idx)
                inflight_id = (inflight or {}).get("attempt_id")
                print(f"  ⚠ poll timeout after {args.poll_seconds}s on round {round_idx}; "
                      f"attempt {inflight_id if inflight_id else '?'} still measuring — "
                      f"next run resumes its poll instead of resubmitting")
                progress[key] = {"status": "poll_timeout",
                                 "submission_id": sub_id,
                                 "attempt_id": inflight_id,
                                 "at": _now_iso(), "leaf_hash": leaf,
                                 "rounds_used": round_idx + 1}
                _save_progress(args.output, progress)
                n_fail += 1
                tuple_failed_unrecoverably = True
                break

            # System-level dispatch failure (auth dead, LC frontend down, …).
            # If the cause is "credential pool exhausted" — recognisable from
            # the warning text — we INLINE-RECOVER: wait until the pool's
            # earliest_available_after, then ask the server to re-dispatch
            # the same submission and poll again. This avoids the death
            # spiral of submitting → failing → marking dispatch_failed → on
            # to the next tuple → failing again, all of which costs the user
            # generation $ for no LeetCode contact.
            if att.get("dispatch_warning"):
                warn = att["dispatch_warning"]
                print(f"  ⚠ dispatch FAILED on round {round_idx}: {warn}")
                if _is_pool_exhaustion_warning(warn):
                    outcome = _wait_for_pool_or_fail(base, args.api_key)
                    if outcome in ("available", "waited"):
                        # Tell the server to re-dispatch this submission's
                        # pending attempts (including the one we just got
                        # a warning on), then re-poll for the same round.
                        try:
                            http_json(
                                "POST",
                                f"{base}/api/admin/submissions/{sub_id}/dispatch",
                                token=args.api_key, body={},
                            )
                        except SystemExit as e:
                            print(f"  !! couldn't trigger re-dispatch: {e}")
                        retry_att = _poll_for_attempt(
                            base, args.api_key, sub_id, slug,
                            round_attempt_idx=round_idx,
                            timeout_s=args.poll_seconds,
                        )
                        if retry_att is not None and not retry_att.get("dispatch_warning"):
                            # Recovery worked — fall through with the
                            # retried attempt as the verdict source.
                            att = retry_att
                            # No `continue`/`break`: drop down to the
                            # success-handling block below.
                        else:
                            # Still failing after waiting. Mark and move on.
                            new_warn = (retry_att or {}).get("dispatch_warning",
                                                              "still no verdict after pool recovery")
                            print(f"  ⚠ still failing after pool wait: {new_warn}")
                            progress[key] = {
                                "status": "dispatch_failed",
                                "reason": f"dispatch_warning_after_wait: {str(new_warn)[:200]}",
                                "at": _now_iso(),
                                "rounds_used": round_idx + 1,
                                "submission_id": sub_id,
                            }
                            _save_progress(args.output, progress)
                            n_fail += 1
                            tuple_failed_unrecoverably = True
                            break
                    else:
                        # outcome ∈ {"empty", "unknown"} — pool can't recover
                        # via wait. Fall back to the old behaviour: mark the
                        # tuple as dispatch_failed and continue. Auto-retried
                        # by the resume filter on the operator's next run.
                        print(f"     ↳ no LeetCode runs were recorded; "
                              f"resume the sweep once you've added or "
                              f"refreshed accounts at /admin/leetcode-accounts.")
                        progress[key] = {
                            "status": "dispatch_failed",
                            "reason": f"dispatch_warning: {warn[:200]}",
                            "at": _now_iso(),
                            "rounds_used": round_idx + 1,
                            "submission_id": sub_id,
                        }
                        _save_progress(args.output, progress)
                        n_fail += 1
                        tuple_failed_unrecoverably = True
                        break
                elif _is_harness_unreachable_warning(warn):
                    # The perf harness / correctness oracle host was
                    # unreachable. It is a single box, and a transient outage
                    # usually clears in a couple of minutes. Wait ONCE, then
                    # re-dispatch this submission's pending attempts and re-poll
                    # the same round. If it is still down, fall back to the old
                    # behaviour (mark dispatch_failed; the resume filter retries
                    # it on the next sweep run).
                    print(f"     ↳ harness/oracle is down; waiting "
                          f"{_HARNESS_DOWN_RETRY_WAIT_S}s once, then one retry.")
                    time.sleep(_HARNESS_DOWN_RETRY_WAIT_S)
                    retry_att = _redispatch_and_repoll(
                        base, args.api_key, sub_id, slug, round_idx,
                        args.poll_seconds)
                    if retry_att is not None and not retry_att.get("dispatch_warning"):
                        # Harness recovered — fall through with the retried
                        # attempt as the verdict source (no break/continue).
                        att = retry_att
                    else:
                        new_warn = (retry_att or {}).get(
                            "dispatch_warning", "still unreachable after wait")
                        print(f"  ⚠ harness still down after "
                              f"{_HARNESS_DOWN_RETRY_WAIT_S}s wait: {new_warn}")
                        progress[key] = {
                            "status": "dispatch_failed",
                            "reason": f"harness_unreachable_after_wait: {str(new_warn)[:200]}",
                            "at": _now_iso(),
                            "rounds_used": round_idx + 1,
                            "submission_id": sub_id,
                        }
                        _save_progress(args.output, progress)
                        n_fail += 1
                        tuple_failed_unrecoverably = True
                        break
                else:
                    # Non-pool warning (transient LC 5xx, parse error, …).
                    # Same as before: mark as dispatch_failed, continue.
                    print(f"     ↳ no LeetCode runs were recorded for this attempt.")
                    print(f"     ↳ this tuple will be auto-retried on the "
                          f"next sweep run (status=dispatch_failed).")
                    progress[key] = {
                        "status": "dispatch_failed",
                        "reason": f"dispatch_warning: {warn[:200]}",
                        "at": _now_iso(),
                        "rounds_used": round_idx + 1,
                        "submission_id": sub_id,
                    }
                    _save_progress(args.output, progress)
                    n_fail += 1
                    tuple_failed_unrecoverably = True
                    break

            verdict = att.get("status")
            print(f"  → round {round_idx}: {verdict} · runs {att.get('n_accepted_runs')}/{att.get('n_runs')} accepted "
                  f"· avg_rt={att.get('avg_runtime_ms')} avg_mem={att.get('avg_memory_mb')}")
            final_att = att
            previous_attempt_id = att["attempt_id"]
            # Build the history entry for the NEXT round (if any).
            history.append({
                "round_index": round_idx,
                "code": code,
                "judge": distill_judge_result(att),
            })
            if att.get("accepted"):
                solved_at_round = round_idx
                break

        if tuple_failed_unrecoverably:
            continue

        # Final summary for this tuple — records the FIRST-accept round
        # (or None if all rounds failed), and the count of rounds actually run.
        assert final_att is not None
        progress[key] = {
            "status": "complete",
            "submission_id": sub_id,
            "attempt_id": final_att["attempt_id"],
            "verdict": final_att.get("status"),
            "accepted": final_att.get("accepted"),
            "n_runs": final_att.get("n_runs"),
            "n_accepted_runs": final_att.get("n_accepted_runs"),
            "avg_runtime_ms": final_att.get("avg_runtime_ms"),
            "avg_memory_mb": final_att.get("avg_memory_mb"),
            "avg_runtime_percentile": final_att.get("avg_runtime_percentile"),
            "avg_memory_percentile": final_att.get("avg_memory_percentile"),
            "rounds_used": len(history),
            "solved_at_round": solved_at_round,
            "leaf_hash": leaf,
            "at": _now_iso(),
        }
        _save_progress(args.output, progress)
        # LC returned a verdict for this tuple — the cached code we
        # may have written during prefetch is now obsolete. Drop it
        # so the next sweep's cache directory doesn't accumulate
        # forever-stale entries. Exceptions: --submit-from-cache keeps the
        # cache so the same generations can be re-submitted elsewhere, and
        # --keep-cache keeps it so a re-run reuses generations instead of
        # re-calling the model.
        if not args.submit_from_cache and not getattr(args, "keep_cache", False):
            _drop_cached_code(args.output, key, _cache_salt(args))
        n_ok += 1

        # Pacing happens at the START of the next iteration via the
        # prev_pair-aware sleep block above. No tail sleep here.

    # Stop the background generator. The daemon=True flag means a Ctrl-C in
    # main would tear it down anyway, but this is the clean exit path.
    prefetcher.shutdown()

    elapsed_h = (time.time() - sweep_start) / 3600
    print(f"\n=== sweep complete in {elapsed_h:.2f}h ===")
    print(f"  completed: {n_ok}")
    print(f"  skipped:   {n_skip}")
    print(f"  failed:    {n_fail}")
    print(f"  log:       {args.output}")
    print(f"  cells:     {len(cell_subs)} submission(s) created")
    for cell, sid in sorted(cell_subs.items()):
        print(f"    #{sid}  {cell}")


if __name__ == "__main__":
    main()
