# Re-measure LeetCode JavaScript, TypeScript, C++, and Java

This folder re-measures the latency, energy, and memory of every accepted
campaign solution that the paper ranks in these four languages. It then
replaces the old values in the production database.

**Why these four:**
- **JavaScript:** its harness loaded the solution with `eval`, which made the calls several times slower.
- **TypeScript:** 10 LLMs ran on Node 12 and 3 on Node 20. Its harness also transpiled every test case inside the measured process.
- **C++ and Java:** before 2026-10-11, every test case compiled the solution inside the measured process, so the energy reading included the compiler.

The runner now builds each solution first and measures only the prebuilt
program, with the new harnesses.

On the machine that will run it, pull this repository and update the
submodule, so the harness files match `manifest.json`:

```bash
git pull && git submodule update --init Energy-Languages
cd scripts/remeasure_leetcode
```

## What you need on that machine

- Python 3.9 or later (standard library only), and `ssh`/`scp` to `nyuad@10.228.128.30`.
- Network access to the bicho runner, `http://10.228.128.30:8787` (NYU network).
- Network access to `https://perfarena.ngrok.app`.
- `PERFARENA_RUNNER_TOKEN`: the runner's auth token. It is the `auth_token` in production's harness config, and the same token as in the running runner's environment.
- `ARENA_API_KEY`: the production admin key (`~/.perfarena_admin_key`).

## Contents

| File | What it is |
|---|---|
| `jobs.json` | 4,987 jobs in measuring order: JavaScript 1,252, TypeScript 1,254, C++ 1,233, Java 1,248. They come from the paper cache `snapshot-20261027-js4`, made by `make_jobs.py`. 379 JavaScript jobs have no code yet (submissions 504–507). |
| `manifest.json` | The 13 files to deploy on bicho (from this repository), with their hashes and the hash that each file has on bicho now. |
| `fill_code.py` | Fetches the missing code from production: 4 requests, one after the other. |
| `deploy_bicho.sh`, `restart_runner.py` | Back up, copy, and restart the runner with its own environment. |
| `remeasure.py` | Runs the jobs on the runner. It is resumable. |
| `push.py` | Writes the new values to production. |

## Steps

```bash
# 1. complete the jobs (4 sequential requests to production)
python3 fill_code.py --jobs jobs.json --out jobs_full.json --backend https://perfarena.ngrok.app

# 2. deploy the harnesses to bicho (asks for the SSH password once;
#    or set SSHPASS_FILE=<file with the password> to use sshpass)
./deploy_bicho.sh --check      # read-only: every file must say "will be replaced"
./deploy_bicho.sh              # backup ~/harness_backup_<time>.tgz, copy, restart the runner
#   (it refuses to restart while the runner is measuring; just run it again later)
#   undo: ./deploy_bicho.sh --rollback

# 3. confirm that the runner runs the new harnesses (about 2 minutes)
export PERFARENA_RUNNER_TOKEN=...
python3 remeasure.py --jobs jobs_full.json --out results.jsonl --check
#   must end with "deploy looks right", and show node v20 and tsc 5.0.4

# 4. the re-measure: 4,987 jobs at about 70 s each, about 4 days
#    (run it in tmux or with nohup; stop and restart at any time, it resumes)
nohup python3 remeasure.py --jobs jobs_full.json --out results.jsonl > remeasure.log 2>&1 &
tail -f remeasure.log

# 5. write the new values to production: a dry run first, then the real push
export ARENA_API_KEY=$(cat ~/.perfarena_admin_key)
python3 push.py --results results.jsonl --backend https://perfarena.ngrok.app --dry-run
python3 push.py --results results.jsonl --backend https://perfarena.ngrok.app
```

Then send me `results.jsonl`, `remeasure.log`, and `push_skipped.json`. I will
refresh the paper cache and regenerate the tables and figures.

## Good to know

- **The measuring order:**
  - The jobs go problem by problem, with the 4 languages side by side.
  - The 13 LLMs of a problem run in a random order.
  - So the solutions that duel are measured minutes apart, and slow drift of the host affects all LLMs alike.
- **What each job measures:** latency and energy over 7 runs, then memory over 3 readings, as in the campaign and the memory re-measure.
  - Each record keeps the runner's toolchain record: the harness file hashes and the compiler, runtime, and CodeCarbon versions.
- **Production keeps using bicho:** new production submissions still go to bicho. When the runner is busy, it answers 429 and the script waits.
  - Please run no other experiments on bicho during the re-measure.
- **Failed re-measures:** a job whose runs are not all Accepted keeps its old values in production. `push.py` lists it in `push_skipped.json`.
- **CLBG is skipped:** it does not use these harnesses, and in these four languages its measured runs start a prebuilt program.
- **Not covered:** the solutions that the LeetCode judge accepted but our harness failed on (for example, 14 C++ ones before 2026-09-13 and 2 TypeScript ones on Node 12). They count as failures, so they are not jobs. Fixing their status is a separate step.
- **Rollback in production:** `push.py` keeps every old value hidden in the run (`timing_previous`, `memory_previous_mb`), so the change can be undone.
