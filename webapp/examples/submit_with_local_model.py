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
}


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
) -> dict | None:
    """Most recent ongoing submission for this user that matches the FULL
    experiment cell — model + language + prompt + every placeholder-affecting
    knob.

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

    `prompt_id=None` matches against submissions that ALSO have no
    prompt_id (i.e. ad-hoc prompts). The text snapshot in
    `prompt_template` itself is NOT compared — relying on the registered
    id is more robust than diffing prose, and an ad-hoc-with-different-text
    case is rare enough that the small risk of accidental merge is
    acceptable.

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
        matches.append(s)
    matches.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return matches[0] if matches else None


def ollama_model_digest(ollama_url: str, model: str) -> str | None:
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
    "python3":    [r"^\s*def\s+\w+\s*\(", r"^\s*class\s+\w+\s*[:\(]",
                   r"^\s*from\s+\w+\s+import\b", r"->\s*[\w\[]"],
    "cpp":        [r"^\s*#include", r"\bvector\s*<", r"^\s*class\s+\w+\s*\{",
                   r"\bpublic\s*:", r"\bstd::"],
    "c":          [r"^\s*#include", r"\bint\s+main\s*\(",
                   r"\bstruct\s+\w+\s*\{", r"\bprintf\s*\("],
    "java":       [r"^\s*public\s+class\s+\w+",
                   r"\bpublic\s+[\w\[\]<>]+\s+\w+\s*\([^)]*\)\s*\{",
                   r"\bSystem\.out\.", r"\bimport\s+java\.",
                   r"\bnew\s+\w+\s*\[",          # `new int[]{...}`
                   r"\b(?:int|long|double|float|boolean|char|String)\s*\[\]"],
    "csharp":     [r"\bpublic\s+class\s+\w+", r"\busing\s+System\b",
                   r"\bConsole\.\w+\("],
    "javascript": [r"^\s*function\s+\w+\s*\(", r"\bvar\s+\w+\s*=",
                   r"\blet\s+\w+\s*=", r"\bconst\s+\w+\s*=", r"=>"],
    "typescript": [r":\s*(string|number|boolean|void)\b", r"\binterface\s+\w+",
                   r"^\s*function\s+\w+\s*\(.*:.*\)\s*:"],
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
}


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


def call_cost_usd(provider: str | None) -> float:
    """$ cost of one LLM call. Ollama / canned / local are free (0.0). For a
    paid API you'd plug provider pricing × tokens here; until then anything
    non-local returns 0.0 too (the submitter can override per attempt)."""
    # Per the requirement: Ollama models cost 0. Everything we currently
    # drive is local, so 0.0 is correct; left as a function so paid-provider
    # pricing has an obvious home later.
    return 0.0


def fetch_prompt(base: str, name: str) -> dict:
    """Pull a registry prompt by name from the public catalog."""
    prompts = http_json("GET", f"{base.rstrip('/')}/api/prompts")
    matched = [p for p in prompts if p.get("name") == name]
    if not matched:
        names = ", ".join(p["name"] for p in prompts) or "(none)"
        raise SystemExit(f"prompt '{name}' not found in registry. Available: {names}")
    return matched[0]


def call_ollama(ollama_url: str, model: str, statement: str, slug: str,
                params: dict, language: str,
                registry_prompt: dict | None = None,
                extras: dict[str, str] | None = None) -> tuple[str, dict]:
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
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_prompt},
        ],
        "think": True,           # ask reasoning models to expose the thinking channel
        "stream": False,
        "options": params,
    }
    endpoint = ollama_url.rstrip("/") + "/api/chat"
    # Wall-clock of the generation process for this solution. Measured around
    # the full call (including a no-think retry) so it reflects what the client
    # actually waited; persisted to the attempt as wall_clock_ms and summed into
    # the submission's total_generation_ms.
    gen_t0 = time.perf_counter()
    status, resp = _ollama_post(endpoint, payload, timeout=600)
    # Non-thinking models reject `think`; retry once without it.
    if status == 400 and "thinking" in json.dumps(resp).lower():
        print("  (model has no thinking channel — retrying without think)",
              file=sys.stderr)
        payload.pop("think", None)
        status, resp = _ollama_post(endpoint, payload, timeout=600)
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
    code = _extract_code(cleaned, fence_lang=fence)
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
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--model-version", default="ollama")
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
    ap.add_argument("--poll-seconds", type=int, default=60)
    args = ap.parse_args()

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

    model_digest = args.model_digest
    if not args.no_model and not model_digest:
        # One lookup per run (not per sample) — the digest is identical for
        # every call to the same model.
        model_digest = ollama_model_digest(args.ollama_url, args.model)
        if model_digest:
            print(f"  model digest (from Ollama): {model_digest[:24]}…")

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
            print(f"  [{i}/{k}] asking {args.model} at temperature={t} …")
            try:
                code, trace = call_ollama(
                    args.ollama_url, args.model, statement, args.slug,
                    sample_params, args.language,
                    registry_prompt=registry_prompt,
                    extras=extras,
                )
                rlen = len(trace["reasoning"] or "")
                print(f"  [{i}/{k}] got {len(code)} chars of code; reasoning trace: {rlen} chars")
            except SystemExit as e:
                print(f"  [{i}/{k}] Ollama call failed ({e}); using canned solution",
                      file=sys.stderr)
                code, trace = _canned_trace(
                    args.slug, args.language, sample_params,
                    reason=f"ollama-unreachable: {e}",
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
        leaf = compute_trace_hash(code, summary, trace)
        print(f"  [{i}/{k}] trace leaf hash: {leaf}")

        attempt: dict = {
            "title_slug": args.slug,
            "code": code,
            "inference_trace": summary,
            "trace": trace,
            # Cost + tokens of this single LLM call (one sample). Ollama is
            # free → 0.0; response_tokens counts the CODE only, not reasoning.
            "cost_usd": call_cost_usd(trace.get("provider")),
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
