# Concepts

The SDK uses the names of the PerfArena API. This page defines each one.

## On the server

Competition
: A named set of problems, e.g. `leetcode-99-perf` or the CLBG competition.
  Each competition lists the performance oracles that it accepts.

Problem
: One coding task of a competition, with a statement, hints, and starter
  code per language. Its `slug` identifies it (`two-sum`).

Prompt
: A prompt template in the server's registry, e.g. `experiment-system`. The
  SDK fills its placeholders (`{{code_template}}`, `{{hints_block}}`, ...)
  exactly as the scripts do. Without a prompt name, the SDK uses its built-in
  inline prompt.

Performance oracle (harness)
: The service that measures latency, memory, and energy, e.g.
  `arena-linux-local` (a PerfArena runner) or `leetcode` (the LeetCode
  judge). The API and the `--harness` flag call it a harness.

Correctness oracle
: The service that gives the verdict, e.g. `arena-local`. Without one, the
  verdict of the performance oracle decides.

Replica and replica policy
: An oracle can have several replicas (runner hosts). Its replica policy
  (e.g. `primary`, `least_busy`, `sticky`, `split_repeats`,
  `broadcast_merge`) says how the server spreads work over them. An admin
  sets the policy on the oracle. A spec can only check it.

Submission
: The solutions that one source gives for one competition in one language:
  one model and version, one prompt, one set of oracles. A submission holds
  one attempt per problem and sample.

Attempt
: One solution to one problem inside a submission. The server dispatches it
  to the oracles and records the verdict and the measurements.

Verdict
: The result of an attempt: `Accepted`, `Wrong Answer`, `Compile Error`,
  and so on. A *prefailed* attempt never reaches an oracle. Its verdict is
  the reason it failed: `generation_timeout`, `generation_error`, or
  `wrong_language_extracted`. pass@1 counts prefailed attempts as failures.

Measurement run
: The server stores one row per measurement of an attempt (`n_runs` on an
  attempt). This is a different thing from an SDK run.

## In the SDK

Run
: One execution of a {py:class}`~perfarena.spec.RunSpec`. `Arena.run(spec)`
  returns a {py:class}`~perfarena.runs.executor.RunResult` with a `run_id`.

Cell
: One unit of work of a run: (model, prompt, language, problem). The
  ledger and the generation cache key a cell as
  `model|prompt|language|problem`.

Sample (chain)
: One independent generation chain of a cell. A spec with `sampling.k > 1`
  gives each cell k samples, with temperatures spread over a range.

Round
: One step of a sample. Round 0 generates from the problem. Each later
  round sees the code and the verdict of the round before it.

Strategy
: The rule for samples and rounds: `SingleShot`, `KSamples`,
  `IterativeRefine`, or `RetryUntilAccepted`. The spec chooses it
  ({doc}`runs`).

Ledger
: A JSON file with the state of each cell. It makes a run resumable.

Generation cache
: A directory of generated code per cell. A resumed run does not pay for a
  model call twice. Its layout is the one of `matrix_sweep.py`'s
  `sweep_cache/`.

Trace
: The record of a generation that goes with each attempt: the provider, the
  model, the parameters, the raw answer, the reasoning, the token use, and
  the cost. Its hash (`leaf_hash`) can be signed with an Ed25519 key.

## How cells become submissions

A run puts all cells with the same model, version, prompt, and language into
one submission. Before it creates a submission, it looks for a matching one
with `PUT /api/submissions/upsert`, among the submissions of the API key's
owner. A submission matches when these are equal: model name, model version,
language, prompt, hints, optimization target, number of rounds, and agent.
The competition, the harness, and the correctness oracle must be equal, or
empty on an older submission. Model parameters are not compared. By default, a submission whose
attempts are all `Accepted` does not match (`append_fully_accepted` changes
this). A problem that the matching submission already covers is skipped.
