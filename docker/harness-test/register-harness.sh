#!/usr/bin/env bash
# Register the running harness container (:8787) with the backend as BOTH the
# performance harness and the correctness oracle, under slug `arena-local`.
# Run after `docker compose -f docker-compose.local.yml up -d`.
#
# The backend reaches the runner over the compose network at http://harness:8787
# (the service name), NOT localhost.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
COMPOSE=(docker compose -f docker-compose.local.yml)

"${COMPOSE[@]}" exec -T backend python - <<'PY'
from sqlmodel import Session, select
from app.db import engine
from app.models import Harness, CorrectnessOracle

ENDPOINT = "http://harness:8787"     # compose service name, not localhost
with Session(engine) as s:
    for Model, name in ((Harness, "Local Docker runner (perf)"),
                        (CorrectnessOracle, "Local Docker runner (correctness)")):
        row = s.exec(select(Model).where(Model.slug == "arena-local")).first()
        if row is None:
            row = Model(slug="arena-local", name=name, kind="arena-local",
                        config={"endpoint": ENDPOINT}, status="active")
        else:
            row.kind = "arena-local"
            row.config = {"endpoint": ENDPOINT}
            row.status = "active"
        s.add(row)
    s.commit()
    print("OK: registered arena-local Harness + CorrectnessOracle ->", ENDPOINT)
PY
