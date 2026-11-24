# Analysis scripts of the PerfArena paper

This folder holds copies of the scripts that make the tables, figures, and numbers of the PerfArena paper.
The scripts read the arena data from a local cache.
They write their results to `analysis/out/`.
Git ignores `analysis/out/`.

## Requirements

- Python 3 with `numpy`, `scipy`, and `matplotlib`.
- `snapshot_to_cache.py` needs the backend environment (`backend/.venv`), because it loads the backend app.

## Network

By default, no script contacts a remote host.
The default arena URL is `http://offline.invalid`, which does not exist.
To read from an arena, set `ARENA_BASE=<url>` or give `--base-url <url>`.
A script then sends one request for each submission that is not in the cache.
Do not do this against a production arena: hundreds of requests can stop it.
If a file is missing from the cache and no arena URL is set, the script stops with a "nodename nor servname" (name resolution) error.

## Inputs

### The data cache (`PERFARENA_CACHE`)

Set `PERFARENA_CACHE=<folder>` to the cache folder.
The folder holds `submissions.json` and one `attempts_<id>.json` file for each submission.
`snapshot_to_cache.py` makes this folder from a copy of the arena database.
The paper's data cache is not public yet.

Make a cache from a database copy (run from the repo root):

```sh
backend/.venv/bin/python analysis/snapshot_to_cache.py --db <copy.db> --cache-dir <cache>
```

The database copy must have the current backend schema.
The script does not run the backend's start-up steps, so it does not change the copy.
If you only have an admin export (JSON), give `--export <export.json>` and a new `--db` path.
The script then builds the database from the export with the current schema.

### Input files in `analysis/data/`

`gen_paper_data.py` also reads these files from `analysis/data/`, if they exist.
If a file does not exist, the script continues without it.

| File | What it does |
|---|---|
| `<comp>_invalid.json` | Accepted solutions that leave all performance rankings. They still count for pass@1. |
| `<comp>_baseline_exclude.json` | Accepted solutions that never set a reference value. |
| `<comp>_sweep_time_from.json` | Gives the sweep time of a re-sent submission from the submission that it copies. |
| `<comp>_memory_remeasured.jsonl` | Re-measured peak memory. If it exists, it replaces all memory values. |

`<comp>` is `leetcode-99-perf` or `clbg-10`.
The paper uses `clbg-10_invalid.json` and `leetcode-99-perf_sweep_time_from.json`.
These files come with the paper's data, which is not public yet.

The frozen reference set for `--baseline <version>` is an output: `--freeze-baseline <version>` writes `<comp>_baseline_<version>.json` to `analysis/out/figs/data/`, and later runs read it from there.
The paper uses version `v10` for both competitions.

`workload_check.py` reads one more file: the JSON lines (`workload_sample.jsonl`) that a sampling script on the measurement host wrote for the paper. That script is not in this repository yet, so skip `workload_check.py` for your own data.

## Order of the steps

Run all commands from the repo root.
Set the environment first:

```sh
export PERFARENA_CACHE=<cache>
export ARENA_BASE=http://offline.invalid
```

1. Make the cache (see above).
2. Make the per-competition data and tables. Run CLBG second: `cost.tex` and `rq1_efficiency.tex` need the files of both competitions.
   `--baseline <tag>` reads a frozen reference set (see "Input files" above). For your own data, freeze one first with `--freeze-baseline <tag>`, then use it:
   ```sh
   python3 analysis/gen_paper_data.py --competition leetcode-99-perf --freeze-baseline v1
   python3 analysis/gen_paper_data.py --competition clbg-10 --freeze-baseline v1
   python3 analysis/gen_paper_data.py --competition leetcode-99-perf --baseline v1
   python3 analysis/gen_paper_data.py --competition clbg-10 --baseline v1
   ```
   The paper uses its frozen set `v10` (`--baseline v10`), which comes with its data.
3. Make the cell files that `bt_analysis.py` needs (`<comp>_cells.json`). Use `--refresh`, because the cell files do not exist yet.
   ```sh
   python3 analysis/gen_stat_tests.py --refresh
   ```
4. Make the Bradley-Terry results. This step takes about 4 minutes.
   ```sh
   python3 analysis/bt_analysis.py
   ```
5. Make the Bradley-Terry tables.
   ```sh
   python3 analysis/make_bt_tables.py
   ```
6. Make the figures and the other results.
   ```sh
   python3 analysis/make_rank_bump.py --competition leetcode-99-perf --bt
   python3 analysis/make_rank_bump.py --competition clbg-10 --bt
   python3 analysis/make_pareto_levels.py --bt
   python3 analysis/workload_check.py <workload_sample.jsonl>
   python3 analysis/judge_variation.py
   python3 analysis/correctness_consistency.py
   python3 analysis/lang_cost.py
   python3 analysis/lang_popularity.py
   ```

## What each script makes

All output paths are relative to `analysis/out/`.
They are the same paths as in the paper's repository.

| Script | Output | Use in the paper |
|---|---|---|
| `snapshot_to_cache.py` | The data cache (`--cache-dir`) | Input of all other scripts |
| `gen_paper_data.py` | `tables/cost.tex` | Table `tab:cost` (RQ3) |
| | `tables/baseline_contrib_<comp>.tex` | Table `tab:baseline-contrib` (appendix) |
| | `tables/difficulty_breakdown_leetcode.tex` | Table `tab:difficulty-breakdown` (appendix) |
| | `figs/data/<comp>_ratios.json`, `_stats.json`, `_rq2.json`, `_cost.json` | Inputs of the later scripts |
| | `tables/rq1_efficiency.tex`, `rq2_tradeoffs_<comp>.tex`, `appendix_rankings_<comp>.tex`, `shared_cells_leetcode.tex` | Not used in the paper now |
| `gen_stat_tests.py` | `figs/data/<comp>_cells.json` | Input of `bt_analysis.py` and `lang_popularity.py` |
| | `figs/data/stat_tests.json`, `tables/stat_tests.tex` | Not used in the paper now |
| `bt_analysis.py` | `figs/data/bt_results.json` | Rankings, tests, and numbers of RQ1 to RQ3 |
| `make_bt_tables.py` | `tables/rq1_overall.tex` | Table `tab:rq1-overall` (RQ1) |
| | `tables/rq1_languages.tex`, `_energy.tex`, `_memory.tex` | Tables `tab:rq1-languages`, `tab:rq1-languages-energy`, `tab:rq1-languages-memory` (appendix) |
| | `tables/bt_rankings.tex` | Tables `tab:bt-<language>` (appendix) |
| | `tables/bt_stats.tex` | Table `tab:stat-tests` (appendix) |
| `make_rank_bump.py` | `figs/res/rank_bump_{latency,energy,memory}_<comp>.pdf` | Figure `fig:rank-bump` (RQ1) |
| `make_pareto_levels.py` | `figs/tikz/pareto_levels_<comp>.tex`, `figs/data/<comp>_pareto_levels.json` | Figure `fig:pareto-levels` (RQ2) |
| `make_rq1_charts.py` | `figs/res/models_by_language_<comp>.pdf`, `metric_ranking_<comp>.pdf` | Not used in the paper now |
| `workload_check.py` | `figs/data/leetcode_workload_check.json`, `tables/workload_check.tex` | Table `tab:workload-check` (appendix) |
| `judge_variation.py` | `figs/data/leetcode_judge_variation.json` | Variation of the LeetCode judge (section "Measuring and Ranking Efficiency") |
| `correctness_consistency.py` | Printed text | Table `tab:xlang-consistency` (appendix) and the correctness section |
| `lang_cost.py` | Printed text | Cost per language (appendix) |
| `lang_popularity.py` | Printed text | Language popularity (appendix) |
| `memory_jobs.py` | The `--out` file | Jobs for the platform's memory re-measure. It needs `--db`. |

## Notes

- `make_pareto_levels.py` imports `make_bt_tables.py`, which reads `bt_results.json` at once. Thus `make_pareto_levels.py --help` also needs that file.
- Without `--competition`, `make_rank_bump.py` reads `sections/appendix.tex` of the paper, and `make_rq1_charts.py` reads `figs/data/rq1_ratios.json`. These files are not in this repo. Always give `--competition`.
- If you do not set `PERFARENA_CACHE`, `judge_variation.py` uses a cache path on the author's computer, and the other scripts use `~/.cache/perfarena-paper`.
- Some tables in the paper have small manual edits after the scripts made them (for example, the caption or the table width). The numbers are the same.
