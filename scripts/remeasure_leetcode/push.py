#!/usr/bin/env python3
"""Replace the latency, energy, and memory of the re-measured attempts in
the production database, through the admin endpoints
POST /api/admin/attempts/timing and POST /api/admin/attempts/memory.

Production's attempt IDs differ from the paper cache's (the database was
rebuilt), so the script first finds each attempt's current ID from
GET /api/submissions/{id}/attempts, by problem and logged_at, one submission
at a time. The timing endpoint also checks the problem, language, and
submission before it writes. The memory endpoint checks nothing, so a record
is pushed only with an ID resolved this way.

    export ARENA_API_KEY=$(cat ~/.perfarena_admin_key)
    python3 push.py --results results.jsonl --backend https://perfarena.ngrok.app --dry-run
    python3 push.py --results results.jsonl --backend https://perfarena.ngrok.app

Only a job whose runs are all Accepted is pushed, so a failed re-measure keeps
the old values; the failed jobs are listed in push_skipped.json. The old values
stay hidden in each run's raw_response (keep_previous), for audit and rollback.
Each run's raw_response also carries the runner's toolchain record. All
requests are sequential, with a pause between them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

RUN_KEYS = ("runtime_ms", "runtime_energy_j", "iterations", "time_us_per_op", "energy_j_per_op")


def call(base, key, path, body=None, timeout=600):
    req = urllib.request.Request(
        base.rstrip("/") + path, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                 "ngrok-skip-browser-warning": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def stamp(t):
    return (t or "").replace("T", " ")[:19]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--backend", required=True)
    ap.add_argument("--competitions", default="leetcode-99-perf,clbg-10")
    ap.add_argument("--what", default="timing,memory")
    ap.add_argument("--method", default="LeetCode re-measure on bicho: prebuilt programs only "
                                        "(no build in the measured process); JS/TS shared Node loop, "
                                        "new Function loader, TypeScript ES2022 strict")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-keep-previous", action="store_true")
    args = ap.parse_args()
    key = os.environ.get("ARENA_API_KEY", "")
    if not key:
        sys.exit("set ARENA_API_KEY to the admin API key")
    comps, what = set(args.competitions.split(",")), set(args.what.split(","))

    latest = {}
    for line in open(args.results):
        if line.strip():
            r = json.loads(line)
            if r["competition"] in comps:
                latest[r["key"]] = r          # a later line for the same job wins
    recs = list(latest.values())

    ids, unresolved = {}, []
    for sid in sorted({r["submission_id"] for r in recs}):
        atts = call(args.backend, key, f"/api/submissions/{sid}/attempts")
        atts = atts if isinstance(atts, list) else atts.get("items") or atts.get("attempts") or []
        index = {}
        for a in atts:
            index.setdefault((a["problem_slug"], stamp(a.get("submitted_at"))), []).append(a["attempt_id"])
        for r in (r for r in recs if r["submission_id"] == sid):
            found = index.get((r["title_slug"], stamp(r["logged_at"])), [])
            if len(found) == 1:
                ids[r["key"]] = found[0]
            else:
                unresolved.append((r["key"], len(found)))
        time.sleep(args.pause)
    print(f"{len(recs)} records, {len(ids)} resolved, {len(unresolved)} unresolved"
          + (f": {unresolved[:5]}" if unresolved else ""), flush=True)
    if unresolved:
        sys.exit("not pushing: every record must resolve to exactly one attempt")

    timing, memory, skipped = [], [], []
    for r in recs:
        t, m = r["timing"], r["memory"]
        if "timing" in what:
            if t["status"] == "Accepted" and t["runs"]:
                timing.append({
                    "attempt_id": ids[r["key"]], "title_slug": r["title_slug"], "language": r["language"],
                    "submission_id": r["submission_id"], "method": args.method,
                    "runs": [{**{k: x.get(k) for k in RUN_KEYS},
                              "raw_response": {**(x.get("raw_response") or {}), "toolchain": r.get("toolchain")}}
                             for x in t["runs"]]})
            else:
                skipped.append({"key": r["key"], "what": "timing", "status": t["status"]})
        if "memory" in what:
            if m["status"] == "Accepted" and m["memory_mb"]:
                memory.append({"attempt_id": ids[r["key"]], "memory_mb": m["memory_mb"], "method": m.get("method")})
            else:
                skipped.append({"key": r["key"], "what": "memory", "status": m["status"]})
    json.dump(skipped, open("push_skipped.json", "w"), indent=1)
    print(f"timing items {len(timing)}, memory items {len(memory)}, skipped {len(skipped)} "
          f"(push_skipped.json)", flush=True)

    keep = not args.no_keep_previous
    for path, items in (("/api/admin/attempts/timing", timing), ("/api/admin/attempts/memory", memory)):
        total = 0
        for i in range(0, len(items), args.batch):
            res = call(args.backend, key, path, {"items": items[i:i + args.batch],
                                                 "dry_run": args.dry_run, "keep_previous": keep})
            total += res["updated"]
            print(f"  {path} batch {i // args.batch + 1}: updated={res['updated']} "
                  f"skipped={len(res['skipped'])}"
                  + (f" first skip: {res['skipped'][0]}" if res["skipped"] else ""), flush=True)
            time.sleep(args.pause)
        if items:
            print(f"{path}: {'would update' if args.dry_run else 'updated'} {total} of {len(items)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
