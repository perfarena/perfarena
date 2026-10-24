# Run an experiment

A run generates code with one or more models, submits each solution to the
arena, and waits for the verdicts. A {py:class}`~perfarena.spec.RunSpec`
says what to run. {py:meth}`Arena.run <perfarena.arena.Arena.run>` executes
it.

## Write a spec

Build a spec in code:

```python
from perfarena import RunSpec

spec = (RunSpec.builder()
        .model("gpt-5.5", provider="openai", temperature=0.0)
        .prompt("experiment-system")
        .competition("leetcode-99-perf").languages("python3", "cpp")
        .oracles("arena-linux-local", "arena-local")
        .rounds(3, stop_on_accept=True)
        .build())
```

or write the same spec as YAML:

```yaml
model: {name: gpt-5.5, provider: openai, params: {temperature: 0.0}}
prompt: experiment-system
competition: leetcode-99-perf
languages: [python3, cpp]
oracles: {performance: arena-linux-local, correctness: arena-local}
rounds: {n: 3, stop_on_accept: true}
```

`perfarena spec init` writes a template. `RunSpec.load(path)` reads a file,
and `spec.to_yaml()` writes one. A spec is immutable: `spec.with_changes(...)`
gives a changed copy, and `spec.digest()` is its identity. A spec that is not
valid raises {py:class}`~perfarena.errors.SpecError`. {doc}`reference/spec`
lists every field.

To test several models or prompts in one run, give `models:` and `prompts:`
as lists. Each builder call `.model(...)` adds one model.

## Run it

```python
from perfarena import Arena

arena = Arena.from_profile("prod")
result = arena.run(spec, ledger="ledger.json", cache="sweep_cache")
print(result.run_id, result.completed, result.skipped, result.failed)
print(result.submissions)        # {"model|version|prompt|language": submission_id}
```

From the shell, flags override the fields of the spec file:

```bash
$ perfarena --profile prod run spec.yaml
$ perfarena --profile prod run spec.yaml --rounds 1 --languages java
$ perfarena run spec.yaml --dry-run          # print the resolved spec and exit
```

## What a run does

The run makes one cell per (model, prompt, language, problem). It follows the
rules of `matrix_sweep.py` for each cell:

1. **Submission.** It finds or creates the submission of the cell's group
   ({doc}`concepts`). A problem that the submission already covers is
   skipped.
2. **Rounds.** Each round generates code, builds the attempt, submits it,
   and waits for the verdict (`GET /api/attempts/{id}`). The next round sees
   the previous code and the verdict.
3. **Failed generation.** A model failure (a bad request, a timeout) becomes
   a prefailed attempt, so pass@1 counts it. An infrastructure failure (a
   rate limit, a gateway error) becomes a ledger entry that the next run
   retries.
4. **Wrong language.** Code that does not look like the target language is
   submitted as a prefailed attempt (`wrong_language_extracted`). The trace
   keeps the raw answer.
5. **Dispatch failure.** When the runner or the LeetCode account pool is
   down, the run waits once (`recovery_wait_s`), asks the server to dispatch
   again, and waits for the verdict again.

When LeetCode judges the run, the cells run one at a time, with a pause
between them (`inter_cell_delay_s`, `inter_problem_delay_s`).

## Strategies

The spec chooses the strategy:

| Spec | Strategy | Samples per cell | Rounds per sample |
|---|---|---|---|
| defaults | `SingleShot` | 1 | 1 |
| `sampling.k > 1` | `KSamples` | k | 1 |
| `rounds.n > 1`, `stop_on_accept: false` | `IterativeRefine` | k | n, all of them |
| `rounds.n > 1`, `stop_on_accept: true` | `RetryUntilAccepted` | k | up to n, stop at the first `Accepted` |

The k samples get temperatures spread evenly from the model's temperature up
to `temperature + sampling.spread`, at most 1.0.

To use another rule, subclass
{py:class}`~perfarena.runs.strategies.ExperimentStrategy` and pass it to
`run`:

```python
from perfarena.runs import ExperimentStrategy

class TwoRoundsAlways(ExperimentStrategy):
    name = "two_rounds"

    @property
    def rounds(self) -> int:
        return 2

arena.run(spec, strategy=TwoRoundsAlways(spec))
```

## Resume a run

The ledger file (default `perfarena-ledger.json`) records the state of each
cell. Run the same spec with the same ledger to resume:

| Ledger status | Meaning | Next run |
|---|---|---|
| `complete` | a verdict landed, or the problem was already covered | skips the cell |
| `skipped` | not applicable (e.g. no starter code) | skips the cell |
| `failed` | generation or submit failed | retries it if `transient`, or with `--retry-failed` |
| `dispatch_failed` | the server could not dispatch | retries it |
| `poll_timeout` | no verdict in time | polls the same attempt again, without a new submit |

## Share generations with the scripts

The generation cache (`cache=` or `--cache DIR`) has the layout of
`matrix_sweep.py`'s `sweep_cache/`. The script and the SDK can use the same
directory. The run uses the cache only for single-sample, first-round cells,
and drops an entry after its verdict unless `keep_cache` is set.

## Follow a run

A run publishes events to an {py:class}`~perfarena.runs.events.EventBus`.
Subscribe a reporter or any callable:

```python
from perfarena.runs import ConsoleReporter, EventBus, JsonlReporter

bus = EventBus(ConsoleReporter(), JsonlReporter("events.jsonl"))
bus.subscribe(lambda ev: print(ev.kind, ev.data))
arena.run(spec, events=bus)
```

The event kinds, in the order a cell gives them: `run_started`,
`cell_started`, `cell_skipped`, `generated`, `generation_failed`,
`submitted`, `verdict`, `dispatch_failed`, `poll_timeout`, `cell_done`,
`run_done`. On the CLI, `--events FILE` writes them as JSONL.

To stop a run from another thread, pass a `threading.Event` as `cancel` and
set it. The run then starts no new cell. A cell in progress stops before its
next sample, and the ledger records it as a transient failure, so the next
run retries it.

## Check the replica policy

`spec.replicas` names the replica policy that each oracle must have. The run
only checks it. It refuses to start when an oracle's live policy differs,
and it never changes the policy:

```python
arena.policy_mismatches(spec)        # [] when the oracles agree with the spec
arena.replica_policy("arena-linux-local")
```
