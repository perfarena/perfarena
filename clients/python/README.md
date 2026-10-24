# perfarena: SDK, CLI, and MCP server

`perfarena` is the Python client of the PerfArena platform. It has three
interfaces on one core:

- an **SDK**: one `RunSpec` describes a run, and `Arena.run(spec)` executes it;
- a **CLI** (`perfarena`), whose flags are generated from the `RunSpec` schema;
- an **MCP server** (`perfarena mcp`), so an agent can read the arena, submit
  solutions, and start runs.

The client scripts in `webapp/examples/` stay maintained. They are the
reference implementation: the parity tests run them and the SDK on the same
inputs and require identical results (see "Tests").

## Install

```bash
pip install -e clients/python            # SDK + CLI
pip install -e "clients/python[mcp]"     # + MCP server
pip install -e "clients/python[sign]"    # + Ed25519 trace signing
pip install -e "clients/python[dev]"     # + tests
pip install -e "clients/python[docs]"    # + Sphinx, to build docs/
```

Python 3.11 or later.

## Documentation

The full documentation (guides, every spec field, the CLI help, the MCP
tools, and the Python API) is a Sphinx site in `docs/`:

```bash
pip install -e "clients/python[docs]"
cd clients/python && .venv/bin/sphinx-build -b html docs docs/_build/html
python -m http.server 8001 --directory docs/_build/html   # http://127.0.0.1:8001
```

## Connect

`Settings.resolve()` reads, from lowest to highest priority: defaults, a
profile, the environment (`PERFARENA_BASE_URL` / `PERFARENA_API_KEY`, or the
scripts' `ARENA_BASE_URL` / `ARENA_API_KEY`), then explicit arguments. An
explicitly named profile wins over the environment, so a local key in your
shell is never sent to another host. Profiles never hold a key, only where to
read it:

```toml
# ~/.config/perfarena/profiles.toml   (or $PERFARENA_PROFILES)
[default]
base_url = "http://localhost:8000"
api_key_env = "ARENA_API_KEY"

[prod]
base_url = "https://perfarena.ngrok.app"
api_key_file = "~/.perfarena_admin_key"
```

The client caps parallel requests per host: 2 for a public host and 8 for
localhost (`max_concurrency` overrides). Only idempotent calls are retried, so
a retry can never create a duplicate submission.

## Run an experiment

```python
from perfarena import Arena, RunSpec

spec = (RunSpec.builder()
        .model("gpt-5.5", provider="openai", temperature=0.0)
        .prompt("experiment-system")
        .competition("leetcode-99-perf").languages("python3", "cpp")
        .oracles("arena-linux-local", "arena-local")
        .rounds(3, stop_on_accept=True)
        .build())

result = Arena.from_profile("prod").run(spec, ledger="ledger.json")
print(result.completed, result.failed, result.submissions)
```

The same spec as YAML (`perfarena spec init` writes a template):

```yaml
model: {name: gpt-5.5, provider: openai, params: {temperature: 0.0}}
prompt: experiment-system
competition: leetcode-99-perf
languages: [python3, cpp]
oracles: {performance: arena-linux-local, correctness: arena-local}
rounds: {n: 3, stop_on_accept: true}
```

```bash
perfarena --profile prod run spec.yaml
perfarena --profile prod run spec.yaml --rounds 1 --languages java   # flags override the file
perfarena run --help                                                  # every spec field
```

What a run does per cell (model × prompt × language × problem), with the
rules of `matrix_sweep.py`:

- **Submission:** one submission per (model, version, prompt, language),
  found or created through `PUT /api/submissions/upsert`. A problem that
  submission already covers is skipped.
- **Each round:** generate → build the attempt → submit → wait for the
  verdict (`GET /api/attempts/{id}`). The next round sees the previous code
  and the judge's verdict.
- **Failed generation:** a model failure (bad request, timeout) becomes a
  prefailed attempt, so pass@1 counts it. An infrastructure failure
  (rate limit, gateway) becomes a retryable ledger entry.
- **Wrong language:** code that does not look like the target language is
  submitted as a prefailed attempt, with the raw answer kept in the trace.
- **Resume:** the ledger makes a run resumable. An in-flight attempt left by
  an earlier run is polled again, not resubmitted.
- **Cache:** the generation cache (`--cache DIR`) has the same layout as
  `matrix_sweep.py`'s `sweep_cache/`, so the two can share generations.
- **LeetCode:** when LeetCode judges the run, cells run one at a time and are
  paced.

Strategies (chosen from the spec, or pass your own `ExperimentStrategy`):
`SingleShot`, `KSamples` (k samples, temperatures spread), `IterativeRefine`
(n rounds, all run), and `RetryUntilAccepted` (stop at the first Accepted).

`spec.replicas` only checks. A run refuses to start when an oracle's live
replica policy differs from the spec. It never changes the policy: an admin
sets it on the oracle.

Progress goes to an `EventBus`. Subscribe a `ConsoleReporter`, a
`JsonlReporter(path)`, or your own callable.

## Submit code you already have

```python
arena.submit_solution("two-sum", "python3", code, model="my-agent",
                      harness="arena-local", correctness="arena-local",
                      agent="claude-code", wait_s=600)
```

`perfarena submit --problem two-sum --language python3 --file sol.py --model m --harness h --wait 600`
does the same from the shell. Submitting needs an admin key, as the REST API does.

## MCP server

```bash
perfarena --profile prod mcp                          # stdio, for a local agent
perfarena mcp --transport streamable-http --port 8765
perfarena --profile prod mcp --allow-admin            # + replica/redispatch tools
```

Claude Code: `claude mcp add perfarena -- perfarena --profile prod mcp`.

- **Read tools** (any key, or none): `list_competitions`, `list_problems`,
  `get_problem`, `list_prompts`, `list_models`, `get_rankings`,
  `compare_models`, `get_submission`, `list_attempts`, `get_attempt`,
  `list_oracles`, `list_replicas`, `get_run`.
- **Write tools** (admin key): `submit_solution`, `wait_for_verdict` (with
  progress notifications), `start_run` (a background job), `cancel_run`.
- **Admin tools** (`--allow-admin` + admin key): `add_replica`,
  `set_replica_status`, `calibrate_replica`, `redispatch_submission`.
- **Resources:** `perfarena://problems/{slug}`,
  `perfarena://submissions/{id}`, `perfarena://rankings/{competition}`.
- **Prompt:** `solve_problem(slug, language, prompt)`.

The server never returns an oracle's config (it can hold runner tokens) and
never echoes the API key. SDK errors reach the agent as tool errors with their
message.

## Providers

Built in: `ollama`, `openai` (and OpenAI-compatible endpoints: Azure,
OpenRouter, vLLM), `anthropic`, and `fake` (scripted, for tests and dry
runs). A provider does transport only. Prompt building and code extraction
happen once, above it. Every provider is wrapped in the same middleware:
`RetryOnConnectionError` (5 tries with back-off; HTTP errors and timeouts are
not retried) and `HardTimeout` (a wall-clock cap).

Add a provider from another package through the entry point group
`perfarena.providers`. Each entry point names a factory
`factory(model_spec) -> Provider`:

```toml
[project.entry-points."perfarena.providers"]
mybackend = "mypkg.provider:make"
```

## Design

| Piece | Pattern | Module |
|---|---|---|
| `Arena` | Facade | `arena.py` |
| `RunSpec`, `RunSpec.builder()` | Builder, value object | `spec.py` |
| `ArenaClient` + resource groups | Gateway | `api/` |
| `Provider` registry + entry points | Strategy, plugin registry | `llm/base.py` |
| `RetryOnConnectionError`, `HardTimeout` | Decorator | `llm/middleware.py` |
| `PromptBuilder`, `LeetCodeShape` / `ClbgShape` | Template method, Strategy | `prompts.py` |
| `CodePipeline` | Chain of responsibility | `codegen.py` |
| Experiment strategies | Strategy | `runs/strategies.py` |
| Cells, planner | Command | `runs/cells.py` |
| `Ledger`, `GenerationCache` | Repository | `runs/ledger.py` |
| `EventBus`, reporters | Observer | `runs/events.py` |
| CLI, MCP server | Adapters over the SDK | `cli.py`, `mcp_server.py` |

## Tests

```bash
cd clients/python && .venv/bin/python -m pytest
```

The tests run the real backend (`webapp/backend`) in process on a temporary
database, with real dispatch to fake runners and a fake LLM over HTTP. The SDK
venv therefore also needs `pip install -r webapp/backend/requirements.txt`.

- `test_parity_units.py` checks the scripts' functions against the SDK's:
  prompts as sent on the wire for every seeded prompt, extraction, language
  fingerprints, compile flags, cost, cell matching, refinement feedback,
  ordering, and the OpenAI and Ollama request bodies.
  `PERFARENA_PARITY_DB=<copy of arena.db>` adds extraction parity on 3,000
  real model answers.
- `test_parity_scripts.py` runs `matrix_sweep.py`,
  `submit_with_local_model.py`, and `iterative_refine.py` as subprocesses, and
  the SDK, on the same experiment. The prompts, attempts, and submissions must
  be identical.
- `test_run.py`, `test_mcp.py`, `test_spec_cli.py`, and
  `test_client_providers.py` cover the runs, the MCP server (in process and
  over stdio), the CLI, and the HTTP and provider layers.

## Differences from the scripts, on purpose

- **Round 0 of a multi-round run** leaves `{{round_instruction}}` empty, as
  `matrix_sweep.py` does. `iterative_refine.py` fills it ("This is round 1 of
  N").
- **A timed-out generation** is not retried (it is a result:
  `generation_timeout`). The scripts' HTTP helpers retried timeouts too.
- **The dispatch-failure recovery** waits once (`recovery_wait_s`), asks the
  server to dispatch again, and waits for the verdict again. Unlike
  `matrix_sweep.py`, it does not inspect the LeetCode account pool.
- **The generation cache** is used only when k=1 (with k samples, one cache
  key would serve every sample).
- **Wrong-language generations** are not written to a separate
  `generate_rejected.jsonl`. They are submitted as prefailed attempts, and
  the raw answer stays in the trace.
- **Anthropic cost** comes from the server's rates (`GET /api/pricing`). The
  SDK has no local Anthropic price table.
