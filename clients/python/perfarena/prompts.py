"""Prompt construction: placeholder builders, the LeetCode / CLBG problem
shapes, and PromptBuilder, which turns (prompt, problem, language, round
state) into the (system, user) pair sent to a model.

Every text below is copied verbatim from the client scripts
(submit_with_local_model.py, iterative_refine.py), and the parity tests check
that both produce identical prompts."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .languages import LANGUAGE_DISPLAY

PLACEHOLDERS = ("{{optimization_directive}}", "{{hints_block}}", "{{code_template}}",
                "{{round_instruction}}", "{{rounds_history}}")


def system_prompt(language: str) -> str:
    """The built-in system prompt used when no registry prompt is given."""
    display, _ = LANGUAGE_DISPLAY.get(language, (language, language))
    return (
        f"You are a competitive-programming assistant. Solve the problem in "
        f"{display} using LeetCode's standard `Solution` shape (the exact "
        f"class and method names LeetCode expects for the problem). Think "
        f"step by step, then return the final solution in a single ```"
        f"{LANGUAGE_DISPLAY.get(language, (display, language))[1]}"
        f" code block. Don't include any other comments or text outside the "
        f"code block."
    )


# ---------- problem shapes ------------------------------------------------------

def is_clbg_statement(statement: str) -> bool:
    """CLBG statements carry an `## Invocation` section; no LeetCode problem
    does."""
    return "## Invocation" in (statement or "")


CLBG_ARGV_HINT: dict[str, str] = {
    "python3": "sys.argv[1]", "cpp": "argv[1]", "c": "argv[1]",
    "rust": "std::env::args().nth(1)", "java": "args[0]", "csharp": "args[0]",
    "javascript": "process.argv[2]", "typescript": "process.argv[2]",
    "golang": "os.Args[1]", "kotlin": "args[0]", "swift": "CommandLine.arguments[1]",
    "scala": "args(0)", "ruby": "ARGV[0]", "php": "$argv[1]", "dart": "args[0]",
    "elixir": "hd(System.argv())",
    "erlang": "the first element of the Args list passed to main/1",
    "racket": "(vector-ref (current-command-line-arguments) 0)",
}

CLBG_JAVA_CLASS: dict[str, str] = {
    "binary-trees": "binarytrees", "fannkuch-redux": "fannkuchredux",
    "fasta": "fasta", "k-nucleotide": "knucleotide", "mandelbrot": "mandelbrot",
    "n-body": "nbody", "pidigits": "pidigits", "regex-redux": "regexredux",
    "reverse-complement": "revcomp", "spectral-norm": "spectralnorm",
}


def clbg_system_prompt(language: str, slug: str = "") -> str:
    display, fence = LANGUAGE_DISPLAY.get(language, (language, language))
    argv = CLBG_ARGV_HINT.get(language, "the first command-line argument")
    base = (
        f"You are an expert performance engineer. Write a COMPLETE, "
        f"self-contained {display} program that solves the benchmark below. "
        f"This is NOT a LeetCode problem: do NOT define a `class Solution` or "
        f"any method wrapper; write a whole program with a normal entry point.\n\n"
        f"INPUT/OUTPUT CONTRACT — follow it EXACTLY. The harness always invokes "
        f"the program as `<program> N`:\n"
        f"1. Read N as the FIRST command-line argument, i.e. `{argv}`, and parse "
        f"it as an integer. Your program must work for ANY N: do NOT hardcode N "
        f"or hardcode/precompute the output, and do NOT read N from standard input.\n"
        f"2. Read from standard input ONLY if the Input section below describes "
        f"an input stream (for example FASTA data on stdin); otherwise read "
        f"nothing from standard input.\n"
        f"3. Print the result to standard output EXACTLY as the Output section "
        f"shows: identical values, formatting, ordering, and whitespace, with no "
        f"labels, prompts, debug lines, or anything before or after.\n\n"
        f"Think step by step, then return the final program in a single "
        f"```{fence} code block with nothing else outside it."
    )
    cls = CLBG_JAVA_CLASS.get(slug) if language == "java" else None
    if cls:
        base += (
            f"\n\nJAVA ENTRY POINT — the harness runs `java {cls} N`, so your "
            f"main class MUST be named EXACTLY `{cls}`: declare "
            f"`public class {cls}` containing "
            f"`public static void main(String[] args)`. Any helper classes must "
            f"be non-public (Java allows one public top-level class per file). A "
            f"class named anything else fails with ClassNotFoundException."
        )
    return base


class ProblemShape:
    """How a problem kind adjusts the prompt pair (Strategy)."""

    def apply(self, sys_p: str, user_p: str, *, language: str, slug: str) -> tuple[str, str]:
        return sys_p, user_p


class LeetCodeShape(ProblemShape):
    """LeetCode problems keep the prompt as written."""


class ClbgShape(ProblemShape):
    """A CLBG benchmark is a whole program: swap the `Solution`-shape system
    prompt for the standalone-program contract."""

    def apply(self, sys_p, user_p, *, language, slug):
        return (clbg_system_prompt(language, slug),
                user_p.replace("Solve this LeetCode problem", "Solve this benchmark problem"))


def shape_for(statement: str) -> ProblemShape:
    return ClbgShape() if is_clbg_statement(statement) else LeetCodeShape()


# ---------- placeholder builders -------------------------------------------------

LATENCY_DIRECTIVE = (
    "Your PRIMARY priority is MINIMIZING RUNTIME. Pick the asymptotically-fastest "
    "algorithm even at the cost of moderate auxiliary memory. The LeetCode "
    "runtime percentile and absolute ms are how this submission will be "
    "evaluated. Correctness is non-negotiable — but among correct solutions, "
    "choose the fastest."
)
MEMORY_DIRECTIVE = (
    "Your PRIMARY priority is MINIMIZING MEMORY USAGE. Prefer in-place "
    "transformations, avoid auxiliary allocations, reuse buffers, and choose "
    "primitive types over boxed ones where the language allows. The LeetCode "
    "memory percentile and absolute MB are how this submission will be "
    "evaluated. Correctness is non-negotiable — but among correct solutions, "
    "choose the leanest."
)


def optimization_directive(target: str | None) -> str:
    return {"latency": LATENCY_DIRECTIVE, "memory": MEMORY_DIRECTIVE}.get(target or "", "")


def hints_block(include_hints: bool, hints: str | None) -> str:
    if not include_hints or not hints:
        return ""
    raw = hints.strip()
    items: list[str] = []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                items = [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            items = []
    if not items:
        items = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not items:
        items = [raw]
    body = "\n".join(f"  • {x}" for x in items)
    return ("HINTS (from the LeetCode editors — these may or may not match the "
            f"optimal approach):\n{body}")


def code_template_block(snippets: dict | None, language: str) -> str:
    return (snippets or {}).get(language, "") or ""


def round_instruction(round_idx: int, total: int) -> str:
    if total <= 1:
        return ""
    if round_idx == 0:
        return (
            f"This is round 1 of {total}. You will attempt this problem again "
            f"with feedback from the judge after each round; use this round to "
            f"establish a baseline."
        )
    return (
        f"This is round {round_idx + 1} of {total}. You have your PREVIOUS "
        f"attempt's code and the LeetCode judge's verdict on it below — use "
        f"them to improve. Improvement priorities, in order:\n"
        f"  1. If the previous attempt was NOT Accepted, fix the root cause.\n"
        f"  2. If it was Accepted but slow, reduce runtime.\n"
        f"  3. If it was Accepted but memory-heavy, eliminate allocations.\n"
        f"  4. If it was already fast and lean, try a fundamentally different "
        f"approach — exploration is welcome."
    )


def format_round_feedback(round_idx: int, code: str, judge: dict,
                          target: str | None, language_fence: str) -> str:
    status = judge.get("status", "Pending")
    runtime, memory = judge.get("runtime_ms"), judge.get("memory_mb")
    rt_pct, mem_pct = judge.get("runtime_percentile"), judge.get("memory_percentile")
    lines: list[str] = [f"────── Round {round_idx} ──────",
                        f"```{language_fence}", code.rstrip(), "```"]
    if status == "Accepted":
        rt_line = f"  ▸ RUNTIME: {runtime} ms"
        if rt_pct is not None:
            rt_line += f" — LeetCode {rt_pct:.0f}th percentile"
        mem_line = f"  ▸ MEMORY:  {memory} MB"
        if mem_pct is not None:
            mem_line += f" — LeetCode {mem_pct:.0f}th percentile"
        lines.append("VERDICT: Accepted")
        if target == "memory":
            lines += [mem_line, rt_line.lower(),
                      "→ Memory is the priority. Look for in-place edits, smaller "
                      "data types, or eliminated auxiliary structures."]
        else:
            lines += [rt_line, mem_line]
            if target == "latency":
                lines.append("→ Runtime is the priority. Find a faster algorithm or "
                             "data structure.")
    else:
        lines.append(f"VERDICT: {status}")
        if judge.get("fail_message"):
            lines.append(f"  error: {judge['fail_message']}")
        if judge.get("fail_input"):
            lines.append(f"  input:    {judge['fail_input']}")
        if judge.get("fail_expected") is not None:
            lines.append(f"  expected: {judge['fail_expected']}")
        if judge.get("fail_got") is not None:
            lines.append(f"  got:      {judge['fail_got']}")
    return "\n".join(lines)


def rounds_history(history: list[dict], target: str | None, language_fence: str) -> str:
    """Feedback block for the next round: ONLY the previous round's code and
    verdict (see iterative_refine.build_rounds_history for why)."""
    if not history:
        return ""
    last = history[-1]
    return ("PREVIOUS ATTEMPT — LEETCODE JUDGE FEEDBACK\n\n"
            + format_round_feedback(last["round_index"], last["code"], last["judge"],
                                    target, language_fence))


def distill_judge(attempt: dict) -> dict:
    """Judge fields for the feedback block from an attempt (the
    /api/attempts/{id} or /api/submissions/{id}/attempts shape)."""
    j: dict = {"status": attempt.get("verdict") or attempt.get("status", "Pending"),
               "accepted": bool(attempt.get("accepted"))}
    if attempt.get("accepted"):
        j.update(runtime_ms=attempt.get("avg_runtime_ms"),
                 memory_mb=attempt.get("avg_memory_mb"),
                 runtime_percentile=attempt.get("avg_runtime_percentile"),
                 memory_percentile=attempt.get("avg_memory_percentile"))
    else:
        for r in reversed(attempt.get("runs") or []):
            log = r.get("error_log")
            if not log:
                continue
            j.update(fail_message=log.get("error_message") or log.get("raw_stderr"),
                     fail_input=log.get("last_testcase"),
                     fail_expected=log.get("expected_output"),
                     fail_got=log.get("code_output"))
            break
    return j


def expand(text: str, *, slug: str, statement: str, language: str,
           extras: dict[str, str] | None = None) -> str:
    display, fence = LANGUAGE_DISPLAY.get(language, (language, language))
    repl = {"{{slug}}": slug, "{{statement}}": statement, "{{language}}": language,
            "{{language_display}}": display, "{{language_fence}}": fence}
    repl.update(extras or {})
    for k, v in repl.items():
        text = text.replace(k, v)
    return text


# ---------- builder --------------------------------------------------------------

@dataclass
class RoundState:
    """What the prompt needs from earlier rounds."""
    round_idx: int = 0
    total_rounds: int = 1
    history: list[dict] = field(default_factory=list)


class PromptBuilder:
    """(registry prompt or None, problem, language, steer, round state) →
    (system prompt, user prompt). The problem's shape (LeetCode / CLBG) is
    applied last, as in the scripts."""

    def __init__(self, *, target: str | None = None, hints: bool = False):
        self.target, self.hints = target, hints

    def extras(self, problem: dict, language: str, state: RoundState) -> dict[str, str]:
        fence = LANGUAGE_DISPLAY.get(language, (language, language))[1]
        return {
            "{{optimization_directive}}": optimization_directive(self.target),
            "{{hints_block}}": hints_block(self.hints, problem.get("hints")),
            "{{code_template}}": code_template_block(problem.get("code_snippets"), language),
            "{{round_instruction}}": (round_instruction(state.round_idx, state.total_rounds)
                                      if state.round_idx > 0 else ""),
            "{{rounds_history}}": rounds_history(state.history, self.target, fence),
        }

    def build(self, prompt: dict | None, problem: dict, language: str,
              state: RoundState | None = None) -> tuple[str, str]:
        state = state or RoundState()
        slug, statement = problem["title_slug"], problem.get("content") or ""
        display = LANGUAGE_DISPLAY.get(language, (language, language))[0]
        if prompt is not None:
            ex = self.extras(problem, language, state)
            sys_p = expand(prompt["system_prompt"], slug=slug, statement=statement,
                           language=language, extras=ex)
            user_p = expand(prompt["user_prompt_template"], slug=slug,
                            statement=statement, language=language, extras=ex)
        else:
            sys_p = system_prompt(language)
            user_p = f"Solve this LeetCode problem `{slug}` in {display}.\n\n{statement}"
        return shape_for(statement).apply(sys_p, user_p, language=language, slug=slug)

    @staticmethod
    def needs_code_template(prompt: dict | None) -> bool:
        return prompt is not None and "{{code_template}}" in (
            prompt["system_prompt"] + "\n" + prompt["user_prompt_template"])
