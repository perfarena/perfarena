#!/usr/bin/env bash
# Register the compose replica runners (harness2, harness3) as replicas of the
# `arena-local` harness AND correctness oracle (see register-harness.sh for the
# primary). Run after:
#   docker compose -f docker-compose.local.yml --profile replicas up -d
#
# Registering replicas does not change any dispatch by itself: with no
# config.replicas.policy the pool uses `least_busy` (correctness) and `sticky`
# (performance). Pick another policy by editing the oracle's config, e.g.
#   "replicas": {"policy": "split_repeats"}
# See webapp/backend/app/replicas.py for every policy.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
COMPOSE=(docker compose -f docker-compose.local.yml)

"${COMPOSE[@]}" exec -T backend python - <<'PY'
from sqlmodel import Session, select
from app.db import engine
from app.models import CorrectnessOracle, Harness, OracleReplica

REPLICAS = {"harness2": "http://harness2:8787", "harness3": "http://harness3:8787"}
with Session(engine) as s:
    for Model, col in ((Harness, "harness_id"), (CorrectnessOracle, "correctness_oracle_id")):
        oracle = s.exec(select(Model).where(Model.slug == "arena-local")).first()
        if oracle is None:
            raise SystemExit("run register-harness.sh first (no arena-local oracle)")
        for name, endpoint in REPLICAS.items():
            row = s.exec(select(OracleReplica).where(
                getattr(OracleReplica, col) == oracle.id,
                OracleReplica.endpoint == endpoint)).first()
            if row is None:
                s.add(OracleReplica(name=f"arena-local-{name}", endpoint=endpoint,
                                    **{col: oracle.id}))
    s.commit()
    print("OK: registered", ", ".join(REPLICAS.values()),
          "as replicas of arena-local (harness + correctness oracle)")
PY
