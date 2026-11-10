# Installation, deployment, and development

This file tells how to install PerfArena, deploy it, configure it, and test it. [`../README.md`](../README.md) gives the overview of the project.

## Installation

### Quick start with Docker (recommended)

The single-container image bakes the frontend, backend, reference data, and migrations. SQLite lives on a volume so the DB survives upgrades.

```bash
git clone https://github.com/sanadlab/perfarena.git
cd perfarena
docker build -t perfarena .

docker run -p 8000:8000 \
  -v perfarena-data:/data \
  -e ARENA_BOOTSTRAP_ADMIN_EMAIL=you@example.com \
  -e ARENA_BOOTSTRAP_ADMIN_PASSWORD='set-a-real-one' \
  perfarena
```

Open <http://localhost:8000>. The bootstrap creates an approved admin user on first boot; the auto-generated API key is printed to stderr (visible via `docker logs <container>`). Subsequent boots with the same email are no-ops.

To **enable real LeetCode dispatch** (off by default — the container ships in fake-judge mode so it works offline), also pass:

```bash
  -e ARENA_DISPATCH_ENABLED=1 \
  -e LEETCODE_COOKIE_HEADER='cookie1=…; cookie2=…' \
```

See `.env.example` for the cookie shape.

### Local development

For iterating on backend or frontend code without Docker:

```bash
git clone https://github.com/sanadlab/perfarena.git
cd perfarena

# ---- backend ----
cd webapp/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.seed                  # imports problems.parquet, seeds default prompts
uvicorn app.main:app --reload       # http://localhost:8000  (set ARENA_ENABLE_DOCS=1 for /docs)

# (optional) seed fake competitors so the leaderboard isn't empty
python -m app.seed_fake_submissions

# ---- frontend ----
cd ../frontend
npm install
npm run dev                         # http://localhost:5173  (proxies /api → :8000)
```

Sign in with the admin API key (paste into Register → "already have a key?" or the `/me/api-key` page).

## Deployment (hardened)

A recent security pass changed how components authenticate and how the runner is isolated. Read this before deploying anywhere reachable.

**What changed:** API keys are stored **hashed** at rest (shown once at creation; the plaintext still works as `Authorization: Bearer`). The SPA logs in for a signed **session token**, which requires `ARENA_SESSION_SECRET`. Interactive docs are **off** by default (`ARENA_ENABLE_DOCS=1` to re-enable). The harness runner is **not exposed to the host**, accepts an optional **`--auth-token`**, mounts **no repo secrets**, and has **no network egress**.

### 1. Local stack (docker compose)

```bash
# .env at the repo root (gitignored):
#   ARENA_BOOTSTRAP_ADMIN_EMAIL=admin@perfarena.dev
#   ARENA_BOOTSTRAP_ADMIN_API_KEY=<strong random>
#   ARENA_SESSION_SECRET=<random>          # required for stable SPA logins
#   PERFARENA_RUNNER_TOKEN=<random>        # optional: enforce runner auth
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml up -d
./docker/harness-test/register-harness.sh
```

The runner sits on an internal, egress-less network (reachable only by the backend at `http://harness:8787`) and mounts only `Energy-Languages` + `webapp/examples`, so executed code sees neither `.env` nor `data/`.

### 2. Backend container (standalone)

**Networking (this is the one that bites): the backend container must be able to reach the harness runner.** When the runner is bare-metal on the SAME host (the usual prod layout, §3), a bridge-networked container (`-p 127.0.0.1:8000:8000`) generally CANNOT reach a service on its own host — with a default `INPUT DROP` + ufw not covering `docker0`, container→host traffic on the runner port is dropped, so dispatch silently fails and the runner log stays empty. Use **`--network host`** so the container shares the host stack and reaches the runner on `127.0.0.1:8787`:

```bash
docker build -t perfarena-backend:latest .
docker run -d --name perfarena-backend \
  --network host \
  -v perfarena-data:/data \
  -e ARENA_DB=/data/arena.db \
  -e ARENA_DISPATCH_ENABLED=1 \
  -e ARENA_BOOTSTRAP_ADMIN_EMAIL=admin@perfarena.dev \
  -e ARENA_BOOTSTRAP_ADMIN_API_KEY='<strong random>' \
  -e ARENA_SESSION_SECRET='<random, stable>' \
  --restart unless-stopped \
  perfarena-backend:latest
```

**`ARENA_DISPATCH_ENABLED=1` is REQUIRED for real dispatch.** Without it the backend runs a built-in fake judge: it synthesizes plausible runs (real-looking `avg_rt`, Accepted/WA/TLE with no detail) and never contacts the runner or LeetCode. If your sweep produces verdicts but the runner log stays empty, this flag is missing.

With `--network host` the app listens on `0.0.0.0:8000` directly on the host (drop `-p`; it's ignored). Keep it off the LAN with `sudo ufw deny 8000` — loopback (your reverse proxy / ngrok tunnel) still reaches it. Register the harness/oracle endpoint as `http://127.0.0.1:8787`. Verify the link: `docker exec perfarena-backend curl -sS -m5 http://127.0.0.1:8787/healthz`.

Bridge-networking alternative (only if you can't use host networking): keep `-p 127.0.0.1:8000:8000`, then open the bridge→host path with `sudo ufw allow in on docker0 to any port 8787 proto tcp` and register the endpoint as `http://172.17.0.1:8787` (the docker0 gateway).

Leave `ARENA_ENABLE_DOCS` unset so `/docs` and `/openapi.json` return 404.

### 3. Harness runner on a bare-metal measurement host

Provision toolchains + venv once, then run the runner as a dedicated non-root user with no access to secrets and no outbound network.

```bash
# one-time: toolchains + codecarbon venv
cd ~/bench/perfarena-leetcode && bash setup-harness-host.sh --skip-energy

# one-time: non-root user + lock secrets away from it
sudo useradd -r -s /usr/sbin/nologin perfrunner
sudo chmod 600 .env data/leetcode_accounts.json      # readable by the backend user only

# one-time: deny egress for the runner uid, allow loopback, so executed code
# cannot exfiltrate but the backend can still reach it on 127.0.0.1. (systemd
# PrivateNetwork would also block the backend->runner call, so use a uid rule.)
sudo iptables -A OUTPUT -m owner --uid-owner perfrunner -o lo -j ACCEPT
sudo iptables -A OUTPUT -m owner --uid-owner perfrunner -j REJECT

# start the runner (localhost-only, token-protected, no npx download)
cd webapp/examples
sudo -u perfrunner env \
  PERFARENA_ROOT="$PWD/../../Energy-Languages" \
  PYTHONPATH="$PWD/../../Energy-Languages" \
  PERFARENA_MACHINE_NAME=bicho PERFARENA_ENERGY_TOOL=codecarbon \
  TSC=tsc PERFARENA_ISOLATION_CHECK=1 \
  PERFARENA_HANDLER_TIMEOUT_S=1200 PERFARENA_LC_MEASURE_TIMEOUT_S=420 \
  PERFARENA_REQUEST_TIMEOUT_S=1320 \
  ~/.venvs/perfrunner/bin/python harness_runner_server.py \
    --host 0.0.0.0 --port 8787 \
    --auth-token "$PERFARENA_RUNNER_TOKEN" \
    --measure-handler perfarena_handler:measure_once \
    --validate-handler perfarena_handler:validate_once \
    --request-timeout 1320 --verbose
```

**Bind address (this bit matters — a wrong choice looks like "dispatch failed / empty runner logs"):** bind an interface the backend can actually reach.
- Use `--host 127.0.0.1` ONLY when the backend runs on the SAME host and reaches the runner over loopback (bare-metal backend, or `docker run --network host`). Register the harness endpoint as `http://127.0.0.1:8787`.
- Use `--host 0.0.0.0` when the backend is a container with port-mapping OR on a different host. Register the endpoint as the runner's reachable address, e.g. `http://<host-lan-ip>:8787`. The `--auth-token` is what protects the (RCE-capable) runner in this case, so it is mandatory here.

If the runner binds `127.0.0.1` but the harness endpoint is a non-loopback address, every connection is refused, nothing reaches the runner, and the backend synthesises failures — with an empty runner log.

Measurement ceilings: a CLBG measure may run 20 min (`PERFARENA_HANDLER_TIMEOUT_S=1200`) and a LeetCode measure 7 min (`PERFARENA_LC_MEASURE_TIMEOUT_S=420`); either overrun is reported as Time Limit Exceeded, never averaged into "Accepted". `--request-timeout 1320` is the global backstop above both.

Register it in the backend (Harnesses page or `register-harness.sh`) with the endpoint matching the bind above. The harness `config.auth_token` MUST equal the runner's `--auth-token`, or dispatch gets 401. Set the harness `config.timeout_s` above the watchdog (e.g. `1380` for a `1320` watchdog); otherwise the backend gives up at its 300 s default and marks attempts "dispatch failed" while the runner is still measuring. The `perfrunner` user must read/write the `Energy-Languages` tree (for compiles) but not `.env` / `leetcode_accounts.json`. `TSC=tsc` uses the image/host-installed compiler so no cell downloads TypeScript at build time (needed under the egress ban).

Runtime knobs (repeats, TLE retries, correctness-gate retries, per-account gap) are also editable live from the admin **Variables** tab (`/admin/settings`) without an env change or restart — an override there wins over the env var. The same tab shows live health of every registered harness.

**More runners (replica pools).** Several runner endpoints can serve one harness or correctness oracle, with a spreading policy, machine classes, calibration, and an optional claim-based queue. Upgrading needs no configuration change. See `docs/deploy-replica-pools.md` for the upgrade steps, adding replicas, and roll-back.

### 4. Run a sweep

```bash
PYTHONUNBUFFERED=1 python -u webapp/examples/matrix_sweep.py \
  --provider openai --openai-base-url https://openrouter.ai/api/v1/ \
  --openai-api-key "$OPENROUTER_KEY" \
  --model z-ai/glm-5.3-flash --prompts experiment-system-with-shape \
  --language python3,java,cpp,typescript,csharp,php \
  --competition leetcode-99-perf --harness arena-linux-local --correctness-oracle leetcode \
  --base-url https://perfarena.ngrok.app --api-key "$ARENA_ADMIN_KEY" \
  --temperature 0 --inference-timeout 300 --retry-failed
```

The API key is hashed at rest, but the plaintext key still authenticates as a Bearer, so `--api-key` is unchanged. Use a generous `--inference-timeout` for reasoning models (glm reasons for minutes), and `PYTHONUNBUFFERED=1 python -u` so progress streams live when the output is redirected.

## Client SDK, CLI, and MCP server

`clients/python/` is the `perfarena` package: an SDK (`Arena.run(RunSpec)`),
a CLI (`perfarena run spec.yaml`), and an MCP server (`perfarena mcp`) for
agents. See `clients/python/README.md`. The scripts below stay the reference
implementation that the SDK is parity-tested against.

## Running stuff

### Submit one solution from a local LLM

```bash
export ARENA_API_KEY=arena_…
python webapp/examples/submit_with_local_model.py \
  --base-url http://localhost:8000 \
  --slug two-sum --language python3 \
  --ollama-url http://localhost:11434 --model qwen2.5-coder:7b
```

### Sweep many (prompt × language × problem) tuples

```bash
export ARENA_API_KEY=arena_…
python webapp/examples/matrix_sweep.py \
  --base-url http://localhost:8000 \
  --ollama-url http://localhost:11434 \
  --models qwen2.5-coder:7b,deepseek-coder:6.7b \
  --languages python3,cpp \
  --slugs two-sum,merge-intervals \
  --prompts baseline-2025q4,baseline-with-shape \
  --inter-problem-delay 60 --inter-tuple-delay 5
```

Add `--rounds 3` to enable iterative refinement (the script auto-picks the matching prompt family).

The sweep is **resumable** — kill it and re-run; the progress log skips completed tuples.

### Run a population batch from the legacy one-shot path

```bash
python leetbot.py two-sum python3 data/test_submissions/two-sum.py
# → appends a line to data/runs.jsonl
```

This bypasses the platform; useful for smoke-testing LeetCode auth.

## Configuration (env vars)

### Container / runtime

| var | default | purpose |
|-----|---------|---------|
| `ARENA_DB` | `webapp/backend/arena.db` (`/data/arena.db` in Docker) | SQLite path |
| `ARENA_SERVE_FRONTEND_DIR` | unset (Docker: `/app/web`) | Serve the SPA at `/` from this dir |
| `ARENA_IP_RATE_LIMIT_ENABLED` | `1` | Toggle per-(IP, endpoint) GET cap |
| `ARENA_IP_RATE_LIMIT_PER_MIN` | `60` | Cap value |
| `ARENA_IP_RATE_LIMIT_WINDOW_S` | `60` | Window length |
| `ARENA_TRUST_PROXY` | `0` | Honour `X-Forwarded-For` (only when behind a trusted proxy) |
| `ARENA_BOOTSTRAP_ADMIN_EMAIL` | unset | Create an admin on first boot (idempotent) |
| `ARENA_BOOTSTRAP_ADMIN_NAME` | `Admin` | Display name |
| `ARENA_BOOTSTRAP_ADMIN_API_KEY` | auto | Use this exact key; else one is generated (stored hashed) |
| `ARENA_BOOTSTRAP_ADMIN_PASSWORD` | unset | If set, also enables `/login` for that user |
| `ARENA_SESSION_SECRET` | per-process random | Signs SPA session tokens. **Set a stable value** or restarts log users out |
| `ARENA_ENABLE_DOCS` | unset (off) | `1` → expose `/docs`, `/redoc`, `/openapi.json` (off in prod) |
| `ARENA_CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins (prod is same-origin) |

### Dispatch

| var | default | purpose |
|-----|---------|---------|
| `ARENA_DISPATCH_ENABLED` | `0` | `1` → real LeetCode; else fake judge |
| `ARENA_DISPATCH_INTER_DELAY` | `12` | Seconds between LC submits within one batch (±20% jitter) |
| `ARENA_DISPATCH_MAX_RETRIES` | `4` | Retries with exponential backoff on 429 |
| `ARENA_DISPATCH_SUBPROC_TIMEOUT` | `180` | Per-attempt subprocess timeout |
| `LEETCODE_COOKIE_HEADER` | — | Full `Cookie:` header (required for real dispatch) |
| `LEETCODE_SESSION` + `LEETCODE_CSRF_TOKEN` | — | Alternative to the header |
| `LEETCODE_HOST` | `https://leetcode.com` | Set to `https://leetcode.cn` for the China site |

### Harness runner (measurement host)

Set on the runner process (see Deployment §3). The `--auth-token` flag / `HARNESS_RUNNER_TOKEN` gates who may execute code; the rest control isolation and measurement.

| var / flag | default | purpose |
|-----|---------|---------|
| `--auth-token` (or `HARNESS_RUNNER_TOKEN`) | empty (open) | Bearer the backend must present; set it in any reachable deployment |
| `--host` | `0.0.0.0` | Bind `127.0.0.1` when the backend is same-host |
| `PERFARENA_ROOT` | auto-detected | Energy-Languages fork root (falls back to auto-detect if unset) |
| `TSC` | `npx --yes -p typescript@…` | Set to `tsc` to use an installed compiler (no download under egress ban) |
| `PERFARENA_ISOLATION_CHECK` | `1` | Refuse to measure if another process is pinning a core |
| `PERFARENA_HANDLER_TIMEOUT_S` | `1200` | CLBG measure ceiling: kill one `make measure` (all runs) past this → Time Limit Exceeded |
| `PERFARENA_LC_MEASURE_TIMEOUT_S` | `420` | LeetCode measure ceiling: wall budget over the repeat loop; repeats past this → Time Limit Exceeded |
| `PERFARENA_CASE_TIMEOUT_S` | `180` | Cap on a single LeetCode measurement run |
| `PERFARENA_REQUEST_TIMEOUT_S` (or `--request-timeout`) | `0` (off) | Global watchdog backstop: SIGKILL the whole request past this. Set ABOVE the two ceilings and BELOW the backend harness `config.timeout_s` |
| `PERFARENA_LOG_FILE` | unset | Tee all runner output (banner, RX/START/DONE, tracebacks) to this file for later inspection |

### Submission limits

| var | default | purpose |
|-----|---------|---------|
| `ARENA_SUBMIT_COOLDOWN_S` | `600` | Min seconds between batches per user |
| `ARENA_SUBMIT_DAILY_CAP` | `5` | Max batches per rolling 24h per user |

Backend reads `webapp/backend/.env` at startup via `python-dotenv`.

## Testing

```bash
cd webapp/backend
pytest                              # 152+ tests; runs offline (fake judge)
pytest -k ip_ratelimit              # one module
pytest -k iterative_refine -vv      # verbose, single module
```

Tests **don't touch real LeetCode** — `conftest.py` pins `ARENA_DISPATCH_ENABLED=0`. Don't override that in test code; if you need to simulate dispatch behaviour, mock at the module boundary.

The frontend doesn't have a test suite yet (PRs welcome — the components are small and pure enough for a Vitest pass).

## Contributing

PerfArena is research code, but it's structured for outside contributions. Two rules dominate everything else:

### 1. The DB is sacred. All schema changes are additive.

Every column you add lands in `db.py:_ADDITIVE_COLUMNS` so a redeploy on top of existing data Just Works™. Never drop, rename, or retype a column on a populated DB. If you genuinely need a destructive change, write a backfill script under `webapp/backend/app/` and call it out explicitly in the PR.

### 2. The population scripts use the public API like a user would.

Don't read or write the DB from `webapp/examples/`. If a sweep needs a new field, expose it through an API endpoint with a typed response in `schemas.py`, then have the sweeper hit that endpoint. This keeps third-party clients on equal footing with the official sweepers.

### Code conventions

- **Backend.** SQLModel models in `models.py`; Pydantic response shapes in `schemas.py` (never return raw model objects). FastAPI dependency-inject auth via `Depends(current_user)` / `current_admin`. Long files are fine — `main.py` is intentionally monolithic for the public API surface.
- **Frontend.** No chart library — inline SVG, see `CompareHistogram.tsx` and `RunsChart.tsx` for the pattern. Theme via CSS vars (`var(--accent)`, `var(--panel-2)`, …) so dark mode works automatically. New pages go in `src/pages/`, register the route in `App.tsx`, and the tab in `Layout.tsx`.
- **Comments.** Document *why*, not *what*. The codebase explains its trade-offs (see `ip_ratelimit.py`'s docstring for the canonical style); match that.

### PR process

1. Branch from `main` (the working branch is `master`; PRs merge into `main`).
2. Add or extend tests under `webapp/backend/tests/` for any new endpoint, schema change, or behaviour switch.
3. Run `pytest` — all 152+ tests must pass. The CI gate is the same.
4. Keep the diff focused; mixed-concern PRs get split.
5. The maintainers will run `/ultrareview` on the branch before merging.

### Reporting bugs / asking questions

Open a GitHub issue with the failing command, the response body, and `docker logs` output if it's container-related. For data-quality issues (a leaderboard row that looks wrong), attach the submission ID — the provenance chain makes triage straightforward.
