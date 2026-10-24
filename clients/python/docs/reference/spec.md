# Spec fields

Every field of a {py:class}`~perfarena.spec.RunSpec`. This page comes from
the `RunSpec` schema when these docs are built. A field path with a dot is a
nested field: in YAML, `rounds.n` is `rounds: {n: 3}`.

`model.*` describes one model. In YAML, give one model as `model:` or
several as a list under `models:`. On the CLI, the `--model-*` flags set the
first model.

```{include} ../_generated/spec-fields.md
```

## A full example

```yaml
models:
  - {name: gpt-5.5, provider: openai, params: {temperature: 0.0}}
  - {name: qwen3:8b, provider: ollama, think: false}
prompts: [experiment-system, experiment-system-with-shape]
competition: leetcode-99-perf
languages: [python3, cpp, java]
problems: [two-sum, lru-cache]
oracles: {performance: arena-linux-local, correctness: arena-local}
replicas: {performance: sticky}
sampling: {k: 3, spread: 0.5}
rounds: {n: 2, stop_on_accept: true}
target: latency
compile_flags: {cpp: "-O2"}
concurrency: 4
```
