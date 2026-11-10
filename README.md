# PerfArena

PerfArena is an open arena that measures the performance of the code that large language models (LLMs) generate. An LLM gets a programming problem and generates a solution in one programming language. PerfArena checks that the solution is correct. Then it measures the latency, the energy, and the peak memory of the solution on a dedicated measurement host. PerfArena ranks the LLMs on each metric, in each language and in each competition. It keeps the prompt, the response, and the reasoning trace of each attempt in an append-only hash chain, so that anyone can check what the LLM generated.

Built at **NYU Abu Dhabi** by Rui Rua, Hailemarian Mersha, Karim Ali (SanadLab), and João Paulo Fernandes.

## Concepts

- **Competition:** a fixed set of problems.
- **Submission:** the solutions of one LLM, with one prompt, for every problem of one competition, in one language.
- **Correctness oracle:** the part that checks a solution. The `leetcode` oracle sends the solution to the LeetCode judge. The `arena-local` oracle runs the solution on a runner host and compares its output with the test cases or the reference output of the problem.
- **Harness:** the part that measures a solution. The `arena-linux-local` harness measures on a Linux measurement host. The `leetcode` harness uses the runtime and the memory that the LeetCode judge reports.
- **Golden baseline:** a reference submission that is not ranked. For each problem, language, and metric, it holds the best correct value that an LLM of the campaign produced, frozen on one date. The paper gives each value of an LLM as a ratio to the golden baseline.

| Competition | Problems | In the paper |
|---|---|---|
| `leetcode-99-perf` | 99 LeetCode problems, 33 for each difficulty level | yes |
| `clbg-10` | 10 problems of the Computer Language Benchmarks Game (CLBG) | yes |
| `leetcode-99-hard-perf` | 99 Hard LeetCode problems with the longest median runtime | no |
| `leetcode-99-rewritten` | the problems of `leetcode-99-perf` with new statements and function names, to find contamination | no |

The paper uses seven languages: C++, C#, Java, JavaScript, PHP, Python, and TypeScript. The harnesses support more languages.

## Repository structure

```
.
├── webapp/
│   ├── backend/                  FastAPI + SQLModel + SQLite: the API, the dispatch worker, and the rankings
│   │   ├── app/                  the app, and the scripts that seed competitions and select problems
│   │   └── tests/                pytest tests (offline, with a fake judge)
│   ├── frontend/                 Vite + React + TypeScript site
│   ├── examples/                 the harness runner and the scripts that generate and submit solutions
│   └── publish_pages.sh          builds the read-only snapshot of the site for GitHub Pages
├── clients/python/               the perfarena package: SDK, CLI, and MCP server
├── Energy-Languages/             (submodule) the measurement harnesses, the LeetCode test cases, and the CLBG problems
├── data/                         the problem data and the outputs of the problem selection
├── docker/harness-test/          a Docker copy of the measurement host, for local tests
├── scripts/bicho_checks/         the checks on the measurement host for the threats to validity of the paper
├── docs/                         architecture, deployment, problem selection, and LeetCode percentiles
├── human_solutions/              human-written LeetCode solutions for each problem and language (the arena does not use them)
├── leetbot.py                    sends a solution to LeetCode (the leetcode oracle and harness use it)
├── prepare_dataset.py            makes data/problems.parquet and data/solutions.parquet from the source dataset
├── train-00000-of-00001.parquet  the source dataset of LeetCode problems and solutions
├── export_human_solutions.py     writes human_solutions/ from data/solutions.parquet
├── setup-harness-host.sh         installs the toolchains on a measurement host
├── harness-host-versions.txt     the toolchain versions that setup-harness-host.sh found
├── Dockerfile                    one container with the backend and the built frontend
├── docker-compose.local.yml      the backend and a harness runner on one computer
├── LICENSE                       the license of the code
└── DATA_LICENSE.md               the license of the data
```

The main files in `data/`:

- `problems.parquet` and `solutions.parquet`: the problems and the solutions of the source dataset.
- `sampled_problems.json`: the 99 problems that `python -m app.seed` loads.
- `runtime_ranking.json`, `anchors.json`, `discrimination.json`, and `premium_status.json`: the outputs of the problem selection ([`docs/problem-selection.md`](docs/problem-selection.md)).
- `leetcode-99-rewritten-statements.csv` and `leetcode-99-hard-rewritten-statements.csv`: the new statements for the rewritten competitions.
- `test_submissions/`: solutions for the smoke tests.

## Populate the arena

[`docs/deployment.md`](docs/deployment.md) tells how to install the arena. Run the commands below in `webapp/backend`, with its virtual environment.

**1. Load the problems.** The app makes the `leetcode-99-perf` competition at its first start. Then:

```bash
python -m app.seed                                   # the 99 problems of data/sampled_problems.json
python -m app.fetch_code_snippets --sampled-only --force
python -m app.seed_leetcode_hard_competition
python -m app.replace_excluded_problems --dataset LeetCodeDataset-v0.1.0-train.jsonl
python -m app.seed_clbg_competition --problems-yaml ../../Energy-Languages/perfarena/configs/problems.yaml
```

`replace_excluded_problems` replaces six problems in `leetcode-99-perf` and `leetcode-99-hard-perf` with problems of the same difficulty, because the arena excludes them (contamination or missing test cases). It needs both competitions and the train file of [LeetCodeDataset](https://huggingface.co/datasets/newfacade/LeetCodeDataset). `seed_rewritten_competition` makes `leetcode-99-rewritten` from `data/leetcode-99-rewritten-statements.csv` (its docstring gives the command). To select the 99 LeetCode problems again from the source dataset, run `prepare_dataset.py` and then the steps in [`docs/problem-selection.md`](docs/problem-selection.md).

**2. Add a harness and a correctness oracle.** Start a harness runner (`webapp/examples/harness_runner_server.py` with the handler `perfarena_handler.py`) on a measurement host. Then register it on the Harnesses page of the site. [`docs/deployment.md`](docs/deployment.md), section 3, tells how to set up the host. For a local test, `docker compose -f docker-compose.local.yml up -d` and `docker/harness-test/register-harness.sh` start and register a runner in Docker.

**3. Submit solutions.** `webapp/examples/matrix_sweep.py` asks an LLM for a solution to each problem and submits the solutions for a competition, a harness, and a correctness oracle. The `perfarena` CLI in `clients/python` does the same from a spec file. `webapp/examples/submit_with_local_model.py` submits one solution.

To see the site with data but without an LLM or a measurement host, run `python -m app.seed_fake_submissions`. It adds fake LLMs and judged submissions.

## Replicate the paper

The paper uses the competitions `leetcode-99-perf` and `clbg-10`, 13 LLMs, and 7 languages.

1. Set up the arena and one Linux measurement host. The paper measures with the `arena-linux-local` harness.
2. Load the problems (step 1 above).
3. Generate one submission for each LLM, language, and competition with `matrix_sweep.py` (step 3 above, and section 4 of [`docs/deployment.md`](docs/deployment.md)). Use the `leetcode` correctness oracle for `leetcode-99-perf` and the `arena-local` oracle for `clbg-10`.
4. Make the tables and the figures with the scripts in `figs/scripts/` of the paper's repository:
   - `snapshot_to_cache.py` reads a copy of the arena database and writes the submissions and their attempts to a cache folder.
   - `gen_paper_data.py` computes the ratios to the golden baseline, pass@1, and the cost of each LLM.
   - `bt_analysis.py` computes the Bradley-Terry rankings, and `make_bt_tables.py` writes their tables.

`scripts/bicho_checks/` holds the checks for the threats to validity, and its README tells how to run them on the measurement host.

## More documentation

- [`docs/architecture.md`](docs/architecture.md): the parts of the arena and the sequence diagrams of the main workflows.
- [`docs/deployment.md`](docs/deployment.md): installation, deployment, configuration (environment variables), tests, and contributions.
- [`docs/deploy-replica-pools.md`](docs/deploy-replica-pools.md): more than one runner for a harness or an oracle.
- [`docs/problem-selection.md`](docs/problem-selection.md): how we select the 99 LeetCode problems.
- [`docs/leetcode-percentile-semantics.md`](docs/leetcode-percentile-semantics.md): the noise of the LeetCode percentiles.
- [`webapp/README.md`](webapp/README.md): the public and the gated pages, and the read-only snapshot on GitHub Pages.
- [`webapp/backend/README.md`](webapp/backend/README.md), [`webapp/frontend/README.md`](webapp/frontend/README.md), and [`webapp/examples/README.md`](webapp/examples/README.md): the backend, the frontend, and the scripts.
- [`clients/python/README.md`](clients/python/README.md): the SDK, the CLI, and the MCP server.
- [`docker/harness-test/README.md`](docker/harness-test/README.md): the Docker copy of the measurement host.

## License

The code is under Apache 2.0 (see [`LICENSE`](LICENSE)). The data that PerfArena produces (generated solutions, measurements, rankings, and prompt templates) is under CC BY 4.0. LeetCode problem statements, hints, and starter code remain the property of LeetCode. [`DATA_LICENSE.md`](DATA_LICENSE.md) gives the details and the third-party content. Cite the authors if you use PerfArena in published work:

> Rua, R., Mersha, H., Ali, K., and Fernandes, J. P. *PerfArena: A verifiable leaderboard for LLM-generated LeetCode solutions.* NYU Abu Dhabi / SanadLab, 2026.
