#!/usr/bin/env python3
"""Fill a paper-data cache from a local copy of the arena database, offline.

The paper scripts read the arena through two public endpoints, the submission
list and the attempts of one submission. This script calls the backend's own
endpoints in-process (FastAPI TestClient, no server and no startup hooks, so
nothing in the copy changes) and writes their responses where the scripts look:

    <cache-dir>/submissions.json        GET /api/submissions
    <cache-dir>/attempts_<id>.json      GET /api/submissions/<id>/attempts

for every submission of the paper's competitions. Run it with the backend's
Python environment (it needs FastAPI), then point the paper scripts at the
cache with PERFARENA_CACHE=<cache-dir>. Run it from the repo root:

    backend/.venv/bin/python analysis/snapshot_to_cache.py \
        --db ~/.cache/perfarena-paper/snapshots/arena-<stamp>.db \
        --cache-dir ~/.cache/perfarena-paper/snapshot-<stamp>

With --export, the script first builds the database from an admin export
(GET /api/admin/export) through the backend's own import, if --db does not
exist yet.
"""
import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
COMPETITIONS = ("leetcode-99-perf", "clbg-10")

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True, help="SQLite copy of the arena database")
ap.add_argument("--cache-dir", required=True, help="where to write the responses")
ap.add_argument("--export", help="admin export (JSON) to build --db from")
args = ap.parse_args()

os.environ["ARENA_DB"] = str(Path(args.db).expanduser())
os.environ["ARENA_IP_RATE_LIMIT_ENABLED"] = "0"
os.environ["ARENA_DISPATCH_ENABLED"] = "0"
sys.path.insert(0, str(BACKEND))
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app                   # noqa: E402

db = Path(args.db).expanduser()
if args.export and not db.exists():
    from sqlmodel import Session           # noqa: E402
    from app.db import engine, init_db      # noqa: E402
    from app import export_import           # noqa: E402
    init_db()
    bundle = json.loads(Path(args.export).read_text())
    # A slim export (exclude_columns) leaves out columns such as
    # attempt.inference_trace that the schema declares NOT NULL. Fill such a
    # missing text column with an empty string so the import accepts the row.
    for name, model in export_import.EXPORTABLE:
        required = [c.name for c in model.__table__.columns
                    if not c.nullable and c.default is None and not c.primary_key
                    and str(c.type).upper().startswith(("VARCHAR", "TEXT"))]
        for row in bundle.get("data", {}).get(name, []):
            for col in required:
                row.setdefault(col, "")
    with Session(engine) as session:
        report = export_import.import_bundle(session, bundle)
        session.commit()
    errored = {t: r["errored"] for t, r in report["counts"].items() if r.get("errored")}
    print(f"built {db} from {args.export}" + (f", errored rows: {errored}" if errored else ""))

client = TestClient(app)                   # no `with`: the startup hook stays off
out = Path(args.cache_dir).expanduser()
out.mkdir(parents=True, exist_ok=True)

r = client.get("/api/submissions")
r.raise_for_status()
subs = r.json()
(out / "submissions.json").write_text(json.dumps(subs))
keep = [s for s in subs if s.get("competition_slug") in COMPETITIONS]
for s in keep:
    r = client.get(f"/api/submissions/{s['id']}/attempts")
    r.raise_for_status()
    (out / f"attempts_{s['id']}.json").write_text(json.dumps(r.json()))
print(f"wrote {out}: {len(subs)} submissions, attempts of {len(keep)}")
