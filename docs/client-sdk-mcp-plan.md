# Plan: a PerfArena client SDK and an MCP server

Status: IMPLEMENTED (2026-10-13) in `clients/python/` (see its README). The
section "As built" at the end records the decisions and the changes against
this plan. The plan text below is kept as written.

## Goal

Replace the client scripts with one Python package, `perfarena`. The package
gives three interfaces on one core:

1. A Python SDK. A run and everything that defines it (model, LLM parameters,
   prompt template, competition, languages, oracles, replica policy, sampling,
   rounds, agent) is one declarative object, `RunSpec`.
2. A CLI that is a thin layer over the SDK. The CLI flags come from the
   `RunSpec` schema, so the flags cannot drift from the SDK.
3. An MCP server, so that an agent can read the arena, submit solutions,
   start experiments, and (when allowed) manage oracles and replicas.

## Why: what the scripts look like today

Survey of `webapp/examples/` and `scripts/` (line numbers as of 2026-10-12):

- **One script is the library.** `submit_with_local_model.py` (2,068 lines)
  holds the providers, prompts, code extraction, cost, provenance, and HTTP
  helpers. `matrix_sweep.py` (2,710 lines) imports 16 names from it through
  `sys.path` manipulation. `retry_until_accepted.py` rewrites `sys.argv` and
  calls `iterative_refine.main()`.
- **Very long functions.** `matrix_sweep.main()` is about 1,255 lines and
  `submit_with_local_model.main()` about 455.
- **The same option has different names and defaults in different scripts:**
  - `--temperature` is 0.2 in one script and 0 in another.
  - Rounds are `--rounds` in one script and `--n-rounds` in another.
  - `--harness` is required in one script, defaults to `arena-local` in
    another, and does not exist in a third.
  - The poll timeout is 120, 300, or 1800 s, chosen by checking whether a
    slug contains "clbg" or "leetcode".
  - The env names differ: `ARENA_BASE_URL` / `ARENA_API_KEY` in
    `examples/`, `ARENA_BASE` / `ARENA_KEY` in `scripts/`.
- **Real bugs:**
  - `submit_with_local_model.py` cannot create a submission: it omits the
    required `harness_slug`, so the request fails with 422.
  - It can also append to a submission of another competition or harness,
    because the lookup ignores both.
  - `iterative_refine.py` can stop polling in the middle of a dispatch, and it
    never finishes on a prefailed or dispatch-warned attempt.
- **Five HTTP helpers and three polling designs.** Errors are raised as
  `SystemExit`. The retry logic parses `"HTTP 5xx"` out of exception text.
- **Each provider copies the prompt pipeline.** Prompt building, the CLBG
  shape swap, think-stripping, and code normalization are repeated in each
  provider's `generate()`.
- **Mirrored tables drift from the backend.** The pricing table is
  duplicated in `main.py`. `MEASURABLE_LANGUAGES` disagrees with its own
  comment.
- **No client sends `agent_slug`**, and no client can manage oracles or
  replicas.
- **Almost no tests.** Three tests cover client code (`_ollama_post` retries
  and `_looks_like`).
- **Security:** `import_harness_measurements.py:36` has a full API key in its
  docstring, tracked in git. Rotate it.

## Architecture

Ports and adapters. The core knows nothing about argparse, MCP, or the
console. The CLI and the MCP server are adapters on the same application
services.

```
perfarena/
  config/      RunSpec, Profile, layered settings
  api/         ArenaClient + resources (typed REST client)
  llm/         Provider protocol, registry, middleware
  prompts/     PromptBuilder, competition shapes
  codegen/     extraction and normalization pipeline
  pricing/     PriceBook
  provenance/  trace, digest, signing
  runs/        planner, executor, strategies, waiter
  state/       ledger (resume) and generation cache
  events/      progress events and reporters
  cli/         `perfarena ...` commands (adapter)
  mcp/         MCP server (adapter)
```

### Main objects and the pattern each one uses

| Object | Pattern | Job |
|---|---|---|
| `Arena` | Facade | One entry point: `Arena.from_profile("bicho")` gives `.problems`, `.prompts`, `.submissions`, `.oracles`, `.replicas`, `.run(spec)`. |
| `RunSpec` | Builder + value object | Declares a run, all of it. Built fluently in code or loaded from a file. It is validated (pydantic), immutable, and hashable. |
| `Settings` | Layered configuration | Defaults < profile file < `PERFARENA_*` env < explicit kwargs/flags. The defaults are defined in one place. |
| `ArenaClient` + resources | Gateway / Repository | Typed calls over httpx with typed errors. Retries only idempotent calls. One concurrency cap per host (the public host must never be bulk-fetched). |
| `Provider` | Strategy + Registry (plugins through entry points) | `generate(Request) -> Completion`. Ollama, OpenAI-compatible, and a new Anthropic provider. Third parties register their own. |
| Provider middleware | Decorator chain | Retry and backoff, hard timeout, usage and cost metering, cache, energy probe. Each concern is written once and applies to every provider. |
| `PromptBuilder` + `CompetitionShape` | Template Method + Strategy | One prompt pipeline. The LeetCode shape and the CLBG shape are strategies, not `if "clbg" in slug` checks. |
| `CodePipeline` | Chain of Responsibility | Strip think blocks, extract the fence, check the language fingerprint, apply language normalizers, read the compile flags. Each step is small and tested. |
| `ExperimentStrategy` | Strategy + Template Method | `SingleShot`, `KSamples`, `IterativeRefine`, and `RetryUntilAccepted` replace three scripts and the argv rewrite. |
| `Planner` / `Cell` | Command | A cell is (model, language, prompt, problem, sample, round). Cells are serializable, so a run can do dry runs, resume, retry, and record a ledger. |
| `Executor` | Pipeline | Generate, submit, wait for the verdict, refine. Concurrency is set by the spec. |
| `AttemptWaiter` | Strategy | One definition of "terminal attempt" (it uses the queue states the backend now has). It polls now and can use server push later. |
| `Ledger`, `GenerationCache` | Repository | Replaces `sweep_log.json` and the ad hoc cache. SQLite or JSONL behind one interface. |
| `EventBus` | Observer | Progress events go to the console reporter, a JSONL log, and MCP notifications. |
| Errors | Exception hierarchy | `ArenaHTTPError(status, body)`, `ProviderError(transient)`, `SpecError`. No `SystemExit` below the CLI. |

### What a run looks like

In code:

```python
from perfarena import Arena, RunSpec

spec = (RunSpec.builder()
        .model("gpt-5.5", provider="openai", temperature=0.0)
        .prompt("experiment-system")
        .competition("leetcode-99-perf").languages("python3", "cpp")
        .oracles(performance="arena-linux-local", correctness="arena-local")
        .replicas(performance="split_repeats", correctness="least_busy")
        .samples(k=1).rounds(3, stop_on_accept=True)
        .build())

arena = Arena.from_profile("bicho")
result = arena.run(spec)              # resumable; progress through events
```

The same run as a file, which the CLI and MCP also accept:

```yaml
model: {name: gpt-5.5, provider: openai, params: {temperature: 0.0}}
prompt: experiment-system
competition: leetcode-99-perf
languages: [python3, cpp]
oracles: {performance: arena-linux-local, correctness: arena-local}
replicas: {performance: split_repeats, correctness: least_busy}
sampling: {k: 1}
rounds: {n: 3, stop_on_accept: true}
```

`perfarena run spec.yaml`, `perfarena run --model gpt-5.5 --languages python3`
(flags generated from the schema), and the MCP tool `start_run(spec)` all
end in the same `Arena.run`.

Replica policy in a spec. The policy is set on the oracle (server side),
because many runs share it. The SDK therefore applies `replicas:` through
the admin API, and only when the caller is admin and the spec asks for it.
Otherwise the SDK only checks that the current policy matches and warns.
This is a decision for discussion (question 5).

## Backend changes that make the client simpler

These are small and can go in parallel. The SDK falls back to today's
behavior when an endpoint is missing.

1. `GET /api/attempts/{id}` returns a machine-readable state (`queued`,
   `running`, `done`, `failed`) built from `dispatch_state`, `final_status`,
   and `dispatch_warning`. This removes the status-string heuristics.
2. `PUT /api/submissions/upsert` takes a "cell key" (model, version,
   language, prompt, competition, oracles, agent, knobs) and returns the
   matching submission or creates it. This replaces `find_existing_submission`
   on the client.
3. `GET /api/pricing` serves the per-model prices that are already on
   `RegisteredModel`, so the client stops carrying its own table.
4. CI contract test: generate the API models from the FastAPI OpenAPI schema
   and fail the build when the SDK and the server disagree.

## MCP server

Built with the official `mcp` Python SDK (FastMCP). It runs over stdio for a
local agent such as Claude Code, and over streamable HTTP when hosted. Every
tool calls the SDK. No tool has its own HTTP code.

Tools (annotated with `readOnlyHint` / `destructiveHint`):

- **Read:**
  - `list_competitions`, `list_problems(competition)`, `get_problem(slug, language)`
    (statement plus the starter snippet)
  - `list_prompts`, `list_models`
  - `get_rankings(competition, metric)`, `compare(models, languages)`
  - `get_submission(id)`, `list_attempts(submission)`,
    `get_attempt(id)` (code, runs, error log)
  - `list_oracles`, `list_replicas(oracle)`, `queue_status`
- **Act:**
  - `submit_solution(problem, language, code, model, agent?)` returns an
    attempt id.
  - `wait_for_verdict(attempt_id, timeout_s)`.
  - `start_run(spec)` returns a job id. The job reports progress through MCP
    notifications. `get_run(job_id)` and `cancel_run(job_id)` complete the
    set.
- **Admin, off unless the server starts with `--allow-admin`:**
  - `add_replica`, `set_replica_policy`, `calibrate_replica`
  - `redispatch_submission`

Resources:

- `perfarena://problems/{slug}`
- `perfarena://submissions/{id}`
- `perfarena://rankings/{competition}`
- `perfarena://specs/{name}`: saved run specs.

Prompts: `solve-problem(slug, language)` expands the registry prompt. An
agent can then be the competitor itself: read the problem, generate code,
`submit_solution` with its `agent` slug, read the verdict, and refine. This
makes agentic producers (the `Agent` table) first-class without a new script.

Safety:

- The API key comes from the profile or env and never appears in tool output.
- The server is read-only by default.
- Admin tools need the flag and an admin key.
- The per-host request cap applies, so an agent cannot bulk-fetch the public
  host.

## Migration (each phase ships, old scripts keep working)

| Phase | Content | Done when |
|---|---|---|
| A | Package skeleton, `Settings`/profiles, `ArenaClient`, typed errors. | Request bodies match fixtures recorded from today's scripts. |
| B | Providers, middleware, `PromptBuilder`, `CodePipeline`, `PriceBook`. | Parity tests pass on a corpus of stored model responses (`attempt.trace` holds the raw responses). |
| C | `RunSpec`, strategies, planner, executor, waiter, ledger, cache; CLI `run`, `sweep`, `refine`. | One small real sweep gives the same ledger and request bodies as `matrix_sweep.py`. |
| D | MCP server: read tools, then submit and wait, then runs as jobs, then admin behind the flag. | An agent solves and submits one problem end to end through MCP. |
| E | Backend endpoints 1 to 4. | The SDK no longer uses its fallbacks. |
| F | Old scripts become shims that print a deprecation line and call the CLI. Delete them after one release. | No imports of `submit_with_local_model` remain. |

Tests: unit tests with a fake provider and an `httpx.MockTransport` arena;
contract tests against the real FastAPI app in process (`TestClient`); end
to end with the fake runners from `tests/replica_fakes.py`.

## Questions for you

1. **Where the package lives:** `clients/python/` in this repo (one PR
   changes server and client together; my recommendation), or its own repo?
   Publish to PyPI, or install from git only?
2. **Spec file format:** YAML (readable, supports comments) or TOML (already
   the Python packaging format)? I recommend YAML for specs and TOML for
   profiles.
3. **CLI library:** Typer (generated help, one dependency) or argparse (no
   dependency)?
4. **Sync or async core:** I recommend a sync core with threads (it matches
   today's code and is easy to debug), with the MCP server wrapping calls in
   `anyio.to_thread`. Async everywhere is the alternative.
5. **Replica policy in a spec:** should a run be allowed to change an oracle's
   policy (admin only), or only assert it?
6. **MCP scope for non-admins:** `POST /api/submissions` is admin-only today.
   Should MCP submission tools stay admin-only, or should we add a scoped
   "competitor" key?
7. **Server-side runs later?** Today the client calls the LLMs, so LLM keys
   stay with the user. Should we keep that, or plan for runs as backend jobs?
8. **Legacy formats:** migrate `data/sweep_log.json` (committed today) into
   the new ledger, or start fresh?

## As built (2026-10-13)

Decisions (your answers): the package lives in `clients/python/` in this repo.
A spec's `replicas:` only checks the live policy. MCP write tools need an
admin key. The backend helper endpoints are added. The client scripts are not
reduced to shims: they stay maintained as the reference implementation, and
the parity tests check the SDK against them. Defaults taken: YAML specs and
TOML profiles, an argparse CLI generated from the schema, a sync core with
threads, and LLM calls on the client.

Backend (additive):

- `GET /api/attempts/{id}`: state `queued` | `running` | `done` | `failed`, the
  verdict, the metrics, the per-run error logs, and the code.
- `PUT /api/submissions/upsert`: find-or-create by cell. With `create: false`
  it only looks.
- `GET /api/pricing`.
- `POST /api/submissions` and `POST /api/submissions/{id}/attempts` now also
  return `attempt_ids`.

The code is in `webapp/backend/app/sdk_api.py`, with tests in
`webapp/backend/tests/test_sdk_api.py`.

Scripts fixed (they are still the reference):

- `submit_with_local_model.py` could not create a submission. It now takes
  `--harness` and `--correctness-oracle`, and scopes its lookup to the
  competition and the harness.
- `iterative_refine.py` read results in the middle of a dispatch and waited
  until its timeout on a prefailed or dispatch-warned attempt. It now uses
  the sweep's terminal-state rule.
- The language-list comments now agree with `MEASURABLE_LANGUAGES`.

Changes against the plan:

- **Old scripts as shims (phase F) is dropped:** the scripts stay, as asked.
- **`compare_models`** takes `models`, `language`, and `competition` (the
  arguments of `/api/compare`).
- **MCP list tools** return one JSON object (`{"problems": [...]}`): the MCP
  SDK would split a list into separate content blocks.
- **The OpenAPI contract test (backend item 4)** is not built. The SDK tests
  instead run against the real backend in process.

The intended differences from the scripts are listed in
`clients/python/README.md` ("Differences from the scripts, on purpose").
