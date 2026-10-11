#!/usr/bin/env python3
"""Re-measure ONLY the peak memory of accepted solutions on a harness runner.

The memory pass now builds each solution first and measures the prebuilt
program (run_harness.sh build/run), with peak RSS from wait4 for both
competitions. Earlier LeetCode memory values included the compiler, so this
script re-measures them without touching latency or energy.

Input: a JSON list of jobs, one per accepted attempt:
    [{"attempt_id": 123, "title_slug": "frog-jump", "language": "cpp",
      "code": "..."}, ...]
(figs/scripts/memory_jobs.py in the paper repo writes it from a snapshot.)

Output: JSON lines, one per finished job, appended as it goes, so a rerun
skips the jobs already in the file:
    {"attempt_id": 123, "memory_mb": [2.1, 2.1, 2.0], "method": "...",
     "status": "Accepted"}

Run it on the measurement host, against the runner, while nothing else is
measuring (the runner answers 429 while it is busy; the script waits):
    PERFARENA_RUNNER_TOKEN=... python3 scripts/remeasure_memory.py \
        --jobs memory_jobs.json --out memory_remeasured.jsonl \
        --runner http://127.0.0.1:8787

With --push <backend URL> (admin key in ARENA_API_KEY), the new readings also
REPLACE the attempts' memory in the PerfArena database, in batches, through
POST /api/admin/attempts/memory: the old values are removed and the attempt and
submission aggregates recomputed. --push-dry-run asks the backend what would
change without writing. --push-only re-sends the results already in --out
without measuring (e.g. after a dry run).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def post_measure(runner: str, token: str, job: dict, repeats: int, timeout: float) -> dict:
    body = {
        "title_slug": job["title_slug"],
        "language": job["language"],
        "code": job["code"],
        "repeats": repeats,
        "compile": True,
        "metrics": ["memory"],
        "meta": {"attempt_id": job["attempt_id"], "purpose": "memory-remeasure"},
    }
    req = urllib.request.Request(
        runner.rstrip("/") + "/measure", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def push(base: str, key: str, recs: list[dict], dry_run: bool, keep_previous: bool) -> dict:
    items = [{"attempt_id": r["attempt_id"], "memory_mb": r["memory_mb"], "method": r.get("method")}
             for r in recs]
    body = {"items": items, "dry_run": dry_run, "keep_previous": keep_previous}
    req = urllib.request.Request(
        base.rstrip("/") + "/api/admin/attempts/memory", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runner", default="http://127.0.0.1:8787")
    ap.add_argument("--repeats", type=int, default=3,
                    help="memory readings per solution (the campaign used 3)")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--languages", default="", help="comma list to restrict to")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--push", default="", metavar="BACKEND_URL",
                    help="also replace the memory values in this PerfArena backend")
    ap.add_argument("--push-batch", type=int, default=50)
    ap.add_argument("--push-dry-run", action="store_true")
    ap.add_argument("--push-only", action="store_true",
                    help="push the results already in --out, without measuring")
    ap.add_argument("--keep-previous", action="store_true",
                    help="keep each old value hidden in the run's raw_response")
    args = ap.parse_args()
    token = os.environ.get("PERFARENA_RUNNER_TOKEN", "")
    key = os.environ.get("ARENA_API_KEY", "")
    if args.push and not key:
        sys.exit("--push needs the admin API key in ARENA_API_KEY")
    pending: list[dict] = []

    def flush() -> None:
        if not (args.push and pending):
            return
        res = push(args.push, key, pending, args.push_dry_run, args.keep_previous)
        print(f"  pushed {len(pending)}: updated={res['updated']} "
              f"skipped={len(res['skipped'])} dry_run={res['dry_run']}"
              + (f" first skip: {res['skipped'][0]}" if res["skipped"] else ""), flush=True)
        pending.clear()

    def pushable(rec: dict) -> bool:
        return rec.get("status") == "Accepted" and bool(rec.get("memory_mb"))

    if args.push_only:
        recs = [json.loads(line) for line in open(args.out) if line.strip()]
        for rec in recs:
            if pushable(rec):
                pending.append(rec)
                if len(pending) >= args.push_batch:
                    flush()
        flush()
        return 0

    jobs = json.load(open(args.jobs))
    if args.languages:
        keep = {s.strip() for s in args.languages.split(",") if s.strip()}
        jobs = [j for j in jobs if j["language"] in keep]
    done = set()
    if os.path.exists(args.out):
        for line in open(args.out):
            try:
                done.add(json.loads(line)["attempt_id"])
            except (ValueError, KeyError):
                pass
    todo = [j for j in jobs if j["attempt_id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(jobs)} jobs, {len(done)} already done, {len(todo)} to run", flush=True)

    t_start = time.time()
    for n, job in enumerate(todo, 1):
        wait = 5.0
        while True:
            try:
                res = post_measure(args.runner, token, job, args.repeats, args.timeout)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:            # runner busy or host not quiet: wait
                    time.sleep(wait)
                    wait = min(wait * 2, 120)
                    continue
                res = {"runs": [], "error": f"HTTP {e.code}: {e.read()[:300]!r}"}
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                print(f"  runner unreachable ({e}); retrying in {wait:.0f}s", flush=True)
                time.sleep(wait)
                wait = min(wait * 2, 120)
        runs = res.get("runs") or []
        mems = [r.get("memory_mb") for r in runs if r.get("memory_mb") is not None]
        rec = {
            "attempt_id": job["attempt_id"],
            "memory_mb": mems,
            "status": runs[0].get("status_msg") if runs else res.get("error", "no runs"),
            "method": ((runs[0].get("raw_response") or {}).get("memory_method") if runs else None),
        }
        with open(args.out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        if pushable(rec):
            pending.append(rec)
            if len(pending) >= args.push_batch:
                flush()
        rate = (time.time() - t_start) / n
        print(f"[{n}/{len(todo)}] {job['language']}/{job['title_slug']} att={job['attempt_id']} "
              f"-> {rec['status']} mem={mems} ({rate:.1f}s/job, "
              f"~{rate * (len(todo) - n) / 3600:.1f} h left)", flush=True)
    flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
