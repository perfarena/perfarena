# Design and tests

## Structure

The SDK is one core with two adapters (the CLI and the MCP server). Each
piece has one job:

| Piece | Pattern | Module |
|---|---|---|
| `Arena` | Facade | `perfarena.arena` |
| `RunSpec`, `RunSpec.builder()` | Builder, value object | `perfarena.spec` |
| `ArenaClient` and the resource groups | Gateway | `perfarena.api` |
| `Provider` registry and entry points | Strategy, plugin registry | `perfarena.llm.base` |
| `RetryOnConnectionError`, `HardTimeout` | Decorator | `perfarena.llm.middleware` |
| `PromptBuilder`, `LeetCodeShape`, `ClbgShape` | Template method, Strategy | `perfarena.prompts` |
| `CodePipeline` | Chain of responsibility | `perfarena.codegen` |
| Experiment strategies | Strategy | `perfarena.runs.strategies` |
| Cells and the planner | Command | `perfarena.runs.cells` |
| `Ledger`, `GenerationCache` | Repository | `perfarena.runs.ledger` |
| `EventBus` and the reporters | Observer | `perfarena.runs.events` |
| CLI, MCP server | Adapters over the SDK | `perfarena.cli`, `perfarena.mcp_server` |

The CLI flags of `run` and `spec show` come from the `RunSpec` fields. The
field descriptions are the help text of the flags and the text of
{doc}`reference/spec`. The CLI and the docs cannot drift from the SDK.

## Server endpoints for clients

Besides the public API, the SDK uses three endpoints that exist for API
clients:

| Endpoint | Use |
|---|---|
| `GET /api/attempts/{id}` | the state of one attempt (polling a verdict) |
| `PUT /api/submissions/upsert` | find or create the submission of a cell group |
| `GET /api/pricing` | per-model rates for the generation cost |

On an older server without them, the SDK falls back to the attempt list of
the submission and to its own matching rule, and records the cost as 0
where it has no rate.

## Tests

```bash
$ cd clients/python && .venv/bin/python -m pytest
```

The tests run the real backend (`webapp/backend`) in process on a temporary
database, with real dispatch to fake runners and a fake LLM over HTTP. The
SDK venv therefore also needs `pip install -r webapp/backend/requirements.txt`.

`test_parity_units.py`
: Compares the functions of the scripts with the SDK's: the prompts as sent
  for every seeded prompt, code extraction, language fingerprints, compile
  flags, cost, cell matching, refinement feedback, ordering, and the OpenAI
  and Ollama request bodies. With `PERFARENA_PARITY_DB=<copy of arena.db>`,
  it also compares extraction on 3,000 real model answers.

`test_parity_scripts.py`
: Runs `matrix_sweep.py`, `submit_with_local_model.py`, and
  `iterative_refine.py` as subprocesses, and the SDK, on the same experiment.
  The prompts, the attempts, and the submissions must be identical.

`test_run.py`, `test_mcp.py`, `test_spec_cli.py`, `test_client_providers.py`
: Cover the runs, the MCP server (in process and over stdio), the CLI, and
  the HTTP and provider layers.

## Differences from the scripts

These differences are on purpose:

- **Round 0 of a multi-round run** leaves `{{round_instruction}}` empty, as
  `matrix_sweep.py` does. `iterative_refine.py` fills it ("This is round 1
  of N").
- **A timed-out generation** is not retried. It is a result,
  `generation_timeout`. The scripts' HTTP helpers retried timeouts too.
- **The dispatch-failure recovery** waits once (`recovery_wait_s`), asks the
  server to dispatch again, and waits for the verdict again. Unlike
  `matrix_sweep.py`, it does not inspect the LeetCode account pool.
- **The generation cache** is used only when k=1, because with k samples one
  cache key would serve every sample.
- **Wrong-language generations** are not written to a separate
  `generate_rejected.jsonl`. They are submitted as prefailed attempts, and
  the trace keeps the raw answer.
- **Anthropic cost** comes from the server's rates (`GET /api/pricing`). The
  SDK has no local Anthropic price table.

## Build these docs

```bash
$ pip install -e "clients/python[docs]"
$ cd clients/python && .venv/bin/sphinx-build -b html docs docs/_build/html
$ python -m http.server 8001 --directory docs/_build/html
```

The build writes the generated pages to `docs/_generated/` (not in git).
