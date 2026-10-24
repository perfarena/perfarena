# perfarena

`perfarena` is the Python client of the PerfArena platform. PerfArena ranks
LLMs by the latency, memory, and energy of the correct code that they
generate. The package has three interfaces on one core:

- an **SDK**. A `RunSpec` describes a run, and `Arena.run(spec)` executes it.
- a **CLI**, `perfarena`. The flags of `perfarena run` come from the
  `RunSpec` fields.
- an **MCP server**, `perfarena mcp`. An agent uses it to read the arena,
  submit solutions, and start runs.

```python
from perfarena import Arena, RunSpec

spec = (RunSpec.builder()
        .model("gpt-5.5", provider="openai", temperature=0.0)
        .prompt("experiment-system")
        .competition("leetcode-99-perf").languages("python3", "cpp")
        .oracles("arena-linux-local", "arena-local")
        .build())

result = Arena.from_profile("prod").run(spec, ledger="ledger.json")
print(result.completed, result.failed, result.submissions)
```

The client scripts in `webapp/examples/` stay the reference implementation.
The parity tests run the scripts and the SDK on the same inputs and require
identical prompts, attempts, and submissions ({doc}`design`).

Start with {doc}`install`, then {doc}`runs`. To give an agent access to
the arena, read {doc}`mcp`. {doc}`reference/spec` lists every field of a
spec.

```{toctree}
:caption: Guide
:hidden:

install
concepts
runs
submit
mcp
providers
```

```{toctree}
:caption: Reference
:hidden:

reference/spec
reference/cli
reference/api/index
```

```{toctree}
:caption: About
:hidden:

design
```
