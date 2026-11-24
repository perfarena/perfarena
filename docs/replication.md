# Replicate PerfArena and the paper's experiments

This guide sets up a PerfArena instance and runs the experiments of the paper. It has five steps:

1. Set up a measurement host: the toolchains, the energy counters, and the harness runner.
2. Start the backend: the API, the dispatch worker, the rankings, and the site.
3. Configure the arena: the problems, the harness and the correctness oracles, and the ranking settings.
4. Run the experiments: generate the solutions and submit them.
5. Make the paper's tables and figures.

[`deployment.md`](deployment.md) is the reference for every setting and for a hardened deployment. This guide gives the order of the steps and the values of the paper.

**Code version.** The release [`paper-v1`](https://github.com/perfarena/perfarena/releases/tag/paper-v1) holds the code of the paper, with its harnesses in [`sanadlab/Energy-Languages` `paper-v1`](https://github.com/sanadlab/Energy-Languages/releases/tag/paper-v1). That release has the older folder layout (`webapp/` in place of `backend/`, `runner/`, and `clients/scripts/`). This guide follows the current layout. The method is the same, but the current harnesses prepare the inputs of C++, C, Rust, Go, Swift, and Dart outside the timer.

## 0. What you need

- **A measurement host.** It must be a bare-metal Linux computer with an Intel processor that exposes RAPL (`/sys/class/powercap/intel-rapl:0`). Do not use a virtual machine: it has no RAPL, so CodeCarbon only estimates the energy.
- **A computer for the backend.** It can be the measurement host. It needs Docker, or Python 3.12 or later.
- **API keys** of the LLM providers.
- **A LeetCode account,** for the `leetcode` correctness oracle of `leetcode-99-perf`, for the starter code of the problems, and for `replace_excluded_problems` (step 3a).

Clone the repository with its two submodules (`frontend/` and `Energy-Languages/`):

```bash
git clone --recurse-submodules https://github.com/perfarena/perfarena.git
cd perfarena
```

The submodule URLs use SSH, so the clone needs an SSH key on GitHub. Without the `frontend/` submodule, the Docker image does not build. You can then run the backend from source (step 2b): the API works, but the site does not.

## 1. The measurement host

### 1a. The paper's host

| Part | Value |
|---|---|
| Processor | Intel Core i7-7600U (2 cores, 4 threads, 2.8 GHz base, 3.9 GHz Turbo Boost) |
| Memory | 16 GB |
| System | Ubuntu 22.04, Linux 6.8 |
| Frequency | the defaults: Turbo Boost on, `powersave` governor of `intel_pstate`, no fixed frequency, no core pinning |
| Power limits | the defaults of the host: PL1 15 W over 31 s, PL2 25 W |
| Energy | CodeCarbon 3.3.0, which reads the RAPL package counter and adds a constant 10 W RAM estimate. The paper subtracts that estimate. |

Keep the default frequency settings. `runner/setup-harness-host.sh --tune` sets the `performance` governor and turns off Turbo Boost, and the paper did not do that.

The runner records the version of each tool with every measured run (since 29 October 2026). The paper's LeetCode runs of C++, Java, JavaScript, and TypeScript used g++ 11.4.0, Node 20.20.2, `tsc` 5.0.4, and Java 21.0.12. The CLBG TypeScript cells use `typescript@5.9.3` through `npx`. The other runs of the paper are older, and their tool versions were not recorded.

### 1b. Toolchains and the measurement venv

```bash
bash runner/setup-harness-host.sh                    # --dry-run prints the steps and changes nothing
~/.venvs/perfrunner/bin/pip install codecarbon==3.3.0   # the paper's CodeCarbon
```

The script installs the toolchains of every language, makes the measurement venv `~/.venvs/perfrunner` (`--venv PATH` puts it elsewhere), and sets `kernel.perf_event_paranoid`. Its last lines tell how to put the toolchains on `PATH`. It installs the latest version of each tool; to get the paper's versions (step 1a), install those versions yourself. At the end, it writes the versions that it found to `runner/harness-host-versions.txt`. The copy of that file in the repository comes from a development computer.

### 1c. Read access to the energy counters

The kernel makes the RAPL counters readable only by root at every boot. Without read access, CodeCarbon estimates the energy (about 12 W) instead of measuring it. A boot service gives one group read access. Use the group of the user that runs the runner (step 1d): your own group (`id -gn`), or the group of a separate runner user.

1. Edit `runner/perfarena-rapl-grant.service`: set `Environment=PERFARENA_RAPL_GROUP=` to that group.
2. Install the service and start it:

   ```bash
   sudo install -m 755 runner/perfarena-rapl-grant.sh /usr/local/sbin/
   sudo install -m 644 runner/perfarena-rapl-grant.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now perfarena-rapl-grant.service
   ```

3. Check, as the runner's user, that the counter is readable. It must print a number:

   ```bash
   cat /sys/class/powercap/intel-rapl:0/energy_uj             # as yourself
   sudo -u perfrunner cat /sys/class/powercap/intel-rapl:0/energy_uj   # as a separate user
   ```

### 1d. Start the runner

The runner executes code that LLMs generate. Protect it with a token, and if you can, run it as a separate user that cannot read your secrets ([`deployment.md`](deployment.md), section 3).

```bash
(umask 077; openssl rand -hex 32 > ~/.perfarena_runner_token)
nohup setsid runner/start_runner.sh >> ~/runner-start.log 2>&1 < /dev/null &
```

`runner/start_runner.sh` starts the runner on port 8787 with the settings of the paper. It reads these settings from the environment:

| Setting | Default |
|---|---|
| `PERFARENA_PYTHON` | `~/.venvs/perfrunner/bin/python` (the venv of step 1b) |
| `PERFARENA_TOKEN_FILE` | `~/.perfarena_runner_token` |
| `PERFARENA_LOG_FILE` | `~/harness.log`. At startup, the log shows the version and the path of every tool. Errors from before the log starts go to `~/harness-console.log`. |
| `PERFARENA_HOST` | `0.0.0.0`. Use `127.0.0.1` when the backend runs on the same host. |
| `PERFARENA_MACHINE_NAME` | the short host name |

A separate runner user has no files in your home folder. Set the four paths to files that it can read, and give it write access to `Energy-Languages/` (the cells compile there). For example:

```bash
sudo -u perfrunner env PERFARENA_PYTHON=/srv/perfrunner/venv/bin/python \
  PERFARENA_TOKEN_FILE=/srv/perfrunner/token PERFARENA_LOG_FILE=/srv/perfrunner/harness.log \
  PERFARENA_HOST=127.0.0.1 nohup setsid runner/start_runner.sh >> /srv/perfrunner/start.log 2>&1 < /dev/null &
```

Check that the runner is up and accepts the token. The answer must be 400 (the token is good, the request is empty). 401 means a wrong token. No answer means that the runner did not start: read the start log and the console log.

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $(cat ~/.perfarena_runner_token)" -d '{}' http://127.0.0.1:8787/validate
```

The runner does not start again after a reboot. Start it again, and check the energy counters again.

**The measurement settings of the paper** are the defaults of the runner and of `Energy-Languages/perfarena.mk`. `start_runner.sh` keeps them:

| Setting | Value |
|---|---|
| LeetCode: time budget of one measured window (`PERFARENA_BUDGET_S`) | 1.5 s, the first 30% of which is warm-up |
| LeetCode: time budget of the memory pass (`PERFARENA_MEM_BUDGET_S`) | 0.5 s |
| LeetCode: memory readings (`PERFARENA_MEM_REPEATS`) | 3; the paper uses the first one |
| CLBG: warm-up runs, measured runs (`PERFARENA_CLBG_WARMUP_RUNS`, `PERFARENA_CLBG_MEASURE_RUNS`) | 1, 7 |
| Idle time before a CLBG measurement (`PERFARENA_IDLE_S`) | 5 s |
| Time limits (`PERFARENA_LC_MEASURE_TIMEOUT_S`, `PERFARENA_HANDLER_TIMEOUT_S`, `PERFARENA_REQUEST_TIMEOUT_S`) | 420 s, 1200 s, 1320 s |
| Host check before and after a measurement (`PERFARENA_ISOLATION_CHECK`) | on: the runner refuses to measure (HTTP 429) while other processes keep the processor busy |

## 2. The backend

The backend can run on the measurement host or on another computer. It must reach the runner on port 8787.

### 2a. Docker

Load the 99 problems **before** the server starts for the first time. At its first start, the app makes the `leetcode-99-perf` competition with the problems that the database holds at that time.

```bash
docker build -t perfarena .
docker volume create perfarena-data
docker run --rm -v perfarena-data:/data perfarena python -m app.seed      # the 99 problems
docker run -d --name perfarena-backend --network host \
  -v perfarena-data:/data \
  -e ARENA_DISPATCH_ENABLED=1 \
  -e ARENA_BOOTSTRAP_ADMIN_EMAIL=you@example.com \
  -e ARENA_BOOTSTRAP_ADMIN_API_KEY='<a long random key>' \
  -e ARENA_SESSION_SECRET='<another long random value>' \
  -e ARENA_RANKING_MIN_CLUSTERS=10 \
  --restart unless-stopped perfarena
```

The site and the API are then on port 8000. `ARENA_DISPATCH_ENABLED=1` is necessary: without it, the backend uses a fake judge and never calls the runner. [`deployment.md`](deployment.md), section 2, explains the network choice (`--network host`, Linux only) and the other settings.

### 2b. From source

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ARENA_DB=$HOME/perfarena-arena.db               # a new database file
.venv/bin/python -m app.seed                           # the 99 problems, before the first start
ARENA_DISPATCH_ENABLED=1 ARENA_BOOTSTRAP_ADMIN_EMAIL=you@example.com \
  ARENA_BOOTSTRAP_ADMIN_API_KEY='<a long random key>' ARENA_SESSION_SECRET='<another long random value>' \
  ARENA_RANKING_MIN_CLUSTERS=10 \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Always set `ARENA_DB`. Its default, `backend/arena.db`, is an old development database in the repository, and the server does not start on it. Keep `ARENA_DB` set for every command of step 3. Without the built frontend, only the API works. To see the site, build it (`(cd ../frontend && npm install && npm run build)`) and add `ARENA_SERVE_FRONTEND_DIR=../frontend/dist` to the server's settings.

### 2c. The admin key

The steps below send API requests as the admin. Put the admin's key in a variable:

```bash
export ARENA=http://127.0.0.1:8000
export ARENA_ADMIN_KEY='<the key of ARENA_BOOTSTRAP_ADMIN_API_KEY>'
```

Only an admin can submit solutions.

## 3. Configure the arena

### 3a. The LeetCode account and the other problems

`replace_excluded_problems` replaces six problems that the arena excludes. It needs the LeetCode account and the train file of LeetCodeDataset v0.1.0: `data/LeetCodeDataset-v0.1.0-train.jsonl.gz` in the [newfacade/LeetCodeDataset](https://github.com/newfacade/LeetCodeDataset) GitHub repository. Download that file and unpack it with `gunzip`. (The Hugging Face page of the dataset has a later version under another name.)

From source, run these commands in `backend/` with its venv and `ARENA_DB`:

```bash
python -m app.leetcode_accounts --add NAME --cookie '<the Cookie header of a signed-in LeetCode session>'
python -m app.seed_leetcode_hard_competition
python -m app.replace_excluded_problems --dataset /path/to/LeetCodeDataset-v0.1.0-train.jsonl
python -m app.fetch_code_snippets                  # the starter code of every problem that has none yet
python -m app.seed_clbg_competition --problems-yaml ../Energy-Languages/perfarena/configs/problems.yaml
```

In Docker, run the first four commands in one-off containers on the same volume, and mount what the image does not hold (the dataset file and `Energy-Languages`):

```bash
P="docker run --rm -v perfarena-data:/data"
$P perfarena python -m app.leetcode_accounts --add NAME --cookie '<the Cookie header>'
$P perfarena python -m app.seed_leetcode_hard_competition
$P -v /path/to/the/dataset/folder:/in:ro perfarena \
  python -m app.replace_excluded_problems --dataset /in/LeetCodeDataset-v0.1.0-train.jsonl
$P perfarena python -m app.fetch_code_snippets
$P -v "$PWD/Energy-Languages:/workspace:ro" perfarena python -m app.seed_clbg_competition
```

`seed_clbg_competition` may say that there is no harness `arena-local`. That is harmless: each submission names its own harness.

### 3b. The harness and the correctness oracles

The first start makes the `leetcode` harness and the `leetcode` correctness oracle. Register the measurement host as the harness `arena-linux-local` and as the correctness oracle `arena-local`. `timeout_s` must be larger than the runner's 1320 s limit, and `auth_token` must equal the runner's token.

```bash
RUNNER='{"endpoint": "http://<runner address>:8787", "auth_token": "<the runner token>", "timeout_s": 1380}'
H=$(curl -s -X POST "$ARENA/api/admin/harnesses" -H "Authorization: Bearer $ARENA_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"slug\": \"arena-linux-local\", \"name\": \"Linux measurement host\", \"kind\": \"arena-local\", \"config\": $RUNNER}" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
curl -s -X POST "$ARENA/api/admin/correctness-oracles" -H "Authorization: Bearer $ARENA_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"slug\": \"arena-local\", \"name\": \"Linux measurement host\", \"kind\": \"arena-local\", \"config\": $RUNNER}"
curl -s -X POST "$ARENA/api/admin/harnesses/$H/probe" -H "Authorization: Bearer $ARENA_ADMIN_KEY"   # the backend reaches the runner
```

With `--network host` and the runner on the same host, the address is `127.0.0.1`. The Harnesses page of the site shows each harness and its health.

### 3c. The competitions: harness and tie margins

A competition accepts a submission only from the harnesses that it lists as compatible. Add `arena-linux-local` to both competitions. Without it, every submission fails with "harness 'arena-linux-local' is not compatible with competition".

Two values tie when they differ by less than the margin of their competition and metric. The paper's margins are √2 times the noise that it measured. The memory margin is different for each language:

| Competition | Latency | Energy | Memory |
|---|---|---|---|
| `leetcode-99-perf` | 1.697056 % | 3.676955 % | per language, as below |
| `clbg-10` | 3.676955 % | 3.535534 % | per language, as below |

Memory margins: C++ 2.786 %, C# 9.829 %, Java 8.118 %, JavaScript 1.541 %, PHP 0.990 %, Python 2.362 %, TypeScript 0.735 %.

A competition update replaces the whole competition, so this script reads each competition, adds the harness and the margins, and sends it back:

```bash
python3 - <<'EOF'
import json, os, urllib.request
base, key = os.environ["ARENA"], os.environ["ARENA_ADMIN_KEY"]
MEM = {"cpp": 2.786, "csharp": 9.829, "java": 8.118, "javascript": 1.541,
       "php": 0.990, "python3": 2.362, "typescript": 0.735}
TIE = {"leetcode-99-perf": {"latency": 1.697056, "energy": 3.676955, "memory": MEM},
       "clbg-10": {"latency": 3.676955, "energy": 3.535534, "memory": MEM}}
def call(method, path, body=None):
    req = urllib.request.Request(base + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))
for c in call("GET", "/api/competitions"):
    if c["slug"] in TIE:
        body = {k: c.get(k) for k in ("slug", "name", "description", "kind", "status")}
        body["compatible_harness_slugs"] = sorted(set(c.get("compatible_harness_slugs") or []) | {"arena-linux-local"})
        body["ranking_tie_pct"] = TIE[c["slug"]]
        call("PUT", f"/api/admin/competitions/{c['id']}", body)
for c in call("GET", "/api/competitions"):
    print(c["slug"], c["compatible_harness_slugs"], c["ranking_tie_pct"])
EOF
```

The paper also needs at least 10 problems before it resamples problems (`ARENA_RANKING_MIN_CLUSTERS=10`, in step 2). The admin **Variables** page (`/admin/settings`) shows the ranking settings and can change them without a restart.

## 4. Run the experiments

### 4a. A smoke test without an LLM

Before you spend API credit, check the path from a submission to a measurement. Submit a known correct solution, the reference solution of `2-keys-keyboard` in `Energy-Languages`, with the `perfarena` CLI:

```bash
python3 -m venv ~/perfarena-client && ~/perfarena-client/bin/pip install -e clients/python
ARENA_API_KEY="$ARENA_ADMIN_KEY" ~/perfarena-client/bin/perfarena --base-url "$ARENA" submit \
  --competition leetcode-99-perf --problem 2-keys-keyboard --language python3 \
  --file Energy-Languages/Python/leetcode/2-keys-keyboard/solution.py \
  --model smoke-test --harness arena-linux-local --correctness-oracle arena-local --wait 900
```

Always give `--base-url`: without it, the CLI uses `http://localhost:8000` or a profile from `~/.config/perfarena/profiles.toml`. The answer must show the attempt as Accepted, with runs that have a latency (`runtime_ms`, per call), an energy (`runtime_energy_j`, per call), and a memory value. For each run, `runtime_energy_j / (runtime_ms / 1000)` is the power in watts. On the paper's host, the median over the runs is 21–26 W. About 12 W means that CodeCarbon estimates the energy because it cannot read RAPL (step 1c).

If the attempt is Accepted but has no runs, the runner did not measure, for example because the host was busy (the host check of step 1d). The attempt still counts for pass@1, but it leaves the rankings, and the Admin page lists it with its harness error. Keep the host idle while it measures.

### 4b. The paper's runs

The paper uses 13 LLMs, the 7 languages `cpp`, `csharp`, `java`, `javascript`, `php`, `python3`, and `typescript`, the prompt template `experiment-system-with-shape`, and one sample (one round) for each problem and language. It requests a temperature of 0 from every API that accepts one.

| Provider | Models (`--model`) |
|---|---|
| OpenAI | `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4-mini` |
| Anthropic | `claude-opus-5-5`, `claude-opus-4-8`, `claude-sonnet-5-5`, `claude-fable-5-1` |
| OpenRouter | `deepseek/deepseek-v4.1-flash`, `google/gemini-3.8-flash`, `z-ai/glm-5.3-flash`, `x-ai/grok-4.6` |

Run `clients/scripts/matrix_sweep.py` once for each model and competition. `leetcode-99-perf` uses the `leetcode` correctness oracle, and `clbg-10` uses `arena-local`:

```bash
for COMP_ORACLE in leetcode-99-perf:leetcode clbg-10:arena-local; do
  PYTHONUNBUFFERED=1 python -u clients/scripts/matrix_sweep.py \
    --provider openai --openai-base-url https://openrouter.ai/api/v1/ --openai-api-key "$OPENROUTER_KEY" \
    --model x-ai/grok-4.6 --temperature 0 --prompts experiment-system-with-shape \
    --languages cpp,csharp,java,javascript,php,python3,typescript \
    --competition "${COMP_ORACLE%%:*}" --harness arena-linux-local --correctness-oracle "${COMP_ORACLE##*:}" \
    --base-url "$ARENA" --api-key "$ARENA_ADMIN_KEY" --inference-timeout 300 --retry-failed
done
```

The sweep can stop and start again: it skips the cells that it has done. For the OpenAI models, use `--openai-base-url https://api.openai.com/v1` with an OpenAI key. `matrix_sweep.py` speaks the OpenAI API and Ollama only. For the Anthropic models, use the `perfarena` CLI, which has an Anthropic provider ([`clients/python/docs/providers.md`](../clients/python/docs/providers.md)) and runs the same sweep from a spec file ([`clients/python/README.md`](../clients/python/README.md)).

### 4c. The golden baseline

The golden baseline holds, for each problem, language, and metric, the best value that an LLM of the campaign produced. `frozen_on` is a cut-off: only the submissions created on or before that day enter. Use the last day of your campaign. (The paper used 26 October 2026 for `leetcode-99-perf` and 10 October 2026 for `clbg-10`.) By default, an LLM that has submitted only part of the cells does not enter; `models` names the LLMs explicitly.

```bash
curl -s -X POST "$ARENA/api/admin/golden-baseline" -H "Authorization: Bearer $ARENA_ADMIN_KEY" \
  -H 'Content-Type: application/json' -d '{"competition": "leetcode-99-perf", "frozen_on": "<the last day of your campaign>"}'
curl -s -X POST "$ARENA/api/admin/golden-baseline" -H "Authorization: Bearer $ARENA_ADMIN_KEY" \
  -H 'Content-Type: application/json' -d '{"competition": "clbg-10", "frozen_on": "<the last day of your campaign>"}'
curl -s -X POST "$ARENA/api/admin/rankings/refresh" -H "Authorization: Bearer $ARENA_ADMIN_KEY"
```

For `clbg-10`, the paper's golden baseline also leaves out the solutions that skip the work of a benchmark or do it in another language (threat C3). Give them in `exclude`, a list of `{"model": ..., "language": ..., "problem": ...}`.

## 5. The paper's tables and figures

The scripts in [`analysis/`](../analysis/README.md) make the tables and the figures of the paper. They read a copy of the arena's database, never the live API. To copy the database while the server runs, use SQLite's `.backup` ([`web-app.md`](web-app.md), "Make and publish a snapshot", step 1). Then follow [`analysis/README.md`](../analysis/README.md). The paper's own data are not public yet.
