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
  * `--inter-problem-delay` (default 60 s) — when the (problem, language)
    pair changes. Long.
  * `ARENA_DISPATCH_INTER_DELAY` on the backend (default 60 s) — gap
    between attempts of different problems WITHIN one dispatch_submission
    call. Enforced server-side, additive with the above when applicable.

`--poll-seconds` (default 600 s = 10 min) is the timeout for each tuple's
dispatch to land in the DB before we move on.

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
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

# Reuse all the codegen + submission plumbing from the single-shot script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from submit_with_local_model import (                          # noqa: E402
    LANGUAGE_DISPLAY,
    _looks_like,
    build_code_template_block, build_hints_block,
    build_optimization_directive,
    call_cost_usd, estimate_code_tokens,
    call_ollama, compute_trace_hash, fetch_prompt,
    find_existing_submission, http_json,
    ollama_model_digest,
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


def _should_skip(progress: dict, key: str, retry_failed: bool) -> bool:
    rec = progress.get(key)
    if rec is None:
        return False
    status = rec.get("status")
    if status == "complete":
        return True
    if status == "skipped":
        return True       # skipped reasons are permanent (no snippet, etc.)
    if status == "failed":
        return not retry_failed
    return False          # in-progress / unknown → retry


def _poll_for_attempt(base: str, api_key: str, sub_id: int, slug: str,
                      round_attempt_idx: int, timeout_s: int) -> dict | None:
    """Block until the round-`round_attempt_idx` attempt for `slug` in
    submission `sub_id` has its dispatch settled (n_runs > 0). Returns the
    attempt dict or None on timeout.

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
            if best.get("n_runs", 0) > 0:
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
                 cell_key_fn, depth: int):
        self._todo = todo
        self._args = args
        self._base = base
        self._cell_covered = cell_covered
        self._cell_key_fn = cell_key_fn
        self._depth = depth
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
        extras = {
            "{{optimization_directive}}": build_optimization_directive(self._args.target),
            "{{hints_block}}":            build_hints_block(self._args.hints, problem.get("hints")),
            "{{code_template}}":          build_code_template_block(snippets, language),
            "{{round_instruction}}":      "",
            "{{rounds_history}}":         "",
        }
        try:
            code, trace = call_ollama(
                self._args.ollama_url, model, problem["content"], slug,
                {"temperature": self._args.temperature}, language,
                registry_prompt=prompt, extras=extras,
            )
        except SystemExit as e:
            return _Prefetched(tup=tup, problem=problem,
                               status="ollama_err", err=str(e))
        if not _looks_like(code, language):
            return _Prefetched(tup=tup, problem=problem, code=code, trace=trace,
                               status="wrong_language")
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


def _resolve_slugs(base: str, arg: str | None) -> list[str]:
    if arg:
        return [s.strip() for s in arg.split(",") if s.strip()]
    # Default: every problem currently seeded in the system.
    return [p["title_slug"] for p in http_json("GET", f"{base}/api/problems")]


# ---------- main loop -------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Matrix sweep over (prompt × language × problem).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__,
    )
    ap.add_argument("--base-url",  default=os.environ.get("ARENA_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--api-key",   default=os.environ.get("ARENA_API_KEY"))
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    ap.add_argument("--model",        default="qwen2.5-coder:7b",
                    help="single model name (used when --models isn't given).")
    ap.add_argument("--models", default=None, metavar="NAME[,NAME]",
                    help="comma-separated model names to sweep, all served by "
                         "the SAME --ollama-url (Ollama selects the model per "
                         "request by name). Adds a model axis to the matrix; "
                         "each (model, prompt, language) is its own experiment "
                         "cell / submission. Overrides --model when set.")
    ap.add_argument("--model-version", default="ollama",
                    help="version tag stamped on every submission (shared "
                         "across all --models; the model NAME already "
                         "distinguishes cells).")
    ap.add_argument("--temperature", type=float, default=0.2)

    # Matrix subsetting
    ap.add_argument("--prompts",   default=None, metavar="NAME[,NAME]",
                    help="comma-separated registry prompt names. Default: all.")
    ap.add_argument("--languages", default=None, metavar="SLUG[,SLUG]",
                    help="comma-separated language slugs. Default: all 14 supported.")
    ap.add_argument("--slugs",     default=None, metavar="SLUG[,SLUG]",
                    help="comma-separated problem slugs. Default: every problem in the system.")
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
    ap.add_argument("--inter-problem-delay", type=float, default=60.0, metavar="SEC",
                    help="seconds applied when the (problem, language) pair "
                         "changes. Long by default to avoid LeetCode's "
                         "breadth-first anti-bot trigger. Stacks on top of "
                         "ARENA_DISPATCH_INTER_DELAY (which only applies "
                         "WITHIN one submission's dispatch).")
    ap.add_argument("--poll-seconds", type=int, default=600, metavar="SEC",
                    help="how long to wait for each tuple's dispatch to settle.")
    ap.add_argument("--output", type=Path,
                    default=Path("data/sweep_log.json"),
                    help="resumable progress log; rerunning skips completed tuples.")
    ap.add_argument("--retry-failed", action="store_true",
                    help="also re-attempt previously-failed tuples (default: skip them).")
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

    if not args.dry_run and not args.api_key:
        raise SystemExit("set --api-key or ARENA_API_KEY (skip with --dry-run)")
    base = args.base_url.rstrip("/")

    # 1. Resolve the matrix
    models = ([m.strip() for m in args.models.split(",") if m.strip()]
              if args.models else [args.model])
    prompts = _resolve_prompts(base, args.prompts)
    languages = _resolve_languages(args.languages)
    slugs = _resolve_slugs(base, args.slugs)
    print(f"matrix dimensions:")
    print(f"  models:    {len(models):>4} ({', '.join(models)})  [all via {args.ollama_url}]")
    print(f"  prompts:   {len(prompts):>4} ({', '.join(p['name'] for p in prompts)})")
    print(f"  languages: {len(languages):>4} ({', '.join(languages)})")
    print(f"  problems:  {len(slugs):>4}")
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
    todo = [t for t in all_tuples
            if not _should_skip(progress, _tuple_key(t[0], t[1]["name"], t[2], t[3]),
                                args.retry_failed)]
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
    repeats = int(os.environ.get("ARENA_DISPATCH_REPEATS", "5"))
    inter_delay_server = float(os.environ.get("ARENA_DISPATCH_INTER_DELAY", "60"))
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

    # 4. Pre-fetch each model's digest once (identity is per-model, served by
    # the same Ollama endpoint). Cached so we don't re-query per tuple.
    model_digests: dict[str, str | None] = {}
    for m in models:
        d = ollama_model_digest(args.ollama_url, m)
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
            n_rounds=1,
        )
        if existing is None:
            continue
        sid = existing["id"]
        cell_subs[cell] = sid
        # Snapshot which problems already have an attempt — those will be
        # skipped (k=1, so any second attempt would 400 anyway, and even
        # at higher k we wouldn't want duplicate work in a resumed sweep).
        atts = http_json("GET", f"{base}/api/submissions/{sid}/attempts",
                         token=args.api_key)
        covered = {a["problem_slug"] for a in atts}
        cell_covered[cell] = covered
        print(f"  auto-append: cell {cell!r} → submission #{sid} "
              f"({len(covered)} problems already attempted; will skip those)")

    # ---- Start the prefetcher (generates code in the background while main
    # is sleeping / dispatching / polling). With --prefetch 0 the same class
    # runs synchronously in the main thread for old-style sequential behavior.
    prefetcher = _Prefetcher(
        todo=todo, args=args, base=base,
        cell_covered=cell_covered, cell_key_fn=_cell_key,
        depth=args.prefetch,
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

        # (problem, language)-aware pacing. All --models share one LeetCode
        # account, so we keep them submitting to the SAME (problem, language)
        # back-to-back: switching only the model or prompt is a cheap
        # "iterate on this problem" hop (short delay). The long inter-problem
        # cooldown fires only when the (problem, language) pair itself changes,
        # which LeetCode treats as breadth-first scraping if done too fast.
        #
        # With --prefetch > 0, this sleep is exactly when the background
        # generator produces the NEXT tuple's code — the wait does useful work.
        pair = (slug, language)
        if prev_pair is not None:
            if pair != prev_pair:
                delay = args.inter_problem_delay
                kind = "problem/language hop"
            else:
                delay = args.inter_tuple_delay
                kind = "same problem+language (model/prompt change)"
            if delay > 0:
                tag = " (prefetcher generating next tuple)" if args.prefetch > 0 else ""
                print(f"\n  … sleeping {delay:.0f}s ({kind}){tag}", flush=True)
                time.sleep(delay)
        prev_pair = pair

        print(f"\n[{i}/{len(todo)} · t+{elapsed_h:.2f}h] "
              f"model={model:<16} prompt={prompt['name']:<22} "
              f"lang={language:<8} slug={slug}")

        # ---- Branch on prefetcher outcome ----
        # cell_covered may have advanced since the prefetcher checked, so we
        # also confirm here. The other status branches are terminal (recorded
        # to progress + counted as skip/fail) — generation already happened
        # (or was deliberately skipped) on the prefetcher thread.
        if item.status == "cell_covered" or slug in cell_covered.get(cell, set()):
            existing_sid = cell_subs.get(cell)
            print(f"  ↺ already in submission #{existing_sid}; skipping")
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
            print(f"  !! fetch problem failed: {item.err}")
            progress[key] = {"status": "failed",
                             "reason": f"fetch_problem: {item.err}",
                             "at": _now_iso()}
            _save_progress(args.output, progress)
            n_fail += 1
            continue
        if item.status == "no_code_snippet":
            print(f"  skip: prompt requires {{{{code_template}}}} but no "
                  f"{language!r} snippet for {slug}")
            progress[key] = {"status": "skipped",
                             "reason": "no_code_snippet_for_language",
                             "at": _now_iso()}
            _save_progress(args.output, progress)
            n_skip += 1
            continue
        if item.status == "ollama_err":
            print(f"  !! ollama failed: {item.err}")
            progress[key] = {"status": "failed", "reason": f"ollama: {item.err}",
                             "at": _now_iso()}
            _save_progress(args.output, progress)
            n_fail += 1
            continue
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
                    code, trace = call_ollama(
                        args.ollama_url, model,
                        (round_problem or {}).get("content", ""),
                        slug,
                        {"temperature": args.temperature}, language,
                        registry_prompt=prompt, extras=extras_r,
                    )
                except SystemExit as e:
                    print(f"  !! ollama failed at round {round_idx}: {e}")
                    progress[key] = {"status": "failed",
                                     "reason": f"ollama (round {round_idx}): {e}",
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
            attempt = {"title_slug": slug, "code": code,
                       "inference_trace": summary, "trace": trace,
                       "cost_usd": call_cost_usd(trace.get("provider")),
                       "response_tokens": estimate_code_tokens(code),
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
