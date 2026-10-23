#!/usr/bin/env python3
"""Replace the latency and energy of re-measured attempts in a PerfArena
backend, through POST /api/admin/attempts/timing (memory stays as it is).

Input: the JSON lines of the bicho re-measure (remeasure.py), one record per
attempt with its submission_id, title_slug, logged_at, language, and the 7 new
runs. Attempt IDs in a database snapshot can differ from the backend's (the
production database was rebuilt and renumbered), so the script first resolves
each record's current attempt ID from GET /api/submissions/{id}/attempts, by
problem and logged_at, one submission at a time. The backend checks the
problem, language, and submission again before it writes.

    ARENA_API_KEY=$(cat ~/.perfarena_admin_key) python3 scripts/push_timing_remeasure.py \
        --backend https://perfarena.ngrok.app --results cpp_remeasure.jsonl --dry-run
    (then the same without --dry-run)

The requests are sequential, with a pause between them, so the backend is
never under load.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

RUN_KEYS = ("runtime_ms", "runtime_energy_j", "iterations", "time_us_per_op", "energy_j_per_op")


def call(base: str, key: str, path: str, body: dict | None = None, timeout: float = 600):
    req = urllib.request.Request(
        base.rstrip("/") + path, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                 "ngrok-skip-browser-warning": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def stamp(t: str | None) -> str:
    return (t or "").replace("T", " ")[:19]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True)
    ap.add_argument("--results", required=True, help="JSON lines of remeasure.py")
    ap.add_argument("--method", default="C++ harness optimization barrier "
                                        "(Energy-Languages 87980f36), bicho 2026-10-23")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-previous", action="store_true")
    args = ap.parse_args()
    key = os.environ.get("ARENA_API_KEY", "")
    if not key:
        sys.exit("needs the admin API key in ARENA_API_KEY")

    recs = [json.loads(line) for line in open(args.results) if line.strip()]
    bad = [r["key"] for r in recs if r.get("status") != "Accepted"
           or any(x.get("status_msg") != "Accepted" for x in r["runs"])]
    if bad:
        sys.exit(f"{len(bad)} records have a run that is not Accepted, e.g. {bad[0]}")

    items, unresolved = [], []
    for sid in sorted({r["submission_id"] for r in recs}):
        atts = call(args.backend, key, f"/api/submissions/{sid}/attempts")
        atts = atts if isinstance(atts, list) else atts.get("items") or atts.get("attempts") or []
        index: dict[tuple, list[int]] = {}
        for a in atts:
            index.setdefault((a["problem_slug"], stamp(a.get("submitted_at"))), []).append(a["attempt_id"])
        for r in (r for r in recs if r["submission_id"] == sid):
            ids = index.get((r["title_slug"], stamp(r["logged_at"])), [])
            if len(ids) != 1:
                unresolved.append((r["key"], len(ids)))
                continue
            items.append({
                "attempt_id": ids[0], "title_slug": r["title_slug"], "language": r["language"],
                "submission_id": sid, "method": args.method,
                "runs": [{**{k: x.get(k) for k in RUN_KEYS}, "raw_response": x.get("raw_response")}
                         for x in r["runs"]]})
        time.sleep(args.pause)
    print(f"{len(recs)} records, {len(items)} resolved, {len(unresolved)} unresolved"
          + (f": {unresolved[:5]}" if unresolved else ""), flush=True)
    if unresolved:
        sys.exit("not pushing: resolve every record first")

    total = 0
    for i in range(0, len(items), args.batch):
        res = call(args.backend, key, "/api/admin/attempts/timing",
                   {"items": items[i:i + args.batch], "dry_run": args.dry_run,
                    "keep_previous": args.keep_previous})
        total += res["updated"]
        print(f"  batch {i // args.batch + 1}: updated={res['updated']} "
              f"skipped={len(res['skipped'])} dry_run={res['dry_run']}"
              + (f" first skip: {res['skipped'][0]}" if res["skipped"] else ""), flush=True)
        for c in res["changes"][:3]:
            print(f"    attempt {c['attempt_id']}: {c['old_avg_runtime_ms']:.4g} -> "
                  f"{c['new_avg_runtime_ms']:.4g} ms", flush=True)
        time.sleep(args.pause)
    print(f"{'would update' if args.dry_run else 'updated'} {total} of {len(items)} attempts")
    return 0 if total == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
