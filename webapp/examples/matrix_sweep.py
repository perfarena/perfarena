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

`--poll-seconds` (default 300 s = 5 min) is the per-tuple dispatch-wait
budget. When a tuple's verdict doesn't land in this window we skip it
(logged as `poll_timeout`, auto-retried on the next sweep run by the
resume classifier — the server keeps the attempt pending and the
stale-marker / dispatcher recovery thread will eventually re-poll it
from the SPA's perspective).

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
    PROVIDERS, Provider,
    _looks_like,
    build_code_template_block, build_hints_block,
    build_optimization_directive,
    call_cost_usd, estimate_code_tokens,
    compute_trace_hash, fetch_prompt,
    find_existing_submission, http_json,
    make_provider,
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


def _cache_key_filename(tuple_key: str) -> str:
    """Stable 16-hex-char filename for a tuple_key. Collision risk in the
    16-char prefix is negligible at any realistic sweep size."""
    h = hashlib.sha256(tuple_key.encode("utf-8")).hexdigest()
    return f"{h[:16]}.json"


def _save_cached_code(output_path: Path, tuple_key: str,
                      code: str, trace: dict) -> None:
    """Persist the freshly-generated code for a tuple so the next resume
    can skip the LLM call. Atomic via tmp + rename. Silent on failure —
    a cache write loss costs an LLM call but never blocks the sweep."""
    cdir = _cache_dir(output_path)
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        path = cdir / _cache_key_filename(tuple_key)
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
                      tuple_key: str) -> tuple[str, dict] | None:
    """Return (code, trace) from the cache for this tuple_key, or None
    when no usable cache entry exists. A malformed cache file is treated
    as a miss — we never want a bad cache to block a sweep."""
    cdir = _cache_dir(output_path)
    path = cdir / _cache_key_filename(tuple_key)
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


def _drop_cached_code(output_path: Path, tuple_key: str) -> None:
    """Delete the cached code for a tuple — called when a verdict has
    landed and the code is no longer needed."""
    cdir = _cache_dir(output_path)
    path = cdir / _cache_key_filename(tuple_key)
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        print(f"  ⚠ cache delete failed for {tuple_key!r}: {e}",
              file=sys.stderr)


def _reconcile_progress(progress: dict, base: str, api_key: str | None) -> dict:
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

    alive: dict[int, set[str]] = {}    # sub_id → set of slugs still attempted
    gone: set[int] = set()

    for sid in ids:
        url = f"{base.rstrip('/')}/api/submissions/{sid}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                gone.add(sid)
                continue
            print(f"  WARN: GET /submissions/{sid} → HTTP {e.code}; "
                  f"keeping its progress entries", file=sys.stderr)
            alive[sid] = None      # sentinel: unknown coverage, skip slug pruning
            continue
        except (urllib.error.URLError, OSError) as e:
            print(f"  WARN: GET /submissions/{sid} → {e}; "
                  f"keeping its progress entries", file=sys.stderr)
            alive[sid] = None
            continue
        # Per-attempt granularity: collect the slugs the submission still
        # has a record for. The /api/submissions/{id} response carries one
        # row per (attempt, run) pair under "runs", each keyed by
        # problem_slug — so a deleted attempt simply has no row here.
        runs_in = data.get("runs") or []
        slugs = {r.get("problem_slug") for r in runs_in if r.get("problem_slug")}
        alive[sid] = slugs

    if not gone and all(v is None for v in alive.values()):
        print(f"    nothing to prune (all submissions still present)")
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
        if needs_template and not snippets.get(language):
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
        cached = _load_cached_code(self._args.output, _tuple_key(
            model, prompt["name"], language, slug))
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
                    model, prompt["name"], language, slug))
        extras = {
            "{{optimization_directive}}": build_optimization_directive(self._args.target),
            "{{hints_block}}":            build_hints_block(self._args.hints, problem.get("hints")),
            "{{code_template}}":          build_code_template_block(snippets, language),
            "{{round_instruction}}":      "",
            "{{rounds_history}}":         "",
        }
        try:
            code, trace = self._provider.generate(
                model, problem["content"], slug,
                {"temperature": self._args.temperature}, language,
                registry_prompt=prompt, extras=extras,
            )
        except SystemExit as e:
            # Keep the historical "ollama_err" status label so resume-log
            # filters still work; the provider name is in the error string
            # for triage.
            return _Prefetched(tup=tup, problem=problem,
                               status="ollama_err",
                               err=f"[{self._provider.name}] {e}")
        if not _looks_like(code, language):
            # Wrong-language output: don't cache — re-generating may
            # give us a clean attempt next time. Caching the broken
            # code would just preserve the bug across resumes.
            return _Prefetched(tup=tup, problem=problem, code=code, trace=trace,
                               status="wrong_language")
        # SUCCESS — persist for re-use on the next resume, in case this
        # tuple's LC dispatch fails to land a verdict and the operator
        # restarts the sweep.
        _save_cached_code(self._args.output,
                          _tuple_key(model, prompt["name"], language, slug),
                          code, trace)
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
    if not arg:
        return sorted(LANGUAGE_DISPLAY)
    out = [l.strip() for l in arg.split(",") if l.strip()]
    bad = [l for l in out if l not in LANGUAGE_DISPLAY]
    if bad:
        raise SystemExit(f"unsupported languages: {bad}. "
                         f"Supported: {sorted(LANGUAGE_DISPLAY)}")
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
    ap.add_argument("--temperature", type=float, default=1)

    # Matrix subsetting
    ap.add_argument("--prompts",   default=None, metavar="NAME[,NAME]",
                    help="comma-separated registry prompt names. Default: all.")
    ap.add_argument("--languages", default=None, metavar="SLUG[,SLUG]",
                    help="comma-separated language slugs. Default: all 14 supported.")
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
    ap.add_argument("--hints",     action="store_true",
                    help="if a prompt uses {{hints_block}}, include LeetCode hints.")
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
    ap.add_argument("--poll-seconds", type=int, default=300, metavar="SEC",
                    help="how long to wait for each tuple's dispatch to settle "
                         "before giving up and moving to the next tuple. On "
                         "timeout the tuple is logged as `status=poll_timeout` "
                         "(auto-retried by the resume classifier next run; no "
                         "operator action required) and the sweep continues. "
                         "Default 300 s (5 min) — long enough for one LC "
                         "verdict + the dispatcher's 5.5-min retry budget to "
                         "overlap, short enough that a wedged dispatch doesn't "
                         "park the sweep on one cell forever.")
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
    ap.add_argument("--retry-failed", action="store_true",
                    help="also re-attempt previously-failed tuples (default: skip them).")
    ap.add_argument("--no-reconcile-progress", dest="reconcile_progress",
                    action="store_false", default=True,
                    help="skip the startup reconcile that drops progress-log "
                         "entries pointing at deleted submissions/attempts. "
                         "Default: enabled — keeps resume honest after a "
                         "server-side delete. Pass this when running offline.")
    ap.add_argument("--prefetch", type=int, default=1, metavar="N",
                    help="how many tuples ahead the background generator may "
                         "stay. With --prefetch 1 (default), while main is "
                         "paying the inter-problem cooldown and waiting on a "
                         "verdict, the model is already producing the next "
                         "tuple's code — turning the sleep into useful work. "
                         "0 disables the pipeline (sequential, like before).")
    ap.add_argument("--dry-run", action="store_true",
                    help="enumerate the matrix and report; no model calls, no submits.")
    args = ap.parse_args()
    # --correctness-oracle is required UNLESS --skip-correctness. Skipping sends
    # no oracle, so the dispatcher's correctness gate stays off for the run.
    if args.skip_correctness:
        args.correctness_oracle = None
    elif not args.correctness_oracle:
        ap.error("--correctness-oracle is required unless --skip-correctness is passed")

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
        progress = _reconcile_progress(progress, base, args.api_key)
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
    est_seconds = (
        len(todo) * 12 * repeats * rounds_factor
        + same_pair_hops * args.inter_tuple_delay
        + problem_hops * args.inter_problem_delay
    )
    print(f"\nestimated wall time: ~{est_seconds / 3600:.1f}h (worst case if every cell uses all {rounds_factor} round(s))")
    print(f"  REPEATS={repeats}  ·  rounds={args.rounds}  ·  inter-tuple={args.inter_tuple_delay}s  "
          f"·  inter-problem={args.inter_problem_delay}s")
    print(f"  (problem,language) hops: {problem_hops}  ·  "
          f"same-pair hops (model/prompt only): {same_pair_hops}")
    print(f"  (server INTER_DELAY={inter_delay_server}s also applies WITHIN "
          f"one submission's dispatch.)")
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
        print(f"\n[{i}/{len(todo)} · t+{elapsed_h:.2f}h] "
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
        if item.status == "ollama_err":
            print(f"  !! ollama failed: {item.err} (no LC contact, no delay)")
            progress[key] = {"status": "failed", "reason": f"ollama: {item.err}",
                             "at": _now_iso()}
            _save_progress(args.output, progress)
            n_fail += 1
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
            wait_s, reason = _pacing_wait_until_account_ready(
                base, args.api_key,
                floor_seconds=floor, ceiling_seconds=ceiling,
            )
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
        prefailed_reason = "wrong_language_extracted" if item.status == "wrong_language" else None
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

            # ---- Build the attempt body ----
            summary = (trace.get("reasoning") or trace.get("raw_response")
                       or "matrix sweep — see structured trace").strip()[:4000] or "x"
            if prefailed_reason and len(summary) < 20:
                summary = (f"client flagged {prefailed_reason}; raw response is "
                           "in trace.raw_response")
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

            # ---- Submit (create cell submission on first round/cell, append after) ----
            try:
                if sub_id is None:
                    created = http_json(
                        "POST", f"{base}/api/submissions", token=args.api_key,
                        body={
                            "model_name": model,
                            "model_version": args.model_version,
                            "language": language,
                            "prompt_template": prompt["system_prompt"],
                            "prompt_id": prompt["id"],
                            "model_params": {"temperature": args.temperature},
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

            # ---- Poll for THIS round's verdict ----
            att = _poll_for_attempt(base, args.api_key, sub_id, slug,
                                    round_attempt_idx=round_idx,
                                    timeout_s=args.poll_seconds)
            if att is None:
                print(f"  ⚠ poll timeout after {args.poll_seconds}s on round {round_idx}; "
                      f"recording as in-progress")
                progress[key] = {"status": "poll_timeout",
                                 "submission_id": sub_id,
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
        # forever-stale entries.
        _drop_cached_code(args.output, key)
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
