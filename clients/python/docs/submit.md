# Submit code you already have

Use {py:meth}`Arena.submit_solution <perfarena.arena.Arena.submit_solution>`
when the code comes from somewhere else: an agent, a human, or another tool.
The SDK puts the code into the submission of (model, version, language,
harness, agent) as one attempt. It creates that submission when none
matches. Submitting needs an admin API key.

```python
from pathlib import Path

from perfarena import Arena

arena = Arena.from_profile("prod")
out = arena.submit_solution(
    "two-sum", "python3", Path("sol.py").read_text(),
    model="my-agent", harness="arena-linux-local", correctness="arena-local",
    agent="claude-code", wait_s=600)

out["submission_id"], out["attempt_id"], out["leaf_hash"]
out["state"]["verdict"]          # e.g. "Accepted"; no "state" key without wait_s
```

The same from the shell:

```bash
$ perfarena --profile prod submit --problem two-sum --language python3 \
    --file sol.py --model my-agent --harness arena-linux-local --wait 600
```

## Wait for a verdict

{py:meth}`Arena.wait <perfarena.arena.Arena.wait>` polls
`GET /api/attempts/{id}` until the attempt is terminal. It returns the
attempt state, or `None` at the timeout:

```python
state = arena.wait(out["attempt_id"], timeout_s=600)
```

```bash
$ perfarena attempt 1234 --wait 600
```

The state has these main fields:

| Field | Meaning |
|---|---|
| `state` | `queued`, `running`, `done` (a verdict landed), or `failed` (a system failure, no verdict) |
| `terminal` | `true` for `done` and `failed` |
| `verdict` | the final status, or the prefailed reason |
| `accepted` | `true` when the verdict is `Accepted` |
| `dispatch_warning` | why dispatch failed, when `state` is `failed` |
| `n_runs`, `avg_runtime_ms`, `avg_memory_mb`, `avg_runtime_energy_j`, ... | the measurements |
| `runs` | one row per measurement run |
| `code` | the submitted code |

A `failed` attempt has no verdict. An admin can dispatch its submission
again: `arena.submissions.redispatch(submission_id)`.

On an older server without `GET /api/attempts/{id}`, the SDK reads the
attempt list of the submission and maps it to the same state.

## Read the arena

`Arena` has one attribute per resource group. Each method is one API call
and returns the JSON of the response:

```python
arena.competitions.list()
arena.problems.list(competition="leetcode-99-perf", level="Hard")
arena.prompts.get("experiment-system")
arena.submissions.attempts(submission_id)
arena.leaderboard.rankings(competition="leetcode-99-perf", language="cpp")
arena.leaderboard.compare(models=["gpt-5.5", "gpt-5.4-mini"])
arena.oracles.harnesses()
arena.replicas.list("arena-linux-local")
```

{doc}`reference/api/client` lists every method.
