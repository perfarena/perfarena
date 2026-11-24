#!/usr/bin/env bash
# Register the running harness container (:8787) with the backend as BOTH the
# performance harness and the correctness oracle, under slug `arena-local`.
# Run after `docker compose --project-directory . -f infra/docker-compose.local.yml up -d`.
#
# The backend reaches the runner over the compose network at http://harness:8787
# (the service name), NOT localhost.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
COMPOSE=(docker compose --project-directory . -f infra/docker-compose.local.yml)

# The runner's token (compose passes PERFARENA_RUNNER_TOKEN to it): from the
# environment, else from the root .env. Empty = an open runner (dev default).
[ -n "${PERFARENA_RUNNER_TOKEN:-}" ] || PERFARENA_RUNNER_TOKEN=$(sed -n 's/^PERFARENA_RUNNER_TOKEN=//p' .env 2>/dev/null | tail -1 | tr -d "\"'")

"${COMPOSE[@]}" exec -T -e PERFARENA_RUNNER_TOKEN="${PERFARENA_RUNNER_TOKEN:-}" backend python - <<'PY'
import os
from sqlmodel import Session, select
from app.db import engine
from app.models import Harness, CorrectnessOracle

ENDPOINT = "http://harness:8787"     # compose service name, not localhost
# Backend httpx timeout for /measure. MUST exceed the runner watchdog
# (PERFARENA_REQUEST_TIMEOUT_S, 1320s) so the backend waits for the runner's
# verdict — including a Time Limit Exceeded — instead of giving up early and
# recording "dispatch failed" while the measurement is still running. (Validate
# is separately capped at 300s in arena_local_runner, so this large value only
# affects /measure.)
CONFIG = {"endpoint": ENDPOINT, "timeout_s": 1380}
if os.environ.get("PERFARENA_RUNNER_TOKEN"):
    CONFIG["auth_token"] = os.environ["PERFARENA_RUNNER_TOKEN"]
with Session(engine) as s:
    for Model, name in ((Harness, "Local Docker runner (perf)"),
                        (CorrectnessOracle, "Local Docker runner (correctness)")):
        row = s.exec(select(Model).where(Model.slug == "arena-local")).first()
        if row is None:
            row = Model(slug="arena-local", name=name, kind="arena-local",
                        config=CONFIG, status="active")
        else:
            row.kind = "arena-local"
            row.config = CONFIG
            row.status = "active"
        s.add(row)
    s.commit()
    print("OK: registered arena-local Harness + CorrectnessOracle ->", ENDPOINT,
          "(with the runner token)" if "auth_token" in CONFIG else "(no token: open runner)")
PY
