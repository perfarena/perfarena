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


def _ollama_post(url: str, body: dict, timeout: int) -> tuple[int, object]:
    """POST to Ollama. Return (status, parsed_json_or_text) WITHOUT raising on
    HTTP errors, so the caller can react to e.g. 'does not support thinking'.
    Connection-level failures still raise SystemExit (→ canned fallback)."""
    data = json.dumps(body).encode()
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
        raise SystemExit(f"cannot reach {url}: {e}")


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
) -> dict | None:
    """Most recent ongoing submission for this user matching the model header.

    "Ongoing" = `fully_accepted` is False (i.e. there's still room to add more
    problems). Returns the matched submission dict or None.
    """
    subs = http_json("GET", f"{base}/api/me/submissions", token=key)
    matches = [
        s for s in subs
        if s.get("model_name") == model_name
        and s.get("model_version") == model_version
        and s.get("language") == language
        and not s.get("fully_accepted", False)
    ]
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


def _expand_template(text: str, *, slug: str, statement: str, language: str) -> str:
    """Replace the {{...}} placeholders used by registry prompt templates."""
    display, fence = LANGUAGE_DISPLAY.get(language, (language, language))
    repl = {
        "{{slug}}": slug, "{{statement}}": statement,
        "{{language}}": language,
        "{{language_display}}": display,
        "{{language_fence}}": fence,
    }
    out = text
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


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
                registry_prompt: dict | None = None) -> tuple[str, dict]:
    """Call Ollama's native /api/chat (think=on). Return (code, trace).
    `language` selects the prompt wording and the preferred code-fence tag.
    If `registry_prompt` is given, its system_prompt + user_prompt_template
    are used after placeholder expansion; otherwise we fall back to the
    inline `system_prompt(language)` + slug/statement string."""
    display, fence = LANGUAGE_DISPLAY.get(language, (language, language))
    if registry_prompt is not None:
        sys_p = _expand_template(
            registry_prompt["system_prompt"],
            slug=slug, statement=statement, language=language,
        )
        user_prompt = _expand_template(
            registry_prompt["user_prompt_template"],
            slug=slug, statement=statement, language=language,
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
    status, resp = _ollama_post(endpoint, payload, timeout=600)
    # Non-thinking models reject `think`; retry once without it.
    if status == 400 and "thinking" in json.dumps(resp).lower():
        print("  (model has no thinking channel — retrying without think)",
              file=sys.stderr)
        payload.pop("think", None)
        status, resp = _ollama_post(endpoint, payload, timeout=600)
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
    ap.add_argument("--append-if-existing", action="store_true",
                    help="if an ongoing submission exists for the same "
                         "(model_name, model_version, language), append this "
                         "attempt to it instead of creating a new submission")
    ap.add_argument("--append-to", type=int, default=None, metavar="SUB_ID",
                    help="append this attempt to a specific submission id "
                         "(skips the search; overrides --append-if-existing)")
    ap.add_argument("--samples-per-problem", "-k", type=int, default=1, metavar="K",
                    help="generate K solutions for the slug (temperature-varied) "
                         "and submit them all as one pass@k attempt set; sets "
                         "the new submission's n_samples_per_problem=K")
    ap.add_argument("--prompt", default=None, metavar="NAME",
                    help="use a registry prompt by name (GET /api/prompts) "
                         "instead of the inline default; the resulting "
                         "submission is stamped with prompt_id for filtering")
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
    for i, t in enumerate(temps, start=1):
        sample_params = {"temperature": t}
        if args.no_model:
            code, trace = _canned_trace(
                args.slug, args.language, sample_params, reason="--no-model",
            )
            print(f"  [{i}/{k}] canned ({args.language})")
        else:
            display, _ = LANGUAGE_DISPLAY[args.language]
            print(f"  [{i}/{k}] asking {args.model} at temperature={t} …")
            try:
                code, trace = call_ollama(
                    args.ollama_url, args.model, statement, args.slug,
                    sample_params, args.language,
                    registry_prompt=registry_prompt,
                )
                rlen = len(trace["reasoning"] or "")
                print(f"     got {len(code)} chars of code; reasoning trace: {rlen} chars")
            except SystemExit as e:
                print(f"     Ollama call failed ({e}); using canned solution",
                      file=sys.stderr)
                code, trace = _canned_trace(
                    args.slug, args.language, sample_params,
                    reason=f"ollama-unreachable: {e}",
                )
        # inference_trace = required human-readable summary (≥ 20 chars);
        # the full structured provenance goes in `trace`.
        summary = (trace.get("reasoning") or trace.get("raw_response")
                   or "model produced the solution; see structured trace").strip()
        summary = summary[:4000] or "solution generated by model"
        leaf = compute_trace_hash(code, summary, trace)
        print(f"     trace leaf hash: {leaf}")

        attempt: dict = {
            "title_slug": args.slug,
            "code": code,
            "inference_trace": summary,
            "trace": trace,
        }
        if model_digest:
            attempt["model_digest"] = model_digest
        if args.sign_key:
            signed = sign_leaf(args.sign_key, leaf)
            if signed:
                attempt["trace_sig"], attempt["trace_signer"] = signed
        attempts.append(attempt)
        sample_leafs.append(leaf)

    # Decide whether to start a new submission or append this attempt to an
    # existing ongoing one. Precedence: --append-to (explicit) > --append-if-existing
    # (lookup) > new submission.
    target_sub_id: int | None = args.append_to
    if target_sub_id is None and args.append_if_existing:
        existing = find_existing_submission(
            base, args.api_key, args.model, args.model_version, args.language,
        )
        if existing is not None:
            target_sub_id = existing["id"]
            print(f"• found ongoing submission #{target_sub_id} "
                  f"({existing.get('total_solved', 0)}/{existing.get('total_problems', 0)} solved) "
                  f"— will append")
        else:
            print("• no matching ongoing submission — creating a new one")

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
