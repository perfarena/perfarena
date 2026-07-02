# PerfArena API examples

## Provider plugin layer

Every script in this directory is provider-agnostic. Pick the backend with
`--provider {ollama,openai}`:

| Provider | Connection flags | Cost | Model digest source |
|---|---|---|---|
| `ollama` (default) | `--ollama-url` | always $0 | `/api/tags` SHA-256 (`ollama:<sha>`) |
| `openai` | `--openai-base-url` + `--openai-api-key` / `OPENAI_API_KEY` | computed from `usage.{prompt,completion}_tokens` × `OPENAI_PRICING_USD_PER_1M` (see `submit_with_local_model.py`) | `system_fingerprint` from a 1-token probe (`openai:<fingerprint>`) |

`--openai-base-url` defaults to `https://api.openai.com/v1` but any
OpenAI-compatible endpoint works: Azure OpenAI, OpenRouter, Together, vLLM,
llama.cpp's `--api-key` server, etc. Each invocation runs against exactly
one provider — see the answer to the `--model mixing` question in the
README's contributing notes if you want to extend that.

Add a new provider by subclassing `Provider` in
`submit_with_local_model.py` and registering it in the `PROVIDERS` dict.

## `submit_with_local_model.py`

End-to-end: pull a problem → ask the configured LLM for a Python solution
(capturing a verifiable provenance trace) → submit → poll until judged → print
results. Standard library only.

```bash
export ARENA_API_KEY=arena_xxxxxxxx          # your approved account's key

# --- OpenAI (gpt-4.1-mini): ---
export OPENAI_API_KEY=sk-…
python submit_with_local_model.py \
  --base-url http://localhost:8000 \
  --slug two-sum --language python3 \
  --provider openai --model gpt-4.1-mini --temperature 0.2

# --- Ollama (default endpoint http://localhost:11434): ---
python submit_with_local_model.py \
  --base-url http://localhost:8000 \
  --slug check-if-all-as-appears-before-all-bs \
  --language python3 \
  --ollama-url http://localhost:11434 \
  --model qwen2.5-coder:7b

# Any system-supported language works (system prompt + code-fence extraction
# adapt automatically): python3, cpp, c, rust, java, csharp, javascript,
# typescript, golang, kotlin, swift, scala, ruby, php.
python submit_with_local_model.py \
  --slug count-items-matching-a-rule --language cpp \
  --ollama-url http://localhost:11434 --model qwen2.5-coder:7b

# No Ollama — small canned python3 solution so the demo path runs anywhere
# (only the canned slug + python3 combo is supported; everything else needs
# a real model):
python submit_with_local_model.py \
  --slug check-if-all-as-appears-before-all-bs --no-model
```

### The provenance trace

So a reviewer can certify the model actually produced the solution, each
attempt carries a structured `trace` (stored by the platform, included in
submission detail and the dataset export):

| key             | meaning                                                     |
|-----------------|-------------------------------------------------------------|
| `prompt`        | exact user prompt sent to the model                         |
| `system_prompt` | system prompt used                                          |
| `raw_response`  | the model's full, unedited message content                  |
| `reasoning`     | the thinking channel (Ollama `message.thinking` or `<think>` tags), if the model emits one |
| `model` / `provider` / `endpoint` | which model answered and where        |
| `params`        | decoding params (temperature, …)                            |
| `created_at`    | ISO timestamp                                               |
| `provider_meta` | Ollama timings + token counts (`eval_count`, durations, …)  |

The script uses Ollama's native `/api/chat` with `think: true`, so reasoning
models (deepseek-r1, qwen3, …) expose their chain-of-thought in `reasoning`.

### Provenance integrity

This is the integrity/accountability layer (it proves a trace wasn't altered
or back-dated and binds it to you — it does *not* by itself prove the named
model produced the text):

* **Leaf hash** — `sha256(canonical_json({code, inference_trace, trace}))`.
  The script prints it; the platform recomputes it and anchors it in a public
  **append-only hash chain** (`GET /api/provenance/log`). The chain is
  tamper-evident: changing, reordering, or back-dating any entry breaks every
  later hash. `canonical_json` = `json.dumps(obj, sort_keys=True,
  separators=(",",":"), ensure_ascii=False)` — reproduce it exactly.
* **Model digest** — a provider-agnostic identity stored per attempt. For
  Ollama the script reads the real `sha256` from `/api/tags`
  (`ollama:<sha256>`). For other providers pass `--model-digest`
  (HF revision SHA, an OpenAI `system_fingerprint`, …). Omit it and the
  platform derives a deterministic digest from name/version/params/provider
  metadata (`derived:<sha256>`).
* **Signature (optional)** — `--gen-key PATH` once, then `--sign-key PATH` to
  Ed25519-sign the leaf hash. The platform verifies it and reports
  `sig_status` (`verified` / `invalid` / `unsigned` / `unsupported`). Needs
  the `cryptography` package; without it the script still runs, just unsigned.

The server echoes `provenance` back per attempt (`trace_hash`, `sig_status`,
`model_digest`, `model_digest_source`, `log_seq`, `logged_at`) — visible on the
submission page and included in the dataset export.

To build a *fully accepted* submission, loop over every supported slug
(`GET /api/problems`) and `POST /api/submissions/{id}/attempts` for each — see
the "Use the API directly" section on the Submit page.
