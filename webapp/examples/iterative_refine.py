#!/usr/bin/env python3
"""Iterative-refinement PerfArena client.

Runs an N-round refinement loop on one problem (or a small set):

  Round 0: cold-start (no prior feedback).
  Round 1..N-1: the prior round's LeetCode-judge result is folded back into
    the prompt so the model can fix failures or optimize for the target metric.

Three orthogonal experiment knobs, all set at the submission level:

  --n-rounds N           Total rounds in the chain (default 3; 1 = single-shot)
  --hints                Include LeetCode's editor hints in the prompt
  --target latency|memory   Steer the model toward a specific perf metric

Talks to the platform's normal endpoints — no special backend code path. The
unified prompt template `experiment-system` (seeded by app.main on startup)
is fetched once from /api/prompts; this script expands its four placeholders
per round:

  {{optimization_directive}}   empty / latency-steer / memory-steer
  {{round_instruction}}        "This is round K of N — apply the priorities…"
  {{hints_block}}              empty / "HINTS: …"
  {{rounds_history}}           empty / formatted previous rounds' feedback

The chain is recorded server-side via Attempt.previous_attempt_id, so a
viewer can walk the lineage on the attempts page.

Validate without burning quota:
  python iterative_refine.py --dry-run --n-rounds 3 --hints --target latency \\
      --slug two-sum
  # Prints the expanded prompts for every round without calling Ollama or
  # posting to PerfArena.

Full end-to-end run:
  export ARENA_API_KEY=arena_xxxxxxxx
  python iterative_refine.py --slug two-sum --model qwen2.5-coder:7b \\
      --n-rounds 3 --hints --target latency
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Re-use the heavy lifting from the existing client. Both scripts live in
# webapp/examples, so this import works without path tricks.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from submit_with_local_model import (                          # noqa: E402
    CANNED_CODE, CANNED_LANGUAGE, CANNED_SLUG,
    LANGUAGE_DISPLAY,
    PROVIDERS, Provider,
    _canned_trace, _expand_template, _now,
    build_hints_block, build_optimization_directive,            # shared
    call_cost_usd, estimate_code_tokens,                        # cost/token accounting
    compute_trace_hash, fetch_prompt, http_json, make_provider,
    sign_leaf,
)


# ---------- round-specific placeholder builder ------------------------------
# Most placeholder builders (optimization_directive, hints_block,
# code_template) live in submit_with_local_model.py — they're identical
# for single-shot and refinement runs. The only one unique to refinement
# is round_instruction, since single-shot has n_rounds=1 by definition.

def build_round_instruction(round_idx: int, total: int) -> str:
    if total <= 1:
        return ""
    if round_idx == 0:
        return (
            f"This is round 1 of {total}. You will attempt this problem again "
            f"with feedback from the judge after each round; use this round to "
            f"establish a baseline."
        )
    return (
        f"This is round {round_idx + 1} of {total}. You have the LeetCode "
        f"judge's verdict on every previous round below — use it to improve. "
        f"Improvement priorities, in order:\n"
        f"  1. If the most recent attempt was NOT Accepted, fix the root cause.\n"
        f"  2. If it was Accepted but slow, reduce runtime.\n"
        f"  3. If it was Accepted but memory-heavy, eliminate allocations.\n"
        f"  4. If it was already fast and lean, try a fundamentally different "
        f"approach — exploration is welcome."
    )


def format_round_feedback(round_idx: int, code: str, judge: dict,
                          target: str | None, language_fence: str) -> str:
    """Turn one attempt's result into the model-facing feedback block."""
    status = judge.get("status", "Pending")
    runtime = judge.get("runtime_ms")
    memory = judge.get("memory_mb")
    rt_pct = judge.get("runtime_percentile")
    mem_pct = judge.get("memory_percentile")
    fail_input = judge.get("fail_input")
    fail_expected = judge.get("fail_expected")
    fail_got = judge.get("fail_got")
    fail_msg = judge.get("fail_message")

    lines: list[str] = [f"────── Round {round_idx} ──────"]
    lines.append(f"```{language_fence}")
    lines.append(code.rstrip())
    lines.append("```")
    if status == "Accepted":
        # Order metrics by target so the target one is visually first.
        rt_line = f"  ▸ RUNTIME: {runtime} ms"
        if rt_pct is not None:
            rt_line += f" — LeetCode {rt_pct:.0f}th percentile"
        mem_line = f"  ▸ MEMORY:  {memory} MB"
        if mem_pct is not None:
            mem_line += f" — LeetCode {mem_pct:.0f}th percentile"
        if target == "memory":
            lines.append(f"VERDICT: Accepted")
            lines.append(mem_line.replace("MEMORY: ", "MEMORY: "))
            lines.append(rt_line.lower().replace("runtime: ", "runtime: "))
            lines.append(
                "→ Memory is the priority. Look for in-place edits, smaller "
                "data types, or eliminated auxiliary structures."
            )
        else:
            lines.append(f"VERDICT: Accepted")
            lines.append(rt_line)
            lines.append(mem_line)
            if target == "latency":
                lines.append(
                    "→ Runtime is the priority. Find a faster algorithm or "
                    "data structure."
                )
    else:
        lines.append(f"VERDICT: {status}")
        if fail_msg:
            lines.append(f"  error: {fail_msg}")
        if fail_input:
            lines.append(f"  input:    {fail_input}")
        if fail_expected is not None:
            lines.append(f"  expected: {fail_expected}")
        if fail_got is not None:
            lines.append(f"  got:      {fail_got}")
    return "\n".join(lines)


def build_rounds_history(history: list[dict], target: str | None,
                         language_fence: str) -> str:
    if not history:
        return ""
    blocks = [
        format_round_feedback(h["round_index"], h["code"], h["judge"],
                              target, language_fence)
        for h in history
    ]
    header = "PREVIOUS ROUNDS — LEETCODE JUDGE FEEDBACK"
    return header + "\n\n" + "\n\n".join(blocks)


# ---------- judge-result distiller ------------------------------------------

def distill_judge_result(attempt_detail: dict) -> dict:
    """Pull the fields format_round_feedback needs out of /api/submissions/{id}/
    attempts response. Picks the per-attempt aggregate (mean across the 5
    dispatch runs) when accepted, the last run's error log otherwise."""
    j: dict = {
        "status": attempt_detail.get("status", "Pending"),
        "accepted": bool(attempt_detail.get("accepted")),
    }
    if attempt_detail.get("accepted"):
        j["runtime_ms"] = attempt_detail.get("avg_runtime_ms")
        j["memory_mb"] = attempt_detail.get("avg_memory_mb")
        j["runtime_percentile"] = attempt_detail.get("avg_runtime_percentile")
        j["memory_percentile"] = attempt_detail.get("avg_memory_percentile")
    else:
        # Find the last run with an error log
        for r in reversed(attempt_detail.get("runs") or []):
            log = r.get("error_log")
            if not log:
                continue
            j["fail_message"] = log.get("error_message") or log.get("raw_stderr")
            j["fail_input"] = log.get("last_testcase")
            j["fail_expected"] = log.get("expected_output")
            j["fail_got"] = log.get("code_output")
            break
    return j


# ---------- one full N-round run for ONE problem ----------------------------

def run_chain(args, base: str, prompt: dict, problem: dict,
              provider: Provider | None = None) -> dict:
    """Drive the full N-round chain for `problem`. Returns a small summary."""
    slug = problem["title_slug"]
    language = args.language
    _, fence = LANGUAGE_DISPLAY[language]

    history: list[dict] = []
    submission_id: int | None = None
    previous_attempt_id: int | None = None
    # Probe the provider for the model identity digest once (skipped on
    # canned/dry). For Ollama this reads /api/tags; for OpenAI it issues
    # a 1-token probe to read system_fingerprint. The provider was built
    # in main() and passed in so the same client state is reused.
    model_digest = None
    if not args.no_model and not args.dry_run and provider is not None:
        model_digest = provider.model_digest(args.model)

    summary_rows: list[dict] = []
    for round_idx in range(args.n_rounds):
        # --- expand the four placeholders ---
        opt = build_optimization_directive(args.target)
        ri = build_round_instruction(round_idx, args.n_rounds)
        hb = build_hints_block(args.hints, problem.get("hints"))
        rh = build_rounds_history(history, args.target, fence)

        sys_p = _expand_template(prompt["system_prompt"], slug=slug,
                                 statement=problem["content"], language=language)
        user_p = _expand_template(prompt["user_prompt_template"], slug=slug,
                                  statement=problem["content"], language=language)
        # The five extras aren't part of _expand_template's standard repl set,
        # so do them here. {{code_template}} is the LeetCode-provided starter
        # snippet for this (problem, language); empty string when the prompt
        # doesn't use it or when the snippet is missing (run
        # `python -m app.fetch_code_snippets` to populate).
        snippets = (problem.get("code_snippets") or {})
        code_template = snippets.get(language, "")
        if "{{code_template}}" in (prompt["user_prompt_template"]
                                   + prompt["system_prompt"]):
            if not code_template:
                print(f"  warning: prompt uses {{{{code_template}}}} but no "
                      f"snippet for {slug}/{language} — run "
                      f"`python -m app.fetch_code_snippets --sampled-only`",
                      file=sys.stderr)
        for k, v in (
            ("{{optimization_directive}}", opt),
            ("{{round_instruction}}", ri),
            ("{{hints_block}}", hb),
            ("{{rounds_history}}", rh),
            ("{{code_template}}", code_template),
        ):
            sys_p = sys_p.replace(k, v)
            user_p = user_p.replace(k, v)

        if args.dry_run:
            print(f"\n=== ROUND {round_idx} (dry-run) ===")
            print("--- SYSTEM ---")
            print(sys_p)
            print("--- USER ---")
            print(user_p)
            # Synthesize a fake "Accepted" so subsequent rounds can render
            # the feedback block as a smoke test of that code path.
            history.append({
                "round_index": round_idx, "code": "(no code in dry-run)",
                "judge": {"status": "Accepted", "accepted": True,
                          "runtime_ms": 100 + round_idx * 10,
                          "memory_mb": 15.0,
                          "runtime_percentile": 50.0 - round_idx * 5,
                          "memory_percentile": 55.0},
            })
            continue

        # --- generate code ---
        if args.no_model:
            if slug != CANNED_SLUG or language != CANNED_LANGUAGE:
                raise SystemExit(
                    f"--no-model only works for {CANNED_SLUG} in "
                    f"{CANNED_LANGUAGE}; supply --model + --ollama-url for "
                    f"other problems."
                )
            code, trace = _canned_trace(slug, language,
                                        {"temperature": args.temperature},
                                        reason="--no-model")
            # Stuff the expanded prompt back into the trace for auditability.
            trace["prompt"] = user_p
            trace["system_prompt"] = sys_p
        else:
            # Re-use the active provider with our pre-expanded prompts.
            # Build a fake "prompt" dict the provider's generate() sees as
            # a registry prompt with all the {{placeholders}} ALREADY
            # substituted, so generate() doesn't re-expand them.
            fake_prompt = {
                "system_prompt": sys_p,
                "user_prompt_template": user_p,
            }
            assert provider is not None  # main() guarantees this in the not-no-model branch
            code, trace = provider.generate(
                args.model, problem["content"], slug,
                {"temperature": args.temperature}, language,
                registry_prompt=fake_prompt,
            )

        # --- submit ---
        summary = (trace.get("reasoning") or trace.get("raw_response")
                   or "model produced solution; see structured trace").strip()
        summary = summary[:4000] or "solution generated by model"
        leaf = compute_trace_hash(code, summary, trace)
        attempt_body: dict = {
            "title_slug": slug, "code": code, "inference_trace": summary,
            "trace": trace, "round_index": round_idx,
            "cost_usd": call_cost_usd(trace),
            "response_tokens": estimate_code_tokens(code),
            "wall_clock_ms": trace.get("wall_clock_ms"),
            "generation_energy_j": trace.get("energy_j"),
        }
        if model_digest:
            attempt_body["model_digest"] = model_digest
        if args.sign_key:
            signed = sign_leaf(args.sign_key, leaf)
            if signed:
                attempt_body["trace_sig"], attempt_body["trace_signer"] = signed
        if previous_attempt_id is not None:
            attempt_body["previous_attempt_id"] = previous_attempt_id

        if submission_id is None:
            # First round → create the submission with all knobs declared
            # up-front. The server enforces n_rounds + records the knobs.
            created = http_json(
                "POST", f"{base}/api/submissions",
                token=args.api_key,
                body={
                    "model_name": args.model,
                    "model_version": args.model_version,
                    "language": language,
                    "prompt_template": prompt["system_prompt"],
                    "prompt_id": prompt["id"],
                    "model_params": {"temperature": args.temperature},
                    "n_samples_per_problem": 1,
                    "n_rounds": args.n_rounds,
                    "include_hints": bool(args.hints),
                    "optimization_target": args.target,
                    # Scope to the chosen competition so pass@k denominators
                    # match the competition's size rather than the full DB.
                    "competition_slug": args.competition,
                    "attempts": [attempt_body],
                },
            )
            submission_id = created["submission_id"]
            print(f"  round {round_idx}: submission #{submission_id} created")
        else:
            resp = http_json(
                "POST", f"{base}/api/submissions/{submission_id}/attempts",
                token=args.api_key,
                body={"attempts": [attempt_body]},
            )
            print(f"  round {round_idx}: appended attempt to #{submission_id}")

        # --- poll for judge result ---
        attempt_id = _poll_for_attempt(base, submission_id, slug, round_idx,
                                       args.poll_seconds)
        attempt_detail = _find_attempt_detail(base, submission_id, attempt_id)
        if attempt_detail is None:
            raise SystemExit(
                f"could not find judged attempt for round {round_idx} "
                f"(submission #{submission_id}, slug {slug})"
            )
        previous_attempt_id = attempt_detail["attempt_id"]
        judge = distill_judge_result(attempt_detail)
        history.append({
            "round_index": round_idx, "code": code, "judge": judge,
        })
        summary_rows.append({
            "round": round_idx, "status": judge["status"],
            "runtime_ms": judge.get("runtime_ms"),
            "memory_mb": judge.get("memory_mb"),
            "rt_pct": judge.get("runtime_percentile"),
            "mem_pct": judge.get("memory_percentile"),
            "attempt_id": previous_attempt_id,
        })
        tail = ""
        if judge.get("accepted"):
            tail = f" · rt={judge.get('runtime_ms')}ms · mem={judge.get('memory_mb')}MB"
        print(f"  round {round_idx} → {judge['status']}{tail}")

    return {
        "submission_id": submission_id,
        "slug": slug,
        "rounds": summary_rows,
    }


def _poll_for_attempt(base: str, sub_id: int, slug: str, round_idx: int,
                      poll_seconds: int) -> int:
    """Block until the round-`round_idx` attempt for `slug` is judged. Returns
    its attempt_id. The platform's 5×-dispatch usually finishes within ~30s."""
    deadline = time.time() + poll_seconds
    last_status = "?"
    while time.time() < deadline:
        atts = http_json("GET", f"{base}/api/submissions/{sub_id}/attempts")
        for a in atts:
            if a["problem_slug"] != slug:
                continue
            if a.get("round_index", 0) != round_idx:
                continue
            if a["status"] != "queued" and a["n_runs"] > 0:
                return a["attempt_id"]
            last_status = a["status"]
        time.sleep(2)
    raise SystemExit(
        f"timed out after {poll_seconds}s waiting for round {round_idx} of "
        f"{slug}; last status seen: {last_status}"
    )


def _find_attempt_detail(base: str, sub_id: int, attempt_id: int) -> dict | None:
    atts = http_json("GET", f"{base}/api/submissions/{sub_id}/attempts")
    for a in atts:
        if a["attempt_id"] == attempt_id:
            return a
    return None


# ---------- main ------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Iterative-refinement client for PerfArena.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__,
    )
    ap.add_argument("--base-url", default=os.environ.get("ARENA_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.environ.get("ARENA_API_KEY"))
    ap.add_argument("--competition", default="leetcode-99-perf", metavar="SLUG",
                    help="competition the submission targets (default: "
                         "leetcode-99-perf). --slug must belong to it.")
    ap.add_argument("--slug", default="two-sum",
                    help="problem slug to refine (one chain)")
    ap.add_argument("--provider", default="ollama", choices=sorted(PROVIDERS),
                    help="LLM backend. ollama (default) uses --ollama-url; "
                         "openai uses --openai-base-url + --openai-api-key.")
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    ap.add_argument("--openai-base-url",
                    default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                    help="OpenAI-compatible /v1 endpoint.")
    ap.add_argument("--openai-api-key", default=os.environ.get("OPENAI_API_KEY"),
                    help="OpenAI API key (or set OPENAI_API_KEY env var).")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--model-version", default=None,
                    help="version tag stamped on every submission. "
                         "Defaults to the provider name when omitted.")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--language", default="python3",
                    choices=sorted(LANGUAGE_DISPLAY))
    ap.add_argument("--no-model", action="store_true",
                    help="skip Ollama; uses the canned solution (only valid "
                         f"with --slug {CANNED_SLUG} --language {CANNED_LANGUAGE})")
    ap.add_argument("--n-rounds", type=int, default=3, choices=range(1, 11),
                    help="rounds in the refinement chain (1..10; default 3)")
    ap.add_argument("--hints", action="store_true",
                    help="include LeetCode's editor hints in the prompt")
    ap.add_argument("--target", choices=("latency", "memory"), default=None,
                    help="steer the model to optimize for runtime or memory")
    ap.add_argument("--prompt-name", default="experiment-system",
                    help="registry prompt to use (default: the unified "
                         "iterative_refine template seeded by app.main)")
    ap.add_argument("--sign-key", default=None,
                    help="Ed25519 PEM private key to sign each round's trace")
    ap.add_argument("--poll-seconds", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the expanded prompt per round; no Ollama, no submit")
    args = ap.parse_args()

    if not args.dry_run and not args.api_key:
        raise SystemExit("set --api-key or ARENA_API_KEY (or use --dry-run)")
    base = args.base_url.rstrip("/")

    if args.dry_run:
        # Use a stub problem so we can render hints/feedback without hitting the API.
        problem = {
            "title_slug": args.slug,
            "content": (
                "(dry-run stub statement) Given an integer array `nums` and "
                "an integer `target`, return indices of the two numbers such "
                "that they add up to target."
            ),
            "hints": (
                '["A really brute force way would be to search for all '
                'possible pairs of numbers but that would be too slow.", '
                '"Use a hash map to remember what value you have seen so far."]'
            ),
            "code_snippets": {
                "python3": (
                    "class Solution:\n"
                    "    def twoSum(self, nums: List[int], target: int) -> List[int]:\n"
                    "        "
                ),
                "cpp": (
                    "class Solution {\npublic:\n"
                    "    vector<int> twoSum(vector<int>& nums, int target) {\n"
                    "        \n    }\n};"
                ),
            },
        }
        prompt = fetch_prompt_or_stub(base, args.prompt_name)
    else:
        problem = http_json("GET", f"{base}/api/problems/{args.slug}")
        prompt = fetch_prompt(base, args.prompt_name)

    print(f"• problem '{args.slug}' · language={args.language} · "
          f"n_rounds={args.n_rounds} · hints={args.hints} · "
          f"target={args.target or 'none'}")
    if not args.dry_run:
        print(f"• using registry prompt '{prompt['name']}' (id={prompt['id']})")

    # Build the LLM backend once for the whole chain. Skipped when
    # --no-model / --dry-run since those paths don't hit the provider.
    provider: Provider | None = None
    if not args.no_model and not args.dry_run:
        provider = make_provider(args)
        print(f"• provider={provider.name}")
    if not args.model_version:
        args.model_version = provider.name if provider else "ollama"

    result = run_chain(args, base, prompt, problem, provider=provider)

    if args.dry_run:
        print("\n=== dry-run complete — no submissions made ===")
        return

    print("\n=== chain complete ===")
    print(f"submission #{result['submission_id']}  · slug {result['slug']}")
    print(f"  {'rnd':>3}  {'status':<14}  {'rt(ms)':>7}  {'mem(MB)':>8}  "
          f"{'rt%':>4}  {'mem%':>4}  attempt_id")
    for r in result["rounds"]:
        rt = r["runtime_ms"]; mem = r["memory_mb"]
        rt_p = r["rt_pct"]; mem_p = r["mem_pct"]
        rt_s = f"{rt:>7.1f}" if rt is not None else "       "
        mem_s = f"{mem:>8.2f}" if mem is not None else "        "
        rtp_s = f"{rt_p:>4.0f}" if rt_p is not None else "    "
        mp_s = f"{mem_p:>4.0f}" if mem_p is not None else "    "
        print(f"  {r['round']:>3}  {r['status']:<14}  "
              f"{rt_s}  {mem_s}  {rtp_s}  {mp_s}  {r['attempt_id']}")


def fetch_prompt_or_stub(base: str, name: str) -> dict:
    """Dry-run fallback: if the API isn't running, use a stub matching the
    seeded `experiment-system` template so we can exercise expansion alone."""
    try:
        return fetch_prompt(base, name)
    except SystemExit:
        return {
            "id": -1, "name": "experiment-system (stub)",
            "system_prompt": (
                "You are a competitive-programming assistant. Solve the problem "
                "in {{language_display}} using LeetCode's standard `Solution` "
                "shape.\n\n{{optimization_directive}}\n\n{{round_instruction}}\n\n"
                "Think step by step, then return the final solution in a single "
                "```{{language_fence}} code block."
            ),
            "user_prompt_template": (
                "Solve this LeetCode problem `{{slug}}` in {{language_display}}.\n\n"
                "{{statement}}\n\n{{hints_block}}\n\n{{rounds_history}}"
            ),
        }


if __name__ == "__main__":
    main()
