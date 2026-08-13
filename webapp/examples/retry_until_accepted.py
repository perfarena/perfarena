#!/usr/bin/env python3
"""Retry-until-accepted PerfArena workflow.

Thin preset over `iterative_refine.py` for the correctness-driven case:
give the model up to N=3 attempts, feed the correctness-oracle's verdict +
error log back into the next round's prompt, stop as soon as one round
lands `Accepted` (subsequent rounds would only spend tokens re-solving
something the model already got right — n_rounds is a MAXIMUM here, not a
fixed count).

Everything lives in iterative_refine.py — the round loop, prompt expansion,
verdict distillation, error-log wiring. This script just forces the two
knobs that define the workflow (`--stop-on-accept` on, `--n-rounds`
defaulting to 3) and delegates.

Any iterative_refine flag works here — pass `--slug`, `--model`,
`--language`, `--competition`, `--openai-api-key`, etc. exactly as you
would over there.

Examples
--------

Correctness-driven retry with Ollama, up to 3 rounds:

    export ARENA_API_KEY=arena_xxxxxxxx
    python retry_until_accepted.py \\
        --slug median-of-two-sorted-arrays \\
        --model qwen2.5-coder:7b

Same, with OpenAI:

    export ARENA_API_KEY=arena_xxxxxxxx
    export OPENAI_API_KEY=sk-…
    python retry_until_accepted.py \\
        --slug median-of-two-sorted-arrays \\
        --provider openai --model gpt-5-mini

Custom retry budget (up to 5 rounds, still stops early on Accepted):

    python retry_until_accepted.py --slug two-sum --n-rounds 5

Dry-run (renders the prompts per round without hitting Ollama / the API):

    python retry_until_accepted.py --dry-run --slug two-sum

Server-side, the Submission row is stamped with `stop_on_accept=True` so
later analysis (`/submissions/{id}` responses, exports) can filter
correctness-driven retries apart from fixed-N sweeps that need every
round to run.
"""
from __future__ import annotations

import sys


def main() -> None:
    # Force the two flags that define the workflow. Both are idempotent —
    # if the user also passes them explicitly, argparse just parses the
    # duplicate cleanly. Default n_rounds is applied only when the user
    # didn't pass their own.
    argv = sys.argv[1:]
    if not any(a == "--stop-on-accept" for a in argv):
        argv.append("--stop-on-accept")
    # --n-rounds defaults to 3 in iterative_refine.py already, so we only
    # need to add it when the user hasn't set it AND wants a different
    # default. Leave the value alone here to keep the delegation transparent.

    # Rebuild sys.argv so iterative_refine.main()'s argparse sees the
    # forced flags. Keep argv[0] so error messages still point at the
    # user-visible entry point rather than at iterative_refine.py.
    sys.argv = [sys.argv[0], *argv]

    # Import lazily so `--help` on this script works even if
    # iterative_refine's optional deps aren't importable in some env.
    from iterative_refine import main as _delegate
    _delegate()


if __name__ == "__main__":
    main()
