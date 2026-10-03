#!/usr/bin/env python3
"""End-to-end PerfArena client: local Ollama model → solution → submit → results.

It records a verifiable provenance trace so a reviewer can confirm the model
actually produced the solution. The trace sent to (and stored by) the platform
contains:

  prompt          the exact user prompt sent to the model
  system_prompt   the system prompt (if any)
  raw_response    the model's full, unedited message content
  reasoning       the thinking/reasoning channel, when the model emits one
  model/provider/endpoint/params/created_at
  provider_meta   Ollama timing + token counts (eval_count, durations, …)

Provenance integrity (matches the server's provenance.py):

  * leaf hash — sha256 over canonical_json({code, inference_trace, trace}).
    The script prints it; the platform recomputes it and anchors it in a
    public append-only hash chain. They must match byte-for-byte.
  * model digest — a provider-agnostic identity. For Ollama we read the
    real model sha256 from /api/tags; otherwise pass --model-digest (HF
    revision SHA, provider system_fingerprint, …). If omitted the platform
    derives a deterministic one.
  * signature (optional) — sign the leaf hash with an Ed25519 key so the
    trace is cryptographically bound to you. Needs the `cryptography`
    package; without it the script still runs, just unsigned.

Flow:
  1. Pull the problem statement from the public API.
  2. Ask a local Ollama model (native /api/chat, think enabled) for a Python
     solution. If Ollama is unreachable, fall back to a canned two-sum solution
     so the script still runs (the trace records that fallback honestly).
  3. Submit code + structured trace + digest (+ optional signature).
  4. Poll until judged; print results and the provenance the server echoes back.

Standard library only (signing degrades gracefully if `cryptography` is absent).

Usage:
  export ARENA_API_KEY=arena_xxxxxxxx
  python submit_with_local_model.py \
      --base-url http://localhost:8000 \
      --slug two-sum \
      --ollama-url http://localhost:11434 \
      --model qwen2.5-coder:7b

  # No Ollama? Canned solution (trace marks provider="canned-fallback"):
  python submit_with_local_model.py --slug two-sum --no-model

  # First-time signing key, then sign every submission with it:
  python submit_with_local_model.py --gen-key ./arena_ed25519.key
  python submit_with_local_model.py --slug two-sum --sign-key ./arena_ed25519.key
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# Canned (`--no-model`) is Python-only and tied to one problem the sampled
# database always contains. Other languages / slugs require a real model.
CANNED_SLUG = "check-if-all-as-appears-before-all-bs"
CANNED_LANGUAGE = "python3"
CANNED_CODE = (
    "class Solution:\n"
    "    def checkString(self, s):\n"
    "        # All a's must precede all b's ⇔ the substring 'ba' never occurs.\n"
    "        return 'ba' not in s\n"
)

# System-supported language slugs → (display name, markdown code-fence tag).
# Mirrors webapp/backend/app/languages.py. Used to build language-specific
# prompts and to extract the right ``` fenced block from the model's output.
LANGUAGE_DISPLAY: dict[str, tuple[str, str]] = {
    "python3":    ("Python 3",   "python"),
    "cpp":        ("C++",        "cpp"),
    "c":          ("C",          "c"),
    "rust":       ("Rust",       "rust"),
    "java":       ("Java",       "java"),
    "csharp":     ("C#",         "csharp"),
    "javascript": ("JavaScript", "javascript"),
    "typescript": ("TypeScript", "typescript"),
    "golang":     ("Go",         "go"),
    "kotlin":     ("Kotlin",     "kotlin"),
    "swift":      ("Swift",      "swift"),
    "scala":      ("Scala",      "scala"),
    "ruby":       ("Ruby",       "ruby"),
    "php":        ("PHP",        "php"),
    "dart":       ("Dart",       "dart"),
    "elixir":     ("Elixir",     "elixir"),
    "erlang":     ("Erlang",     "erlang"),
}

# The subset the perf harness can actually validate + measure — MUST track
# perfarena_handler._LANG_MAP (the runner's authority). swift/scala are valid
# LeetCode languages the sweep understands but the harness can't score yet, so
# they are deliberately excluded here: a default sweep won't waste attempts on
# them (they'd fail the correctness gate with "not measurable"). Pass them
# explicitly via --languages only if you know support has landed.
MEASURABLE_LANGUAGES: tuple[str, ...] = (
    "python3", "cpp", "c", "rust", "java", "csharp", "javascript",
    "typescript", "golang", "kotlin", "swift", "scala", "ruby", "php",
    "dart", "elixir", "erlang",
)


def system_prompt(language: str) -> str:
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


def _is_clbg_statement(statement: str) -> bool:
    """A CLBG benchmark problem ships a self-describing statement with an
    `## Invocation` section and a `Default N:` line (see
    app.seed_clbg_competition._render_content) — markers no LeetCode problem
    carries. Detecting them lets the client swap the LeetCode `Solution`-shape
    instruction for the standalone-program one these benchmarks need."""
    s = statement or ""
    return "## Invocation" in s and "Default N:" in s


# The FIRST command-line argument sits at a different index per language:
# Java/C#/Ruby/Kotlin/Scala pass ONLY the user args (index 0); Node passes
# [node, script, arg...] (index 2); the rest pass [program, arg...] (index 1).
# The harness ALWAYS invokes the program as `<program> N`, so pinning the exact
# accessor stops a model from reading the wrong slot (or from reading N off
# stdin) — the input-normalization the CLBG oracle depends on.
_CLBG_ARGV_HINT: dict[str, str] = {
    "python3":    "sys.argv[1]",
    "cpp":        "argv[1]",
    "c":          "argv[1]",
    "rust":       "std::env::args().nth(1)",
    "java":       "args[0]",
    "csharp":     "args[0]",
    "javascript": "process.argv[2]",
    "typescript": "process.argv[2]",
    "golang":     "os.Args[1]",
    "kotlin":     "args[0]",
    "swift":      "CommandLine.arguments[1]",
    "scala":      "args(0)",
    "ruby":       "ARGV[0]",
    "php":        "$argv[1]",
    "dart":       "args[0]",
    "elixir":     "hd(System.argv())",
    "erlang":     "the first element of the Args list passed to main/1",
}

# Java is the ONLY CLBG language with a NAMED entry class: the harness runs
# `java <name> N` (see each Java cell's Makefile RUN_CMD), so the model's main
# class must be named EXACTLY this. The names are the canonical CLBG program
# names and are irregular (reverse-complement -> revcomp, n-body -> nbody), so
# the model cannot guess them and the client cannot derive them from the slug —
# they must be stated. Every other language finds `main` by convention (a
# binary, `dotnet run`, `node script.js`), so no name is imposed.
_CLBG_JAVA_CLASS: dict[str, str] = {
    "binary-trees":       "binarytrees",
    "fannkuch-redux":     "fannkuchredux",
    "fasta":              "fasta",
    "k-nucleotide":       "knucleotide",
    "mandelbrot":         "mandelbrot",
    "n-body":             "nbody",
    "pidigits":           "pidigits",
    "regex-redux":        "regexredux",
    "reverse-complement": "revcomp",
    "spectral-norm":      "spectralnorm",
}


def clbg_system_prompt(language: str, slug: str = "") -> str:
    """System prompt for CLBG benchmarks. Unlike LeetCode, each benchmark is a
    whole program: it reads N from the command line (and, for a few, a data
    stream from stdin) and must print an exact, byte-for-byte output that a
    reference diff checks. A `class Solution` method has no entry point and
    produces no stdout, so it fails compile or correctness for every language.

    The prompt pins the INPUT CONTRACT explicitly — which argument slot holds N
    (per-language, see `_CLBG_ARGV_HINT`), when to read stdin, and byte-exact
    output — because the arena-local oracle diffs stdout against a reference
    produced by invoking every language's program the SAME way (`<program> N`).
    A model that reads N off stdin, from the wrong argv index, or prints an
    extra label fails the diff even when its algorithm is correct."""
    display, fence = LANGUAGE_DISPLAY.get(language, (language, language))
    argv = _CLBG_ARGV_HINT.get(language, "the first command-line argument")
    base = (
        f"You are an expert performance engineer. Write a COMPLETE, "
        f"self-contained {display} program that solves the benchmark below. "
        f"This is NOT a LeetCode problem: do NOT define a `class Solution` or "
        f"any method wrapper; write a whole program with a normal entry point.\n\n"
        f"INPUT/OUTPUT CONTRACT — follow it EXACTLY. The harness always invokes "
        f"the program as `<program> N`:\n"
        f"1. Read N as the FIRST command-line argument, i.e. `{argv}`, and parse "
        f"it as an integer. N equals the Default N shown below. Do NOT read N "
        f"from standard input and do NOT hardcode it.\n"
        f"2. Read from standard input ONLY if the Input section below describes "
        f"an input stream (for example FASTA data on stdin); otherwise read "
        f"nothing from standard input.\n"
        f"3. Print the result to standard output EXACTLY as the Output section "
        f"shows: identical values, formatting, ordering, and whitespace, with no "
        f"labels, prompts, debug lines, or anything before or after.\n\n"
        f"Think step by step, then return the final program in a single "
        f"```{fence} code block with nothing else outside it."
    )
    # Java runs a NAMED class (`java <name> N`); pin it so the program is
    # actually found at run time (otherwise ClassNotFoundException).
    cls = _CLBG_JAVA_CLASS.get(slug) if language == "java" else None
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


def _apply_clbg_shape(sys_p: str, user_prompt: str, statement: str,
                      language: str, slug: str = "") -> tuple[str, str]:
    """When the target is a CLBG benchmark, replace the LeetCode `Solution`-
    shape instruction baked into every registry/inline prompt with the
    standalone-program contract, and drop the 'LeetCode problem' framing from
    the user prompt. `slug` lets the shape pin per-problem details (e.g. the
    Java entry-class name). No-op for LeetCode problems, so it is safe to call
    on every generation path."""
    if not _is_clbg_statement(statement):
        return sys_p, user_prompt
    sys_p = clbg_system_prompt(language, slug)
    user_prompt = user_prompt.replace(
        "Solve this LeetCode problem", "Solve this benchmark problem")
    return sys_p, user_prompt


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def http_json(method: str, url: str, *, token: str | None = None,
              body: dict | None = None, timeout: int = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} {method} {url}\n{e.read().decode(errors='replace')}")
    except (urllib.error.URLError, OSError) as e:
        # OSError covers http.client.RemoteDisconnected / connection reset
        # (e.g. an ngrok tunnel closing mid-request).
        raise SystemExit(f"cannot reach {url}: {e}")


# Connection-level retry config for _ollama_post. The ngrok tunnels people
# commonly front Ollama with drop connections without warning (especially
# the free tier — silently disconnects "long-running" requests, then
# reconnects on the next call). `Remote end closed connection without
# response` and friends are transient; HTTP errors from the model server
# itself are NOT retried (they're real protocol-level responses).
_OLLAMA_MAX_RETRIES = int(os.environ.get("ARENA_OLLAMA_RETRIES", "5"))
_OLLAMA_INITIAL_RETRY_DELAY = float(os.environ.get("ARENA_OLLAMA_RETRY_DELAY", "5.0"))
_OLLAMA_RETRY_CAP_S = 60.0


def _inference_timeout() -> int:
    """Per-request timeout (seconds) for ONE model inference call, read at call
    time so a CLI flag can set it. Slow local models can exceed the 600s
    default; override with --inference-timeout (matrix_sweep /
    submit_with_local_model) or the ARENA_INFERENCE_TIMEOUT_S env var."""
    try:
        return max(1, int(os.environ.get("ARENA_INFERENCE_TIMEOUT_S", "600")))
    except ValueError:
        return 600


def _ollama_post(url: str, body: dict, timeout: int) -> tuple[int, object]:
    """POST to Ollama. Return (status, parsed_json_or_text) WITHOUT raising on
    HTTP errors, so the caller can react to e.g. 'does not support thinking'.

    Connection-level failures (URLError / OSError — e.g. ngrok tunnel
    `Remote end closed connection without response`, DNS hiccups, transient
    socket resets) are retried with exponential back-off (5s → 10s → 20s →
    40s → 60s, capped). Only after `_OLLAMA_MAX_RETRIES` attempts is
    SystemExit raised — at which point the outage is sustained, not flaky.

    HTTP-level errors (4xx/5xx with a body) are returned to the caller
    on the first try; they're protocol responses, not network failures.
    """
    data = json.dumps(body).encode()
    last_err: Exception | None = None
    delay = _OLLAMA_INITIAL_RETRY_DELAY
    for attempt in range(_OLLAMA_MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, raw
        except (urllib.error.URLError, OSError) as e:
            last_err = e
            if attempt >= _OLLAMA_MAX_RETRIES:
                break
            print(f"  ollama connection blip ({type(e).__name__}: {e}); "
                  f"retrying in {delay:.0f}s "
                  f"(attempt {attempt + 1}/{_OLLAMA_MAX_RETRIES})",
                  file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, _OLLAMA_RETRY_CAP_S)
    raise SystemExit(
        f"cannot reach {url} after {_OLLAMA_MAX_RETRIES + 1} attempts "
        f"(last error: {last_err!r}). "
        f"Override via ARENA_OLLAMA_RETRIES / ARENA_OLLAMA_RETRY_DELAY env vars."
    )


# ---------- provenance (must mirror backend/app/provenance.py) ---------------

def canonical_json(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def compute_trace_hash(code: str, inference_trace: str, trace: dict | None) -> str:
    leaf = {"code": code, "inference_trace": inference_trace, "trace": trace}
    return hashlib.sha256(canonical_json(leaf)).hexdigest()


def sign_leaf(key_path: str, leaf_hash: str) -> tuple[str, str] | None:
    """Sign the leaf hash with an Ed25519 private key. Returns
    (signature_b64, public_key_b64) or None if `cryptography` is unavailable."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ModuleNotFoundError:
        print("  (cryptography not installed — submitting unsigned)", file=sys.stderr)
        return None
    with open(key_path, "rb") as f:
        priv = serialization.load_pem_private_key(f.read(), password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise SystemExit(f"{key_path} is not an Ed25519 private key")
    sig = priv.sign(leaf_hash.encode("ascii"))
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return base64.b64encode(sig).decode(), base64.b64encode(pub).decode()


def gen_key(path: str) -> None:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ModuleNotFoundError:
        raise SystemExit("need the `cryptography` package to generate a key")
    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with open(path, "wb") as f:
        f.write(pem)
    print(f"wrote Ed25519 private key → {path} (keep it secret; reuse it to sign)")


def find_existing_submission(
    base: str, key: str, model_name: str, model_version: str, language: str,
    *,
    prompt_id: int | None = None,
    include_hints: bool = False,
    optimization_target: str | None = None,
    n_rounds: int = 1,
    competition_slug: str | None = None,
    harness_slug: str | None = None,
) -> dict | None:
    """Most recent ongoing submission for this user that matches the FULL
    experiment cell — model + language + prompt + every placeholder-affecting
    knob + competition.

    "Ongoing" = `fully_accepted` is False (still room to add more problems).

    Two submissions with the same model+language but different prompts (or
    different `--hints` / `--target` / `--n-rounds` settings) are different
    experimental cells — appending one to the other would silently merge
    incomparable data into a single bucket. So the match key includes
    every field that materially changes what the model saw:

      model_name, model_version, language     ← the model identity
      prompt_id                                ← which registry template
      include_hints                            ← was {{hints_block}} filled
      optimization_target                      ← latency / memory / none steer
      n_rounds                                 ← single-shot vs refinement
      competition_slug                         ← which problem set is in scope

    `prompt_id=None` matches against submissions that ALSO have no
    prompt_id (i.e. ad-hoc prompts). The text snapshot in
    `prompt_template` itself is NOT compared — relying on the registered
    id is more robust than diffing prose, and an ad-hoc-with-different-text
    case is rare enough that the small risk of accidental merge is
    acceptable.

    Competition match: when `competition_slug` is None we tolerate any
    submission's competition (back-compat with older callers). When it's
    set, only submissions targeting THAT exact competition (or legacy
    rows with no competition_slug at all) match — otherwise a sweep run
    against `leetcode-99-rewritten` would auto-append to an existing
    `leetcode-99-perf` submission and the backend would 400 every attempt
    with "problem slugs not in submission's competition".

    Returns the matched submission dict or None.
    """
    subs = http_json("GET", f"{base}/api/me/submissions", token=key)
    matches = []
    for s in subs:
        if s.get("model_name") != model_name: continue
        if s.get("model_version") != model_version: continue
        if s.get("language") != language: continue
        if s.get("fully_accepted", False): continue
        if (s.get("prompt_id") or None) != (prompt_id or None): continue
        if bool(s.get("include_hints", False)) != bool(include_hints): continue
        if (s.get("optimization_target") or None) != (optimization_target or None): continue
        if int(s.get("n_rounds") or 1) != int(n_rounds): continue
        if competition_slug is not None:
            existing_slug = s.get("competition_slug") or None
            # Legacy rows with no competition_slug still match — they
            # pre-date the per-submission scope and aren't bound to any
            # competition. New rows must match exactly.
            if existing_slug is not None and existing_slug != competition_slug:
                continue
        # Harness filter — critical for pipelines that run the same
        # (model, prompt, language) cell against MULTIPLE perf oracles.
        # Without this, a --harness arena-local sweep would attach to a
        # prior leetcode-harness submission for the same cell, and the
        # arena-local runs would mix with LC verdicts in one Submission
        # row. The Submission's harness_id maps to harness_slug via
        # /api/me/submissions; legacy rows (harness_slug=None) match any
        # requested slug to preserve back-compat with pre-abstraction
        # data.
        if harness_slug is not None:
            existing_harness = s.get("harness_slug") or None
            if existing_harness is not None and existing_harness != harness_slug:
                continue
        matches.append(s)
    matches.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return matches[0] if matches else None


def ollama_model_digest(ollama_url: str, model: str) -> str | None:
    """Back-compat shim — delegates to the Ollama-specific impl below so
    the OllamaProvider class and direct callers share one source of truth."""
    return _ollama_model_digest_impl(ollama_url, model)


def _ollama_model_digest_impl(ollama_url: str, model: str) -> str | None:
    """Real, provider-native model identity: Ollama's sha256 from /api/tags."""
    try:
        tags = http_json("GET", ollama_url.rstrip("/") + "/api/tags", timeout=10)
    except SystemExit:
        return None
    for m in tags.get("models", []):
        if m.get("name") == model or m.get("model") == model:
            d = m.get("digest")
            return f"ollama:{d}" if d else None
    return None


_LANGUAGE_FINGERPRINTS: dict[str, list[str]] = {
    # Per-language regex shortlist for plausibility-checking extracted code.
    # Each pattern is a strong signal that the code IS that language; matches
    # against any one is sufficient. Conservative on purpose — false negatives
    # cost a sample retry, false positives let cross-language smuggling through.
    # Python is whitespace-structured with no mandatory braces / #include /
    # package line, so a valid CLBG program can be a pure top-level script with
    # none of the method-shape tokens below (e.g. `import sys` + a top-level loop
    # + `print(...)`). Add script-style signals, kept Python-specific so a
    # wrong-language blob is still caught: `^print(` (Java's `System.out.print(`
    # is not at line start; C's `printf(` has the trailing f), `sys.argv/stdin/
    # stdout`, `range(`, and BARE stdlib imports (`import java.util.List;` does
    # not match the module allow-list).
    "python3":    [r"^\s*def\s+\w+\s*\(", r"^\s*class\s+\w+\s*[:\(]",
                   r"^\s*from\s+\w+\s+import\b", r"->\s*[\w\[]",
                   r"^\s*print\s*\(", r"\bsys\.(?:argv|stdin|stdout)\b",
                   r"\brange\s*\(",
                   r"^\s*import\s+(?:sys|math|os|array|itertools|collections|functools|re|heapq|bisect|string|time|decimal|struct|io)\b"],
    "cpp":        [r"^\s*#include", r"\bvector\s*<", r"^\s*class\s+\w+\s*\{",
                   r"\bpublic\s*:", r"\bstd::"],
    # C is hardest to fingerprint by entry point — LeetCode C solutions are
    # JUST one function definition, no #include / main / struct / printf.
    # E.g. `int countGoodRectangles(int** rectangles, int rectanglesSize, …)`.
    # We add patterns covering the LC convention: pointer-style arg lists
    # (`int*` / `int**`), the `arrSize` parameter idiom LC auto-passes, and
    # the typed return shape with C-style pointer notation. Standalone C
    # code (with #include / main / struct / printf) is still caught by the
    # legacy patterns above.
    # C is hard to fingerprint by entry point because LC C solutions are
    # JUST one function definition (no #include / main / struct def /
    # printf). We accept the legacy patterns above AND any C-shape
    # function signature: primitive-or-struct-pointer return type, then
    # an identifier, then an arg list whose first arg is primitive- or
    # pointer-typed. Examples that must match:
    #   int countGoodRectangles(int** rectangles, int rectanglesSize, …)
    #   char* longestPalindrome(char* s)
    #   bool isPalindrome(int x)
    #   void rotate(int* nums, int numsSize, int k)
    #   struct ListNode* mergeTwoLists(struct ListNode* a, struct ListNode* b)
    "c":          [r"^\s*#include", r"\bint\s+main\s*\(",
                   r"\bstruct\s+\w+\s*\{", r"\bprintf\s*\(",
                   # LC C: function returning primitive or struct-ptr, with
                   # a primitive- or pointer-typed first arg. Covers the
                   # vast majority of LC C signatures.
                   r"(?m)^\s*(?:int|char|bool|void|double|float|long|short|struct\s+\w+)\s*\*?\s*\*?\s*\w+\s*\(\s*(?:int|char|bool|void|double|float|long|short|struct\s+\w+)",
                   # LC C: `int** matrix` matrix-style arg appearing anywhere.
                   r"\bint\s*\*\*\s*\w+\b",
                   # LC C: auto-passed size parameter convention (`int numsSize`).
                   r"\bint\s+\w+Size\b",
                   # LC C linked-list / tree convention: `struct ListNode*`
                   # used as a type reference (not a struct definition).
                   r"\bstruct\s+\w+\s*\*"],
    "java":       [r"^\s*public\s+class\s+\w+",
                   r"\bpublic\s+[\w\[\]<>]+\s+\w+\s*\([^)]*\)\s*\{",
                   r"\bSystem\.out\.", r"\bimport\s+java\.",
                   r"\bnew\s+\w+\s*\[",          # `new int[]{...}`
                   r"\b(?:int|long|double|float|boolean|char|String)\s*\[\]"],
    "csharp":     [r"\bpublic\s+class\s+\w+", r"\busing\s+System\b",
                   r"\bConsole\.\w+\("],
    "javascript": [r"^\s*function\s+\w+\s*\(", r"\bvar\s+\w+\s*=",
                   r"\blet\s+\w+\s*=", r"\bconst\s+\w+\s*=", r"=>"],
    # TypeScript is a SUPERSET of JavaScript: a valid TS solution can carry zero
    # TS-specific syntax, which is common in terse CLBG numeric code (plain
    # `const`/`let`/`for` loops, no type annotations). Requiring TS-only syntax
    # here wrongly rejected such solutions as wrong_language_extracted, so we also
    # accept the JavaScript shapes. Python/Java/C# etc. still fail (no
    # const/let/=>/`function` keyword), so gross mismatches are still caught.
    "typescript": [r":\s*(string|number|boolean|void)\b", r"\binterface\s+\w+",
                   r"^\s*function\s+\w+\s*\(.*:.*\)\s*:",
                   r"^\s*function\s+\w+\s*\(", r"\bvar\s+\w+\s*=",
                   r"\blet\s+\w+\s*=", r"\bconst\s+\w+\s*=", r"=>"],
    "golang":     [r"^\s*package\s+\w+", r"^\s*func\s+\w+\s*\(",
                   r"^\s*import\s+\("],
    "kotlin":     [r"^\s*fun\s+\w+\s*\(", r"\bval\s+\w+\s*[=:]",
                   r"\bvar\s+\w+\s*[=:]", r"\bclass\s+\w+\s*\("],
    "swift":      [r"^\s*func\s+\w+\s*\(", r"^\s*class\s+\w+\s*:",
                   r"\blet\s+\w+\s*[:=]", r"\bguard\s+let\b"],
    "scala":      [r"^\s*object\s+\w+\b", r"^\s*def\s+\w+\s*\(.*\)\s*:",
                   r"\bval\s+\w+\s*[:=]"],
    "ruby":       [r"^\s*def\s+\w+", r"^\s*class\s+\w+\b",
                   r"\bend\b\s*$", r"\bputs\s+"],
    "php":        [r"<\?php\b", r"\bfunction\s+\w+\s*\(", r"\$\w+"],
    "rust":       [r"^\s*fn\s+\w+\s*\(", r"\blet\s+(mut\s+)?\w+",
                   r"\bimpl\s+\w+", r"\buse\s+std::"],
    # Dart shares surface syntax with Java (generics `List<>`, braces), so the
    # strongest signals are the ones Java/C#/C++ do NOT share: `import 'dart:…'`
    # and the `main(List<String> …)` entry (Java uses `String[] args`). LeetCode
    # Dart methods also carry NO access modifier (`int xorOperation(int n) {`),
    # unlike Java/C# (`public …`), so a method line that STARTS with a bare Dart
    # return type is a good Dart signal; nullable node types (`TreeNode?`) too.
    "dart":       [r"import\s+['\"]dart:",
                   r"\bvoid\s+main\s*\(\s*List<String>",
                   r"(?m)^\s*(?:int|double|bool|String|void|num)\s+\w+\s*\([^)]*\)\s*(?:\{|=>)",
                   r"\bList<[\w<>?,\s]+>\s+\w+",
                   r"\b\w+\?\s+\w+\s*[=,);]",
                   r"=>"],
    # Elixir: defmodule / def...do / the pipe / @spec are all distinctive (and
    # the `do` keyword after `def` separates it from Python's `def foo():`).
    "elixir":     [r"^\s*defmodule\s+",
                   r"(?m)^\s*defp?\s+\w+.*\bdo(\s|$)",
                   r"\|>",
                   r"@spec\b"],
    # Erlang: module/export/spec attributes and the `Name(Args) ->` clause are
    # unique; nothing in the other languages matches these.
    "erlang":     [r"(?m)^\s*-module\(",
                   r"(?m)^\s*-export\(",
                   r"(?m)^\s*-spec\s",
                   r"(?m)^\s*-include\(",
                   r"(?m)^[a-z][a-zA-Z0-9_]*\([^)]*\)\s*->"],
}


def _is_reasoning_model(model: str) -> bool:
    """Return True for OpenAI SKUs that reject custom `temperature` /
    `top_p` (they return HTTP 400 "Only the default value is supported").

    The match is name-prefix based and case-insensitive so we catch:
      * gpt-5, gpt-5-mini, gpt-5-nano, gpt-5.5, gpt-5.4-mini, …
      * o1, o1-mini, o1-pro
      * o3, o3-mini, o3-pro
      * o4, o4-mini
      * Future o5, o6 if they ship under the same contract
      * Date-pinned variants (gpt-5-mini-2025-08-07, o1-2024-12-17, …)

    We DON'T match gpt-4.* or gpt-3.5 (those accept custom temperature)
    or chat-tuned `gpt-5-chat` (which the docs say *does* accept
    temperature — separate SKU from the reasoning family that shares
    the gpt-5 name).
    """
    if not model:
        return False
    m = model.lower().strip()
    # gpt-5-chat / gpt-5-chat-latest specifically ARE chat-tuned and
    # accept temperature — opt them out before the broader gpt-5 prefix
    # match below catches them.
    if m.startswith("gpt-5-chat") or m.startswith("gpt-5.0-chat"):
        return False
    # gpt-5 family (reasoning by default).
    if m.startswith("gpt-5"):
        return True
    # o-series reasoning models (o1, o3, o4, future o5/o6).
    if re.match(r"^o[1-9](-|$)", m):
        return True
    return False


def _looks_like(code: str, language: str) -> bool:
    """Heuristic: does `code` plausibly match the requested `language`?

    Used post-extraction to catch the failure mode where a small model
    ignores the language directive and emits something else (e.g. asks for
    Python 3, gets Java). Without this guard, _extract_code's permissive
    fallback grabs the wrong-language block and we ship it to LeetCode,
    which silently re-classifies — see the rectangle-overlap-Java incident
    documented in dispatch.py's language-mismatch handler.

    A True result doesn't mean the code is correct or even syntactically
    valid — just that it's *not* glaringly the wrong language. Returns
    True when we don't have fingerprints for the language (rather than
    blocking unknown languages we don't have a heuristic for).
    """
    pats = _LANGUAGE_FINGERPRINTS.get(language)
    if not pats:
        return True
    return any(re.search(p, code, re.MULTILINE) for p in pats)


def _extract_code(text: str, fence_lang: str | None = None) -> str:
    """Grab the first ``` fenced block. Prefer the explicitly-tagged one for
    the requested language, fall back to any tagged or bare fence, fall back
    to the whole text trimmed."""
    if fence_lang:
        m = re.search(rf"```{re.escape(fence_lang)}\s*\n(.*?)```",
                      text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
    m = re.search(r"```[A-Za-z0-9_+-]*\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


# The flags-choosing prompt asks the model to end with a marker line:
#   PERFARENA-FLAGS: -O3 -march=native -std=c++17
_FLAGS_MARKER_RE = re.compile(r"^[ \t>*-]*PERFARENA-FLAGS:[ \t]*(.+?)[ \t]*$",
                              re.IGNORECASE | re.MULTILINE)


def extract_compile_flags(text: str) -> str:
    """Pull the model's chosen compilation flags from a `PERFARENA-FLAGS:` marker
    line (the flags-choosing prompt's contract). Returns the flag string, or ""
    when the model emitted none (caller then falls back to the operator's
    --compile-flags or the language default). Uses the LAST match so a model that
    restates the marker inside its reasoning doesn't shadow the final answer."""
    hits = _FLAGS_MARKER_RE.findall(text or "")
    return hits[-1].strip() if hits else ""


# Guard: reject obviously unsafe / non-flag tokens so a hallucinated marker can't
# inject shell/paths. The runner also falls back on a failed compile, but this
# keeps clearly-bad values out of provenance. Flags look like `-O3`, `-march=x`,
# `-C`, `opt-level=3`, `--release`, etc.
_FLAG_TOKEN_RE = re.compile(r"^[A-Za-z0-9_+=./:-]+$")


def sanitize_compile_flags(flags: str) -> str:
    """Keep only token-shaped args (no shell metacharacters/spaces-in-token).
    Returns the cleaned space-joined flags, or "" if nothing valid remains."""
    out = [t for t in (flags or "").split() if _FLAG_TOKEN_RE.match(t)]
    return " ".join(out)


def _normalize_code_for_lc(code: str, language: str, is_clbg: bool = False) -> str:
    """Per-language post-extraction cleanup for the few cases where a
    harness's submission format diverges from how models naturally write
    the code in a fenced block.

    **PHP** needs opposite shapes for the two harnesses:

      * **LeetCode** wraps the user code in an already-open `<?php ... ?>`
        context, so user code must NOT start with `<?php` (and must not
        end with `?>`). When the model emits the natural full-file shape,
        LC trips a parse error on every submission ("unexpected token
        `<`, expecting end of file") — which is what caused submission
        #54 to fail on all 99 PHP attempts. We strip a leading `<?php`
        (with optional `declare(...)` line right after) and any trailing
        `?>`.
      * **CLBG** runs the `.php` file standalone, so it MUST open with
        `<?php` — PHP prints anything outside a `<?php ... ?>` block as
        text, so a stripped or tag-less program fails every case. Here we
        add the tag when the model omitted it instead of stripping it.

    Both branches are idempotent. Add other languages here only if/when a
    harness wrapper requires a similarly non-obvious pass — most languages
    accept the natural full-file shape unchanged.
    """
    if not code:
        return code
    if language == "php":
        stripped = code.lstrip()
        if is_clbg:
            # Standalone program: ensure the opening tag is present.
            if not re.match(r"<\?(php\b|=)", stripped, re.IGNORECASE):
                code = "<?php\n" + code
            return code
        # LeetCode wrapper: strip a leading `<?php` tag plus any
        # `declare(strict_types=1);`-style line that conventionally
        # follows it, anchored to the start after leading whitespace.
        m = re.match(
            r"<\?php\s*(?:declare\s*\([^)]*\)\s*;\s*)?",
            stripped, re.IGNORECASE,
        )
        if m:
            stripped = stripped[m.end():].lstrip()
        # Symmetrically, drop a trailing `?>` (with any trailing whitespace).
        stripped = re.sub(r"\s*\?>\s*$", "", stripped)
        return stripped
    return code


def _expand_template(text: str, *, slug: str, statement: str, language: str,
                     extras: dict[str, str] | None = None) -> str:
    """Replace the {{...}} placeholders used by registry prompt templates.

    Always handles the universal five: {{slug}}, {{statement}},
    {{language}}, {{language_display}}, {{language_fence}}.

    `extras` is a dict of additional placeholder → value to substitute. The
    seeded `experiment-system` and `experiment-system-with-shape` templates
    use five experiment-specific placeholders — {{optimization_directive}},
    {{round_instruction}}, {{hints_block}}, {{code_template}},
    {{rounds_history}} — that the single-shot caller fills via CLI flags
    (--target, --hints, etc.) and the iterative-refinement caller fills
    from its round-by-round state. Any placeholder the prompt references
    but the caller doesn't want populated should be passed as "" rather
    than omitted — that way it renders as a blank line the model ignores
    instead of staying in the prompt as a literal "{{foo}}" token.
    """
    display, fence = LANGUAGE_DISPLAY.get(language, (language, language))
    repl = {
        "{{slug}}": slug, "{{statement}}": statement,
        "{{language}}": language,
        "{{language_display}}": display,
        "{{language_fence}}": fence,
    }
    if extras:
        repl.update(extras)
    out = text
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


# ---------- experiment-template placeholder builders ----------------------
#
# Shared with webapp/examples/iterative_refine.py — that script imports
# `build_optimization_directive` and `build_hints_block` from here. The
# directives below are deliberately copied verbatim from the prompt-registry
# seed defaults in `webapp/backend/app/main.py::_DEFAULT_PROMPTS` so the
# behaviour of `--target latency` (etc.) on the client matches what the
# registered template would have said for an N-round refinement run.

_LATENCY_DIRECTIVE = (
    "Your PRIMARY priority is MINIMIZING RUNTIME. Pick the asymptotically-fastest "
    "algorithm even at the cost of moderate auxiliary memory. The LeetCode "
    "runtime percentile and absolute ms are how this submission will be "
    "evaluated. Correctness is non-negotiable — but among correct solutions, "
    "choose the fastest."
)
_MEMORY_DIRECTIVE = (
    "Your PRIMARY priority is MINIMIZING MEMORY USAGE. Prefer in-place "
    "transformations, avoid auxiliary allocations, reuse buffers, and choose "
    "primitive types over boxed ones where the language allows. The LeetCode "
    "memory percentile and absolute MB are how this submission will be "
    "evaluated. Correctness is non-negotiable — but among correct solutions, "
    "choose the leanest."
)


def build_optimization_directive(target: str | None) -> str:
    """Render the {{optimization_directive}} placeholder for `--target`."""
    if target == "latency":
        return _LATENCY_DIRECTIVE
    if target == "memory":
        return _MEMORY_DIRECTIVE
    return ""


def build_hints_block(include_hints: bool, hints: str | None) -> str:
    """Render the {{hints_block}} placeholder. `hints` is whatever the
    `/api/problems/{slug}` endpoint returned (LeetCode editor hints, either
    JSON-list-ish or plain newline-separated text)."""
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
    return (
        "HINTS (from the LeetCode editors — these may or may not match the "
        f"optimal approach):\n{body}"
    )


def build_code_template_block(snippets: dict | None, language: str) -> str:
    """Render the {{code_template}} placeholder — the LeetCode starter
    snippet for this (problem, language) pair so the model sees the exact
    class + method signature LeetCode's judge expects. Empty when the
    problem has no snippets cached or the language isn't represented."""
    if not snippets:
        return ""
    return snippets.get(language, "") or ""


# ---------- cost + token accounting ----------------------------------------

def estimate_code_tokens(code: str) -> int:
    """Best-effort token count of the CODE-only response. Uses tiktoken's
    cl100k_base when the package is installed (accurate-ish for code); falls
    back to a ~4-chars-per-token heuristic so the script keeps its
    stdlib-only guarantee when tiktoken is absent."""
    if not code:
        return 0
    try:
        import tiktoken
        return len(tiktoken.get_encoding("cl100k_base").encode(code))
    except Exception:                                            # noqa: BLE001
        return max(1, len(code) // 4)


def call_cost_usd(trace_or_provider) -> float:
    """$ cost of one LLM call. Accepts EITHER:

      * a trace dict (preferred) — looked up against provider-specific
        pricing tables using provider_meta.usage when present, or
      * a bare provider name string (legacy back-compat shim).

    Local providers (ollama, canned-fallback) always return 0.0. For
    OpenAI the cost is computed from provider_meta.usage and the
    OPENAI_PRICING_USD_PER_1M table; unrecognised models or providers
    fall back to 0.0 (submitter can override per attempt)."""
    if isinstance(trace_or_provider, str) or trace_or_provider is None:
        # Legacy call site: call_cost_usd(trace.get("provider")). We can't
        # know usage from just the provider name, so return 0.0 — same
        # behaviour the original function had.
        return 0.0
    trace = trace_or_provider
    provider = trace.get("provider")
    if provider in ("ollama", "canned-fallback", None):
        return 0.0
    if provider == "openai":
        return _openai_cost_from_trace(trace)
    return 0.0


# Per-1M token USD pricing for OpenAI models we support. Keys match what
# the model returns in `model` (post-4.1 / gpt-5 era). Lookup also tries
# the base name after stripping a trailing -YYYY-MM-DD date pin, so a
# response with model="gpt-5-mini-2025-08-07" still resolves to the
# "gpt-5-mini" row.
#
# Numbers reflect OpenAI's published API pricing as of mid-2026 — when
# prices change, edit here. Unknown models fall through to 0.0 (the
# sweep keeps running, the leaderboard's $-column just under-reports
# until the row is added).
# Each row carries:
#   in     — fresh prompt tokens / 1M
#   cached — cached prompt tokens / 1M (10× discount on gpt-5/o-series,
#            ~50% discount on gpt-4* per OpenAI's prompt-caching pricing)
#   out    — completion tokens / 1M (reasoning_tokens are folded into
#            completion_tokens by the Responses API by design, so the
#            out rate covers them)
#
# When `cached` is omitted we conservatively bill the cached portion at
# the same rate as fresh — better to over-report than silently underbill.
OPENAI_PRICING_USD_PER_1M: dict[str, dict[str, float]] = {
    # ---- gpt-5 family (reasoning-native; reasoning tokens billed as output) ----
    "gpt-5":                {"in": 1.25,  "cached": 0.125, "out": 10.00},
    "gpt-5-mini":           {"in": 0.25,  "cached": 0.025, "out": 2.00},
    "gpt-5-nano":           {"in": 0.05,  "cached": 0.005, "out": 0.40},
    # gpt-5.4-mini / gpt-5.5 are codenames the matrix_sweep operator's been
    # using; they're not on OpenAI's public list (canonical is `gpt-5-mini`).
    # Because unrecognised model names get alias-routed rather than rejected,
    # the request still completes — we price them at the gpt-5-mini SKU
    # class. Edit if the operator's private pricing differs.
    "gpt-5.4-mini":         {"in": 0.25,  "cached": 0.025, "out": 2.00},
    "gpt-5.5":              {"in": 0.25,  "cached": 0.025, "out": 2.00},
    # gpt-5-chat is the non-reasoning chat-tuned SKU — same wire prices as
    # gpt-5 today but kept as its own row so future divergence is one edit
    # away.
    "gpt-5-chat":           {"in": 1.25,  "cached": 0.125, "out": 10.00},
    "gpt-5-chat-latest":    {"in": 1.25,  "cached": 0.125, "out": 10.00},
    # ---- gpt-4.1 family ----
    "gpt-4.1":              {"in": 2.00,  "cached": 0.50,  "out": 8.00},
    "gpt-4.1-mini":         {"in": 0.40,  "cached": 0.10,  "out": 1.60},
    "gpt-4.1-nano":         {"in": 0.10,  "cached": 0.025, "out": 0.40},
    # ---- gpt-4o family ----
    "gpt-4o":               {"in": 2.50,  "cached": 1.25,  "out": 10.00},
    "gpt-4o-mini":          {"in": 0.15,  "cached": 0.075, "out": 0.60},
    # ---- o-series reasoning models (reasoning tokens billed at output rate) ----
    "o4-mini":              {"in": 1.10,  "cached": 0.275, "out": 4.40},
    "o3":                   {"in": 2.00,  "cached": 0.50,  "out": 8.00},
    "o3-mini":              {"in": 1.10,  "cached": 0.55,  "out": 4.40},
}


def _cached_prompt_tokens(usage: dict) -> int:
    """Pull the cached portion of the prompt from either API shape.

    The Chat Completions API exposes cached input as
    `prompt_tokens_details.cached_tokens`; the Responses API uses
    `input_tokens_details.cached_tokens`. We accept either. When OpenAI
    didn't report a cached count (older trace, no cache hit), this
    returns 0 and the caller bills the full prompt at the fresh rate."""
    details = (usage.get("prompt_tokens_details")
               or usage.get("input_tokens_details") or {})
    return int(details.get("cached_tokens") or 0)


def _openai_cost_from_trace(trace: dict) -> float:
    """Cost = fresh_in × in_rate + cached_in × cached_rate + out × out_rate.

    Three pieces of the bill we account for:
      1. Fresh prompt tokens  → priced at `price["in"]`
      2. Cached prompt tokens → priced at `price["cached"]` (10× discount
         on gpt-5/o-series, ~50% on gpt-4*). OpenAI applies this
         automatically for repeated prompt prefixes ≥1024 tokens.
      3. Completion tokens   → priced at `price["out"]`. Reasoning tokens
         (gpt-5 / o-series) are folded into completion_tokens by both
         the Chat Completions and Responses APIs, so out_rate covers them.

    Unknown models or a missing usage block return 0.0 — better to
    under-report than to invent a number. When `price["cached"]` is
    absent we fall back to `price["in"]` (no discount), keeping the old
    over-estimating behaviour rather than under-billing silently."""
    model = trace.get("model") or ""
    # Allow date-suffixed pins (e.g. "gpt-4.1-mini-2025-04-14") to match the
    # base entry — strip a trailing -YYYY-MM-DD if present.
    base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    price = OPENAI_PRICING_USD_PER_1M.get(model) or OPENAI_PRICING_USD_PER_1M.get(base)
    if not price:
        return 0.0
    usage = (trace.get("provider_meta") or {}).get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    cached = min(_cached_prompt_tokens(usage), pt)  # guard against bad data
    fresh = pt - cached
    cached_rate = price.get("cached", price["in"])
    return (fresh * price["in"]
            + cached * cached_rate
            + ct * price["out"]) / 1_000_000.0


def fetch_prompt(base: str, name: str) -> dict:
    """Pull a registry prompt by name from the public catalog."""
    prompts = http_json("GET", f"{base.rstrip('/')}/api/prompts")
    matched = [p for p in prompts if p.get("name") == name]
    if not matched:
        names = ", ".join(p["name"] for p in prompts) or "(none)"
        raise SystemExit(f"prompt '{name}' not found in registry. Available: {names}")
    return matched[0]


# ===========================================================================
# Provider plugin layer
# ===========================================================================
#
# Each Provider subclass wraps ONE LLM backend (Ollama, OpenAI, Anthropic,
# vLLM, …) behind a uniform `.generate()` / `.model_digest()` / `.cost_usd()`
# API. Adding a new provider = subclass + register in PROVIDERS — all three
# scripts (submit_with_local_model, matrix_sweep, iterative_refine) pick
# it up automatically through `make_provider()`.
#
# Design rule: a Provider OWNS the trace dict shape it emits. The fields
# `provider`, `model`, `endpoint`, `params`, `created_at`, `wall_clock_ms`
# are required; everything else (provider_meta keys, reasoning extraction,
# raw_response shape) is provider-specific.
#
# Why the legacy `call_ollama()` and `ollama_model_digest()` module-level
# functions still exist: matrix_sweep.py and iterative_refine.py import
# them directly. Until those are fully ported we expose Provider AND keep
# the shims, which delegate to OllamaProvider so behaviour stays in sync.

from abc import ABC, abstractmethod


class Provider(ABC):
    """Abstract LLM backend. Subclasses implement the three methods and
    register themselves in the PROVIDERS dict below."""

    name: str = ""    # override in subclass; matches --provider flag value

    @abstractmethod
    def generate(self, model: str, statement: str, slug: str,
                 params: dict, language: str,
                 registry_prompt: dict | None = None,
                 extras: dict[str, str] | None = None) -> tuple[str, dict]:
        """Run one inference. Return (extracted_code, trace_dict).

        `trace_dict` MUST include: provider, model, endpoint, params,
        created_at, wall_clock_ms, prompt, system_prompt, raw_response.
        SHOULD include: reasoning (None if the model doesn't emit one)
        and provider_meta (token counts, system_fingerprint, etc.)."""

    @abstractmethod
    def model_digest(self, model: str) -> str | None:
        """Return a provider-namespaced digest string for this model
        identity (e.g. `ollama:<sha256>`, `openai:<system_fingerprint>`),
        or None if we can't determine one — the platform will derive a
        deterministic digest from the trace in that case."""

    def cost_usd(self, trace: dict) -> float:
        """USD cost of one call. Default 0.0 — overridden by paid providers."""
        return 0.0


# ----- OpenAI (and OpenAI-compatible endpoints: Azure, OpenRouter, …) ------

# Connection retry — mirrors _OLLAMA_MAX_RETRIES; the same kinds of transient
# disconnects bite the OpenAI client too (especially when going through a
# corporate proxy).
_OPENAI_MAX_RETRIES = int(os.environ.get("ARENA_OPENAI_RETRIES", "5"))
_OPENAI_INITIAL_RETRY_DELAY = float(os.environ.get("ARENA_OPENAI_RETRY_DELAY", "5.0"))
_OPENAI_RETRY_CAP_S = 60.0


def _openai_post(url: str, api_key: str, body: dict, timeout: int) -> tuple[int, object]:
    """POST to OpenAI's /v1/chat/completions. Same retry contract as
    _ollama_post — connection-level failures retry with back-off; HTTP
    errors (including 4xx with a JSON body) are returned to the caller."""
    data = json.dumps(body).encode()
    last_err: Exception | None = None
    delay = _OPENAI_INITIAL_RETRY_DELAY
    for attempt in range(_OPENAI_MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, raw
        except (urllib.error.URLError, OSError) as e:
            last_err = e
            if attempt >= _OPENAI_MAX_RETRIES:
                break
            print(f"  openai connection blip ({type(e).__name__}: {e}); "
                  f"retrying in {delay:.0f}s "
                  f"(attempt {attempt + 1}/{_OPENAI_MAX_RETRIES})",
                  file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, _OPENAI_RETRY_CAP_S)
    raise SystemExit(
        f"cannot reach {url} after {_OPENAI_MAX_RETRIES + 1} attempts "
        f"(last error: {last_err!r}). "
        f"Override via ARENA_OPENAI_RETRIES / ARENA_OPENAI_RETRY_DELAY env vars."
    )


class OpenAIProvider(Provider):
    """OpenAI-compatible chat-completions client. Works with the real
    OpenAI API (default base_url) and any compatible endpoint that
    speaks the same /v1/chat/completions schema — Azure OpenAI,
    OpenRouter, Together, vLLM, llama.cpp's --api-key server, …

    Reasoning extraction: looks at message.reasoning_content first
    (o-series style), then falls back to a `<think>...</think>` block
    inside content for providers that proxy that convention.

    Model digest: uses the API's `system_fingerprint` when present,
    namespaced as `openai:<fingerprint>`. Pure-OSS hosts that don't
    return one get None — platform-side derivation kicks in."""

    name = "openai"

    def __init__(self, base_url: str, api_key: str,
                 cost_table: dict | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.cost_table = cost_table or OPENAI_PRICING_USD_PER_1M

    def generate(self, model: str, statement: str, slug: str,
                 params: dict, language: str,
                 registry_prompt: dict | None = None,
                 extras: dict[str, str] | None = None) -> tuple[str, dict]:
        display, fence = LANGUAGE_DISPLAY.get(language, (language, language))
        if registry_prompt is not None:
            sys_p = _expand_template(
                registry_prompt["system_prompt"],
                slug=slug, statement=statement, language=language,
                extras=extras,
            )
            user_prompt = _expand_template(
                registry_prompt["user_prompt_template"],
                slug=slug, statement=statement, language=language,
                extras=extras,
            )
        else:
            sys_p = system_prompt(language)
            user_prompt = (
                f"Solve this LeetCode problem `{slug}` in {display}.\n\n{statement}"
            )
        # CLBG benchmarks need a standalone program, not a LeetCode `Solution`
        # class — swap the shape instruction when the statement is a benchmark.
        sys_p, user_prompt = _apply_clbg_shape(sys_p, user_prompt, statement, language, slug)
        # The OpenAI schema is messages-based, same as Ollama. Temperature
        # and friends ride in the top-level body, not under `options`.
        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_p},
                {"role": "user",   "content": user_prompt},
            ],
        }
        # Whitelist the params we know are first-class on /v1/chat/completions.
        # Anything else stays in trace.params for provenance but isn't sent.
        for k in ("temperature", "top_p", "max_tokens", "max_completion_tokens",
                  "frequency_penalty", "presence_penalty", "seed", "stop",
                  "reasoning_effort"):
            if params.get(k) is not None:
                body[k] = params[k]
        # Reasoning-tuned SKUs (gpt-5 family, o-series) reject custom
        # `temperature` / `top_p` with HTTP 400 ("Only the default value
        # is supported"). Strip them silently so a default args.temperature
        # of 0.2 doesn't 400-bomb every single tuple — the sampling
        # axis becomes "model decides", which is the intended behaviour
        # for reasoning models anyway. We DO keep `reasoning_effort`
        # (those models' own parallel knob) when set.
        reasoning_model = _is_reasoning_model(model)
        if reasoning_model:
            for stripped in ("temperature", "top_p"):
                if stripped in body:
                    body.pop(stripped)
        # Reasoning models hide their actual chain-of-thought from the
        # `/v1/chat/completions` response (you get a token COUNT in
        # `usage.completion_tokens_details.reasoning_tokens` but no
        # text). To capture the reasoning summary text we want to
        # store on every attempt, we have to route reasoning models
        # through OpenAI's `/v1/responses` endpoint, whose response
        # carries reasoning items separately. Non-reasoning models
        # stay on `/chat/completions` — no point in paying the
        # different-API tax there.
        finish_reason: str | None = None
        if reasoning_model:
            code, content, reasoning, gen_ms, resp, finish_reason = self._call_responses_api(
                model, sys_p, user_prompt, params, language, fence, statement,
            )
        else:
            endpoint = self.base_url + "/chat/completions"
            gen_t0 = time.perf_counter()
            status, resp = _openai_post(endpoint, self.api_key, body, timeout=_inference_timeout())
            gen_ms = int((time.perf_counter() - gen_t0) * 1000)
            if status != 200 or not isinstance(resp, dict):
                raise SystemExit(f"OpenAI HTTP {status}: {resp}")
            choice = (resp.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            # Reasoning channel: (a) o-series exposes it as message.reasoning_content,
            # (b) some OSS proxies pass through <think>...</think> blocks.
            reasoning = msg.get("reasoning_content") or msg.get("reasoning")
            if not reasoning:
                mt = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
                reasoning = mt.group(1).strip() if mt else None
            cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
            code = _normalize_code_for_lc(
                _extract_code(cleaned, fence_lang=fence), language,
                _is_clbg_statement(statement),
            )
            finish_reason = choice.get("finish_reason")
        trace = {
            "prompt": user_prompt,
            "system_prompt": sys_p,
            "raw_response": content,
            "reasoning": reasoning,
            "model": model,
            "provider": "openai",
            "endpoint": self.base_url,
            "params": params,
            "created_at": _now(),
            "wall_clock_ms": gen_ms,
            "provider_meta": {
                "system_fingerprint": resp.get("system_fingerprint"),
                "usage": resp.get("usage"),
                "finish_reason": finish_reason,
                # OpenAI sometimes returns the resolved model pin separate
                # from the requested one (e.g. gpt-4.1-mini-2025-04-14).
                "model_resolved": resp.get("model"),
                # Surface that this attempt went through the Responses API
                # vs Chat Completions, so the SPA / audit can tell apart
                # reasoning-channel traces from non-reasoning ones at a
                # glance without having to inspect the response shape.
                "api": "responses" if reasoning_model else "chat_completions",
            },
        }
        return code, trace

    def model_digest(self, model: str) -> str | None:
        """OpenAI doesn't expose a model SHA on /v1/models the way Ollama
        does. The closest stable identity is the system_fingerprint
        returned by the chat endpoint — but we don't have one without
        burning a real call. Cheapest reliable path: do a 1-token probe
        and read system_fingerprint from the response. We fall back to
        None on any error so the platform's derived digest takes over."""
        endpoint = self.base_url + "/chat/completions"
        try:
            status, resp = _openai_post(endpoint, self.api_key, {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            }, timeout=20)
            if status == 200 and isinstance(resp, dict):
                fp = resp.get("system_fingerprint")
                if fp:
                    return f"openai:{fp}"
        except SystemExit:
            return None
        return None

    def _call_responses_api(
        self, model: str, sys_p: str, user_prompt: str, params: dict,
        language: str, fence: str, statement: str,
    ) -> tuple[str, str, str | None, int, dict, str | None]:
        """Reasoning-model path through /v1/responses.

        Returns the same five fields as the inlined Chat Completions
        block plus `finish_reason`, so the caller can build one unified
        `trace` regardless of which API produced the response:
          * code        — extracted from the message output_text
          * content     — raw message text (input to _extract_code)
          * reasoning   — concatenated reasoning summary items (text)
          * gen_ms      — wall-clock for the API call
          * resp        — full response JSON (for provider_meta)
          * finish_reason — normalised from `resp.status`

        Why the Responses API: Chat Completions hides reasoning content
        from gpt-5 / o-series responses (you only get a token COUNT in
        `usage.completion_tokens_details.reasoning_tokens`). The
        Responses API returns reasoning summary items separately under
        `output[].type == "reasoning"`, which we surface here so the
        per-attempt audit / SPA can show what the model was thinking.

        We request `reasoning.summary = "auto"` so the model picks the
        appropriate verbosity (concise vs detailed) on its own. The
        `reasoning_effort` knob from the chat-completions whitelist
        passes through unchanged when set — it controls how MUCH the
        model reasons, the summary is what we render."""
        body: dict = {
            "model": model,
            "input": [
                {"role": "system", "content": sys_p},
                {"role": "user",   "content": user_prompt},
            ],
            "reasoning": {"summary": "auto"},
        }
        # Pass reasoning_effort through when the operator wants to dial
        # it (default behaviour is the model's own choice).
        if params.get("reasoning_effort") is not None:
            body["reasoning"]["effort"] = params["reasoning_effort"]
        # max_completion_tokens is the cross-API replacement for
        # max_tokens for reasoning models — pass through if set.
        if params.get("max_completion_tokens") is not None:
            body["max_output_tokens"] = params["max_completion_tokens"]
        endpoint = self.base_url + "/responses"
        gen_t0 = time.perf_counter()
        status, resp = _openai_post(endpoint, self.api_key, body, timeout=_inference_timeout())
        gen_ms = int((time.perf_counter() - gen_t0) * 1000)
        if status != 200 or not isinstance(resp, dict):
            raise SystemExit(f"OpenAI HTTP {status}: {resp}")
        # Walk `output` to extract reasoning summary + message text.
        # Output items are typed: {"type": "reasoning", "summary": [...]}
        # and {"type": "message", "content": [{"type": "output_text", "text": ...}]}.
        # Order isn't strictly guaranteed by docs but reasoning typically
        # precedes the final message; we don't depend on order — just
        # collect each type.
        reasoning_parts: list[str] = []
        message_parts: list[str] = []
        for item in (resp.get("output") or []):
            t = item.get("type")
            if t == "reasoning":
                for s in (item.get("summary") or []):
                    txt = s.get("text") if isinstance(s, dict) else None
                    if txt:
                        reasoning_parts.append(txt)
            elif t == "message":
                for c in (item.get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "output_text":
                        txt = c.get("text")
                        if txt:
                            message_parts.append(txt)
        # The Responses API also exposes a convenience `output_text`
        # field at the top level (concatenation of all message_parts).
        # Prefer it when present so we match what OpenAI's own SDK
        # would build, and fall back to our manual join otherwise.
        content = resp.get("output_text") or "\n".join(message_parts)
        reasoning = "\n\n".join(reasoning_parts) if reasoning_parts else None
        # The actual code lives inside content — same extractor as the
        # Chat Completions path.
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        code = _normalize_code_for_lc(
            _extract_code(cleaned, fence_lang=fence), language,
            _is_clbg_statement(statement),
        )
        # `status` is the lifecycle field on a Responses object —
        # "completed" / "in_progress" / "incomplete". Map to a finish_reason
        # roughly equivalent to what Chat Completions returns.
        finish_reason = resp.get("status")
        # Normalise the usage block so downstream consumers (the cost
        # calculator, the token-count columns on the SPA, the live cost
        # recompute on /models) don't have to know whether the trace
        # came from /chat/completions or /responses. The Responses API
        # uses `input_tokens` / `output_tokens`; Chat Completions uses
        # `prompt_tokens` / `completion_tokens`. We add both alias
        # forms to the usage dict so existing code paths keep working.
        usage = resp.get("usage") or {}
        if usage:
            if "prompt_tokens" not in usage and "input_tokens" in usage:
                usage["prompt_tokens"] = usage["input_tokens"]
            if "completion_tokens" not in usage and "output_tokens" in usage:
                usage["completion_tokens"] = usage["output_tokens"]
            resp["usage"] = usage         # in case it was a fresh dict
        return code, content, reasoning, gen_ms, resp, finish_reason

    def cost_usd(self, trace: dict) -> float:
        return _openai_cost_from_trace(trace)


# ----- Ollama (existing behaviour, wrapped in the Provider shape) ---------

class OllamaProvider(Provider):
    """Local Ollama backend. Preserves the exact trace shape the platform
    has been ingesting since v0.1 — adding the Provider wrapper around it
    is a no-op on the wire. Cost is always 0.0 (local inference)."""

    name = "ollama"

    def __init__(self, ollama_url: str, think: bool = True) -> None:
        self.ollama_url = ollama_url
        self.think = think

    def generate(self, model: str, statement: str, slug: str,
                 params: dict, language: str,
                 registry_prompt: dict | None = None,
                 extras: dict[str, str] | None = None) -> tuple[str, dict]:
        return _ollama_generate_impl(
            self.ollama_url, model, statement, slug, params, language,
            registry_prompt=registry_prompt, extras=extras, think=self.think,
        )

    def model_digest(self, model: str) -> str | None:
        return _ollama_model_digest_impl(self.ollama_url, model)

    # cost_usd inherits the default (0.0).


# ----- registry + factory ---------------------------------------------------

PROVIDERS: dict[str, type[Provider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
}


def make_provider(args) -> Provider:
    """Build the Provider implied by argparse Namespace `args`. Expects
    `args.provider` plus the provider-specific connection knobs
    (`args.ollama_url` for ollama; `args.openai_base_url`+`args.openai_api_key`
    for openai). Centralised so all three scripts share one configuration
    path."""
    name = (getattr(args, "provider", None) or "ollama").lower()
    cls = PROVIDERS.get(name)
    if cls is None:
        raise SystemExit(
            f"unknown --provider {name!r}; supported: {', '.join(sorted(PROVIDERS))}"
        )
    if name == "ollama":
        return cls(ollama_url=getattr(args, "ollama_url"),
                   think=not getattr(args, "no_think", False))
    if name == "openai":
        key = (getattr(args, "openai_api_key", None)
               or os.environ.get("OPENAI_API_KEY"))
        if not key:
            raise SystemExit(
                "--provider openai needs an API key: set --openai-api-key "
                "or OPENAI_API_KEY in the environment."
            )
        return cls(
            base_url=getattr(args, "openai_base_url",
                             "https://api.openai.com/v1"),
            api_key=key,
        )
    raise SystemExit(f"provider {name!r} has no factory wiring")


# ===========================================================================
# Legacy module-level shims (back-compat for direct imports)
# ===========================================================================

def call_ollama(ollama_url: str, model: str, statement: str, slug: str,
                params: dict, language: str,
                registry_prompt: dict | None = None,
                extras: dict[str, str] | None = None) -> tuple[str, dict]:
    """Back-compat wrapper: delegates to OllamaProvider.generate. New code
    should instantiate the provider directly via `make_provider(args)`
    so the same call site works for OpenAI too."""
    return OllamaProvider(ollama_url).generate(
        model, statement, slug, params, language,
        registry_prompt=registry_prompt, extras=extras,
    )


def _ollama_generate_impl(ollama_url: str, model: str, statement: str, slug: str,
                          params: dict, language: str,
                          registry_prompt: dict | None = None,
                          extras: dict[str, str] | None = None,
                          think: bool = True) -> tuple[str, dict]:
    """Call Ollama's native /api/chat (think=on). Return (code, trace).
    `language` selects the prompt wording and the preferred code-fence tag.
    If `registry_prompt` is given, its system_prompt + user_prompt_template
    are used after placeholder expansion; otherwise we fall back to the
    inline `system_prompt(language)` + slug/statement string.

    `extras` is forwarded to `_expand_template` to fill experiment-specific
    placeholders ({{optimization_directive}}, {{hints_block}},
    {{code_template}}, {{round_instruction}}, {{rounds_history}}) when the
    registered prompt uses them — see `build_optimization_directive`,
    `build_hints_block`, `build_code_template_block` for the producers.
    """
    display, fence = LANGUAGE_DISPLAY.get(language, (language, language))
    if registry_prompt is not None:
        sys_p = _expand_template(
            registry_prompt["system_prompt"],
            slug=slug, statement=statement, language=language,
            extras=extras,
        )
        user_prompt = _expand_template(
            registry_prompt["user_prompt_template"],
            slug=slug, statement=statement, language=language,
            extras=extras,
        )
    else:
        sys_p = system_prompt(language)
        user_prompt = (
            f"Solve this LeetCode problem `{slug}` in {display}.\n\n{statement}"
        )
    # CLBG benchmarks need a standalone program, not a LeetCode `Solution`
    # class — swap the shape instruction when the statement is a benchmark.
    sys_p, user_prompt = _apply_clbg_shape(sys_p, user_prompt, statement, language, slug)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_prompt},
        ],
        "think": think,          # True exposes the reasoning channel; False (--no-think) disables it
        "stream": False,
        "options": params,
    }
    endpoint = ollama_url.rstrip("/") + "/api/chat"
    # Wall-clock of the generation process for this solution. Measured around
    # the full call (including a no-think retry) so it reflects what the client
    # actually waited; persisted to the attempt as wall_clock_ms and summed into
    # the submission's total_generation_ms.
    gen_t0 = time.perf_counter()
    status, resp = _ollama_post(endpoint, payload, timeout=_inference_timeout())
    # Non-thinking models reject `think`; retry once without it.
    if status == 400 and "thinking" in json.dumps(resp).lower():
        print("  (model has no thinking channel — retrying without think)",
              file=sys.stderr)
        payload.pop("think", None)
        status, resp = _ollama_post(endpoint, payload, timeout=_inference_timeout())
    gen_ms = int((time.perf_counter() - gen_t0) * 1000)
    if status != 200 or not isinstance(resp, dict):
        raise SystemExit(f"Ollama HTTP {status}: {resp}")
    msg = resp.get("message", {})
    content = msg.get("content", "") or ""
    # Ollama returns reasoning either as message.thinking (newer thinking models)
    # or inline inside <think>...</think> tags within the content.
    reasoning = msg.get("thinking")
    if not reasoning:
        mt = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
        reasoning = mt.group(1).strip() if mt else None
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    code = _normalize_code_for_lc(
        _extract_code(cleaned, fence_lang=fence), language,
        _is_clbg_statement(statement),
    )
    trace = {
        "prompt": user_prompt,
        "system_prompt": sys_p,
        "raw_response": content,
        "reasoning": reasoning,
        "model": model,
        "provider": "ollama",
        "endpoint": ollama_url,
        "params": params,
        "created_at": _now(),
        "wall_clock_ms": gen_ms,
        "provider_meta": {
            k: resp[k] for k in (
                "total_duration", "load_duration", "prompt_eval_count",
                "prompt_eval_duration", "eval_count", "eval_duration",
                "done_reason",
            ) if k in resp
        },
    }
    return code, trace


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("ARENA_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.environ.get("ARENA_API_KEY"))
    ap.add_argument("--slug", default="two-sum")
    ap.add_argument("--competition", default="leetcode-99-perf", metavar="SLUG",
                    help="competition the submission targets (default: "
                         "leetcode-99-perf). The slug given by --slug must "
                         "belong to this competition; pass@k uses its problem "
                         "count as the denominator.")
    ap.add_argument("--provider", default="ollama", choices=sorted(PROVIDERS),
                    help="LLM backend. ollama (default) uses --ollama-url; "
                         "openai uses --openai-base-url + --openai-api-key "
                         "(or the OPENAI_API_KEY env var).")
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    ap.add_argument("--openai-base-url",
                    default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                    help="OpenAI-compatible /v1 endpoint. Override for Azure, "
                         "OpenRouter, Together, vLLM, etc.")
    ap.add_argument("--openai-api-key", default=os.environ.get("OPENAI_API_KEY"),
                    help="OpenAI API key (or set OPENAI_API_KEY env var).")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--model-version", default=None,
                    help="free-form provenance tag for the model version. "
                         "Defaults to the provider name when omitted.")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--language", default="python3",
                    choices=sorted(LANGUAGE_DISPLAY),
                    help="target language slug for the solution "
                         "(must match a system-supported language; default python3)")
    ap.add_argument("--no-model", action="store_true",
                    help=f"skip Ollama; use the canned {CANNED_LANGUAGE} "
                         f"solution for {CANNED_SLUG}")
    ap.add_argument("--model-digest", default=None,
                    help="explicit model identity digest (HF revision SHA, "
                         "provider system_fingerprint, …); auto-read for Ollama")
    ap.add_argument("--sign-key", default=None,
                    help="Ed25519 PEM private key to sign the trace leaf hash")
    ap.add_argument("--gen-key", default=None,
                    help="generate an Ed25519 private key at this path and exit")
    # Auto-append is the DEFAULT: any ongoing submission with the same
    # experiment cell (model_name, model_version, language, prompt_id,
    # include_hints, optimization_target, n_rounds) absorbs this attempt.
    # Pass --new-submission to force creating a fresh one instead.
    # `--append-if-existing` is kept as a no-op alias for back-compat
    # with old scripts/docs.
    ap.add_argument("--append-if-existing", action="store_true",
                    help="(no-op alias — auto-append is now the default; "
                         "pass --new-submission to opt out)")
    ap.add_argument("--new-submission", action="store_true",
                    help="force creating a brand-new submission even when "
                         "an ongoing one matches this experiment cell. "
                         "Useful for starting an A/B against an existing run.")
    ap.add_argument("--append-to", type=int, default=None, metavar="SUB_ID",
                    help="append this attempt to a specific submission id "
                         "(skips the search; overrides --append-if-existing)")
    ap.add_argument("--samples-per-problem", "-k", type=int, default=1, metavar="K",
                    help="generate K solutions for the slug (temperature-varied) "
                         "and submit them all as one pass@k attempt set; sets "
                         "the new submission's n_samples_per_problem=K")
    ap.add_argument("--max-concurrent-samples", type=int, default=0, metavar="N",
                    help="when K>1, fan out up to N Ollama calls in parallel "
                         "instead of generating samples sequentially — the "
                         "k-sample total time drops to roughly max(per-call) "
                         "instead of sum(per-call). 0 (default) = uncapped "
                         "(K-wide). Set to 1 for old strictly-sequential.")
    ap.add_argument("--prompt", default=None, metavar="NAME",
                    help="use a registry prompt by name (GET /api/prompts) "
                         "instead of the inline default; the resulting "
                         "submission is stamped with prompt_id for filtering")
    ap.add_argument("--hints", action="store_true",
                    help="if the registry prompt uses {{hints_block}}, fill "
                         "it with LeetCode's editor hints for this problem. "
                         "No-op when the template doesn't reference the "
                         "placeholder, or when the problem has no hints.")
    ap.add_argument("--target", choices=("latency", "memory"), default=None,
                    help="if the registry prompt uses {{optimization_directive}}, "
                         "fill it with a steer toward this metric. Only meaningful "
                         "for templates like experiment-system / "
                         "experiment-system-with-shape that reference the "
                         "placeholder.")
    ap.add_argument("--poll-seconds", type=int, default=None, metavar="SEC",
                    help="how long to wait for the whole submission (all "
                         "--samples-per-problem attempts) to finish judging. "
                         "The wait covers each attempt's server-side validate + "
                         "measure. Default scales with the competition and the "
                         "sample count: ~120s/attempt for LeetCode, "
                         "~1800s/attempt for arena-local CLBG (1 warmup + 7 "
                         "measure whole-program runs at ~150s each, plus "
                         "validate). Too short a budget stops the wait while the "
                         "server is still measuring.")
    ap.add_argument("--inference-timeout", type=int, default=None, metavar="SEC",
                    help="Per-request timeout (seconds) for ONE model inference "
                         "call (default 600). Raise it for slow local models; "
                         "lower it to fail fast.")
    args = ap.parse_args()
    if args.inference_timeout is not None:
        os.environ["ARENA_INFERENCE_TIMEOUT_S"] = str(args.inference_timeout)

    # Poll budget covers every attempt's validate + measure, run serially. CLBG
    # measures far longer than a LeetCode verdict, so scale the default by the
    # competition and the sample count. An explicit --poll-seconds always wins.
    if args.poll_seconds is None:
        _per_attempt = 1800 if "clbg" in (args.competition or "").lower() else 120
        args.poll_seconds = _per_attempt * max(1, args.samples_per_problem)

    if args.gen_key:
        gen_key(args.gen_key)
        return

    if not args.api_key:
        raise SystemExit("set --api-key or ARENA_API_KEY (from your approved PerfArena account)")
    base = args.base_url.rstrip("/")
    params = {"temperature": args.temperature}

    print(f"• fetching problem '{args.slug}' …")
    problem = http_json("GET", f"{base}/api/problems/{args.slug}")
    statement = problem.get("content", "")

    if args.no_model and args.samples_per_problem > 1:
        raise SystemExit(
            "--no-model only ships a single canned solution; "
            "use --samples-per-problem 1 (default) with --no-model, or run "
            "with Ollama to draw multiple samples."
        )

    # Optional registry prompt: resolved once, then used for every sample and
    # stamped on the submission via prompt_id.
    registry_prompt: dict | None = None
    prompt_id: int | None = None
    if args.prompt:
        registry_prompt = fetch_prompt(base, args.prompt)
        prompt_id = registry_prompt["id"]
        print(f"• using registry prompt '{registry_prompt['name']}' (id={prompt_id})")

    # Build the experiment-template placeholder values from CLI flags. The
    # values are the same across every sample in this submission (the only
    # thing that varies per-sample is the temperature), so compute them
    # once. {{round_instruction}} and {{rounds_history}} are always empty
    # in single-shot mode; the iterative-refinement driver in
    # webapp/examples/iterative_refine.py overrides them per round. We
    # ALWAYS include the keys (rendered as empty strings) so a prompt that
    # references them doesn't ship "{{rounds_history}}" verbatim to the
    # model.
    extras = {
        "{{optimization_directive}}": build_optimization_directive(args.target),
        "{{hints_block}}":            build_hints_block(args.hints, problem.get("hints")),
        "{{code_template}}":          build_code_template_block(
                                          problem.get("code_snippets"), args.language),
        "{{round_instruction}}":      "",
        "{{rounds_history}}":         "",
    }
    if registry_prompt is not None:
        # Warn about placeholders the prompt uses but the user didn't fill —
        # they'll render as blank but it's the kind of thing worth catching.
        tpl_text = (registry_prompt["system_prompt"]
                    + "\n" + registry_prompt["user_prompt_template"])
        if "{{code_template}}" in tpl_text and not extras["{{code_template}}"]:
            print(f"  warning: prompt uses {{{{code_template}}}} but no snippet "
                  f"for {args.slug}/{args.language} — run "
                  f"`python -m app.fetch_code_snippets --sampled-only`",
                  file=sys.stderr)
        if "{{hints_block}}" in tpl_text and not args.hints:
            print(f"  note: prompt has {{{{hints_block}}}}; pass --hints to "
                  f"include LeetCode's editor hints", file=sys.stderr)
        if "{{optimization_directive}}" in tpl_text and not args.target:
            print(f"  note: prompt has {{{{optimization_directive}}}}; pass "
                  f"--target latency|memory to steer the model", file=sys.stderr)

    # Temperature schedule for k-sampling: evenly spaced between the base and
    # base+0.5 (capped at 1.0) so each sample's decoder lands in a different
    # part of the distribution. k=1 collapses to the base temperature.
    base_t = args.temperature
    k = max(1, args.samples_per_problem)
    if k == 1:
        temps = [base_t]
    else:
        hi = min(1.0, base_t + 0.5)
        step = (hi - base_t) / (k - 1)
        temps = [round(base_t + i * step, 3) for i in range(k)]

    # Build the provider once per run — same instance handles every sample.
    # Skipped for --no-model (canned solutions don't need a backend).
    provider: Provider | None = None
    if not args.no_model:
        provider = make_provider(args)
    # Default --model-version to the provider name so a sweep filenames /
    # leaderboard cells stay distinguishable when the same --model is run
    # under more than one backend.
    if not args.model_version:
        args.model_version = provider.name if provider else "ollama"

    model_digest = args.model_digest
    if provider is not None and not model_digest:
        # One lookup per run (not per sample) — the digest is identical for
        # every call to the same model.
        model_digest = provider.model_digest(args.model)
        if model_digest:
            print(f"  model digest (from {provider.name}): {model_digest[:24]}…")

    attempts: list[dict] = []
    sample_leafs: list[str] = []
    print(f"• producing {k} sample(s) at temperature(s) {temps}")

    # K>1 samples are independent generations — the only thing that varies is
    # the temperature seed. Fan them out across a ThreadPoolExecutor so the
    # k-sample wall time is dominated by max(per-call) instead of sum(per-call).
    # Ollama serializes internally when needed, so even on a one-GPU box this
    # is safe — the worst case is the same as sequential. --max-concurrent-samples
    # caps the fan-out (0 = no cap, K-wide).
    cap = args.max_concurrent_samples
    workers = k if (cap is None or cap <= 0) else max(1, min(cap, k))
    if workers > 1:
        print(f"  fanning out {k} samples across {workers} concurrent worker(s)")

    # Per-sample work — Ollama call + language-conformance check + attempt
    # dict build. Returns (sample_index, attempt | None, leaf | None, skip_reason).
    def _produce(i: int, t: float):
        sample_params = {"temperature": t}
        if args.no_model:
            code, trace = _canned_trace(
                args.slug, args.language, sample_params, reason="--no-model",
            )
            print(f"  [{i}/{k}] canned ({args.language})")
        else:
            print(f"  [{i}/{k}] asking {args.model} (provider={provider.name}) at temperature={t} …")
            try:
                code, trace = provider.generate(
                    args.model, statement, args.slug,
                    sample_params, args.language,
                    registry_prompt=registry_prompt,
                    extras=extras,
                )
                rlen = len(trace["reasoning"] or "")
                print(f"  [{i}/{k}] got {len(code)} chars of code; reasoning trace: {rlen} chars")
            except SystemExit as e:
                print(f"  [{i}/{k}] {provider.name} call failed ({e}); using canned solution",
                      file=sys.stderr)
                code, trace = _canned_trace(
                    args.slug, args.language, sample_params,
                    reason=f"{provider.name}-unreachable: {e}",
                )
        # Language-conformance check. Small models occasionally ignore the
        # language directive and emit Java/C++/etc. when asked for Python.
        # Rather than silently drop the sample (losing the data point), we
        # flag it with `prefailed_reason` — the server records it as a failed
        # attempt with the raw response preserved, no LeetCode quota burned.
        prefailed_reason: str | None = None
        if not args.no_model and not _looks_like(code, args.language):
            prefailed_reason = "wrong_language_extracted"
            print(f"  [{i}/{k}] ⚠ extracted code doesn't look like {args.language} "
                  f"— recording as failed (raw response preserved in trace)",
                  file=sys.stderr)

        summary = (trace.get("reasoning") or trace.get("raw_response")
                   or "model produced the solution; see structured trace").strip()
        summary = summary[:4000] or "solution generated by model"
        if prefailed_reason and len(summary) < 20:
            # The summary has a min-20-char server-side requirement; for a
            # malformed-output trace the reasoning channel may be empty, so
            # backstop with a self-describing message.
            summary = (f"client flagged {prefailed_reason}; the model's full "
                       "raw response is in trace.raw_response")
        elif len(summary) < 20:
            # A terse-but-valid reasoning ("Simple.") is real provenance, not a
            # defect. Keep the model's words, append context so it stays
            # meaningful AND clears the >=20-char server minimum.
            summary = (f"{summary} [{args.slug}/{args.language}; "
                       "full trace in trace.raw_response]")
        leaf = compute_trace_hash(code, summary, trace)
        print(f"  [{i}/{k}] trace leaf hash: {leaf}")

        attempt: dict = {
            "title_slug": args.slug,
            "code": code,
            "inference_trace": summary,
            "trace": trace,
            # Cost + tokens of this single LLM call (one sample). Ollama is
            # free → 0.0; OpenAI computes (prompt+completion tokens × price)
            # from provider_meta.usage. response_tokens counts the CODE
            # only, not reasoning.
            "cost_usd": call_cost_usd(trace),
            "response_tokens": estimate_code_tokens(code),
            # Generation wall-clock of this solution (ms), measured in call_ollama.
            "wall_clock_ms": trace.get("wall_clock_ms"),
            # Generation energy (J), forwarded if a probe filled it in. None
            # for the default Ollama path; the server hides the column when
            # nothing in scope reports a value.
            "generation_energy_j": trace.get("energy_j"),
        }
        if prefailed_reason:
            attempt["prefailed_reason"] = prefailed_reason
        if model_digest:
            attempt["model_digest"] = model_digest
        if args.sign_key:
            signed = sign_leaf(args.sign_key, leaf)
            if signed:
                attempt["trace_sig"], attempt["trace_signer"] = signed
        return (i, attempt, leaf, None)

    # Run all samples (concurrent or sequential), then re-sort by sample index
    # so the output order is deterministic regardless of which thread finished
    # first — important for reproducibility of trace hashes / chain ordering.
    if workers == 1:
        results = [_produce(i, t) for i, t in enumerate(temps, start=1)]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_produce, i, t)
                       for i, t in enumerate(temps, start=1)]
            results = [f.result() for f in as_completed(futures)]
        results.sort(key=lambda r: r[0])

    for _i, attempt, leaf, _reason in results:
        if attempt is None:
            continue
        attempts.append(attempt)
        sample_leafs.append(leaf)

    # Defensive: with the prefailed-reason path every sample yields an attempt
    # (failed ones are recorded with prefailed_reason instead of being dropped),
    # so this branch only fires if _produce itself returned (None, …) — which
    # nothing currently does. Keep the budget-shrink fallback so any future
    # genuine-skip path stays safe.
    if not attempts:
        raise SystemExit(
            "no attempts produced — every sample failed to materialize. "
            "Inspect the Ollama logs and re-run."
        )
    if len(attempts) < k:
        print(f"  note: requested k={k} samples but only {len(attempts)} "
              f"attempts materialized; the submission's "
              f"n_samples_per_problem will be {len(attempts)}",
              file=sys.stderr)
        k = len(attempts)
    failed_n = sum(1 for a in attempts if a.get("prefailed_reason"))
    if failed_n:
        print(f"  note: {failed_n}/{len(attempts)} sample(s) flagged as "
              f"prefailed (e.g. wrong-language). Recorded as failed attempts "
              f"with the raw response preserved in trace.",
              file=sys.stderr)

    # Decide whether to start a new submission or append this attempt to an
    # existing ongoing one. Precedence: --append-to (explicit) > --append-if-existing
    # (lookup) > new submission.
    # Auto-append by default: look for an ongoing submission whose experiment
    # cell matches this run, and add to it instead of creating a duplicate.
    # The cell key is (model, version, language, prompt_id, hints, target,
    # n_rounds) — see find_existing_submission's docstring for why every
    # field is significant. Explicit overrides: --append-to <ID> forces a
    # specific target; --new-submission forces a fresh one.
    target_sub_id: int | None = args.append_to
    if target_sub_id is None and not args.new_submission:
        existing = find_existing_submission(
            base, args.api_key, args.model, args.model_version, args.language,
            prompt_id=prompt_id,
            include_hints=bool(args.hints),
            optimization_target=args.target,
            n_rounds=1,        # this script is single-shot by design
        )
        if existing is not None:
            target_sub_id = existing["id"]
            print(f"• auto-append: found ongoing submission #{target_sub_id} "
                  f"({existing.get('total_solved', 0)}/{existing.get('total_problems', 0)} solved)")
        else:
            cell = (f"prompt_id={prompt_id} hints={bool(args.hints)} "
                    f"target={args.target or 'none'}")
            print(f"• auto-append: no matching ongoing submission "
                  f"(cell: {cell}) — creating a new one")

    if target_sub_id is not None:
        print(f"• appending {len(attempts)} attempt(s) to submission #{target_sub_id} …")
        resp = http_json(
            "POST", f"{base}/api/submissions/{target_sub_id}/attempts",
            token=args.api_key, body={"attempts": attempts},
        )
        sub_id = resp["submission_id"]
        print(f"  appended {resp['appended']} attempt(s) to submission #{sub_id}")
    else:
        print(f"• submitting {len(attempts)} attempt(s) as a new submission (k={k}) …")
        created = http_json(
            "POST", f"{base}/api/submissions", token=args.api_key,
            body={
                "model_name": args.model,
                "model_version": args.model_version,
                "language": args.language,
                # Persist the EXACT prompt-template text that was sent — the
                # registry resolution may have happened against a moving
                # target, so the prompt_template snapshot keeps the audit
                # honest alongside prompt_id.
                "prompt_template": (
                    registry_prompt["system_prompt"]
                    if registry_prompt is not None else system_prompt(args.language)
                ),
                "model_params": params,
                "n_samples_per_problem": k,
                "prompt_id": prompt_id,
                # Scope this submission to the chosen competition so
                # total_problems / pass@k use that competition's size
                # rather than the full Problem table count.
                "competition_slug": args.competition,
                "attempts": attempts,
            },
        )
        sub_id = created["submission_id"]
        print(f"  submission #{sub_id} created")

    print("• waiting for the 5×-dispatch to finish …")
    deadline = time.time() + args.poll_seconds
    detail = http_json("GET", f"{base}/api/submissions/{sub_id}")
    while time.time() < deadline and detail["submission"]["status"] != "judged":
        time.sleep(2)
        detail = http_json("GET", f"{base}/api/submissions/{sub_id}")

    sub = detail["submission"]
    print("\n=== result ===")
    print(f"submission #{sub_id}  status={sub['status']}")
    print(f"solved {sub['total_solved']}/{sub['total_problems']}  pass@1={sub['pass_at_1']}")
    print(f"avg runtime: {sub['avg_runtime_ms']} ms   best: {sub['best_runtime_ms']} ms")
    print(f"avg memory:  {sub['avg_memory_mb']} MB   best: {sub['best_memory_mb']} MB")
    for run in detail.get("runs", []):
        print(f"  {run['problem_slug']:<26} {run['status_msg']:<14} "
              f"rt={run['runtime_ms']}  mem={run['memory_mb']}")
    sample_leaf_set = set(sample_leafs)
    for idx, t in enumerate(detail.get("traces", []), start=1):
        tr = t.get("trace") or {}
        pv = t.get("provenance") or {}
        match = "MATCH" if pv.get("trace_hash") in sample_leaf_set else "MISMATCH"
        temp = (tr.get("params") or {}).get("temperature")
        print(f"  trace[{idx}] {t['problem_slug']}: provider={tr.get('provider')} "
              f"model={tr.get('model')} temp={temp} "
              f"reasoning={'yes' if tr.get('reasoning') else 'no'}")
        print(f"    leaf {match} · sig={pv.get('sig_status')} "
              f"· digest={(pv.get('model_digest') or '')[:24]} "
              f"({pv.get('model_digest_source')}) · log #{pv.get('log_seq')}")

    log = http_json("GET", f"{base}/api/provenance/log?limit=1")
    print(f"• transparency log: {log['count']} entries · "
          f"chain {'verified' if log['verified'] else 'BROKEN'} · "
          f"head {log['head'][:16]}…")


def _canned_trace(slug: str, language: str, params: dict,
                  reason: str) -> tuple[str, dict]:
    """`--no-model` fallback. Tiny correct solution for one problem in the
    sampled set so the demo path runs without an LLM. Anything else has to
    use --ollama-url --model."""
    if language != CANNED_LANGUAGE or slug != CANNED_SLUG:
        raise SystemExit(
            f"no canned solution for '{slug}' in {language}; run with Ollama "
            f"(or use --slug {CANNED_SLUG} --language {CANNED_LANGUAGE})"
        )
    sys_p = system_prompt(language)
    prompt = f"Solve this LeetCode problem `{slug}` in Python 3."
    return CANNED_CODE, {
        "prompt": prompt,
        "system_prompt": sys_p,
        "raw_response": "```python\n" + CANNED_CODE + "```",
        "reasoning": (
            "All a's must precede all b's iff the substring 'ba' never "
            "occurs in s — one linear scan via `in`. O(n) time, O(1) space."
        ),
        "model": "canned",
        "provider": "canned-fallback",
        "endpoint": None,
        "params": params,
        "created_at": _now(),
        "provider_meta": {"fallback_reason": reason},
    }


if __name__ == "__main__":
    main()
