#!/usr/bin/env python3
"""Re-measure the jobs on the bicho runner: latency and energy (7 runs), then
peak memory (3 readings), per solution, as the campaign and the memory
re-measure did. The runner builds each solution first and measures only the
prebuilt program. Appends one JSON line per finished job, so a rerun skips the
jobs already in --out; stop it at any time and start it again.

    export PERFARENA_RUNNER_TOKEN=...      # the runner's auth token
    python3 remeasure.py --jobs jobs_full.json --out results.jsonl --check
    python3 remeasure.py --jobs jobs_full.json --out results.jsonl

--check measures one job of each language briefly and stops: it shows the
runner's toolchain record and compares its harness hashes with manifest.json,
to confirm the deploy. The runner answers 429 while it is busy
(e.g. with a production job); the script then waits.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_KEYS = ("status_msg", "runtime_ms", "runtime_energy_j", "iterations",
            "time_us_per_op", "energy_j_per_op", "raw_response")
JOB_KEYS = ("key", "competition", "model", "language", "title_slug", "submission_id",
            "logged_at", "cache_attempt_id")


def measure(runner: str, token: str, job: dict, repeats: int, metrics: list[str],
            timeout: float) -> dict:
    body = {"title_slug": job["title_slug"], "language": job["language"], "code": job["code"],
            "repeats": repeats, "compile": True, "metrics": metrics,
            "meta": {"purpose": "js-ts-remeasure", "key": job["key"]}}
    wait = 5.0
    while True:
        req = urllib.request.Request(
            runner.rstrip("/") + "/measure", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:                       # busy, or the host is not quiet
                time.sleep(wait)
                wait = min(wait * 2, 120)
                continue
            return {"runs": [], "error": f"HTTP {e.code}: {e.read()[:300]!r}"}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"  runner unreachable ({e}); retrying in {wait:.0f}s", flush=True)
            time.sleep(wait)
            wait = min(wait * 2, 120)


def status(res: dict) -> str:
    runs = res.get("runs") or []
    if not runs:
        return res.get("error", "no runs")
    bad = [r.get("status_msg") for r in runs if r.get("status_msg") != "Accepted"]
    return "Accepted" if not bad else bad[0]


def check(args, token, jobs) -> int:
    man = json.load(open(os.path.join(HERE, "manifest.json")))
    want = {k.split("/", 1)[1] if k.startswith("Energy-Languages/") else os.path.basename(k): v["sha16"]
            for k, v in man.items()}
    ok = True
    for lang in sorted({j["language"] for j in jobs}):
        job = next(j for j in jobs if j["language"] == lang and j.get("code"))
        res = measure(args.runner, token, job, 1, ["time"], args.timeout)
        runs = res.get("runs") or []
        tc = (runs[0].get("toolchain") if runs else None) or {}
        print(f"{lang} {job['title_slug']}: {status(res)}, "
              f"{runs[0].get('time_us_per_op') if runs else None} us/call, tools {tc.get('tools')}")
        for name, h in (tc.get("harness") or {}).items():
            exp = want.get(name)
            mark = "ok" if exp == h else ("not in bundle" if exp is None else "DIFFERENT")
            ok &= mark != "DIFFERENT"
            print(f"  {name}: {h} {mark}")
        ok &= bool(tc) and status(res) == "Accepted"
    print("deploy looks right" if ok else "CHECK FAILED: the runner does not run the bundle's harness")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runner", default="http://10.228.128.30:8787")
    ap.add_argument("--competitions", default="leetcode-99-perf")
    ap.add_argument("--languages", default="", help="comma list; default: every language of --jobs")
    ap.add_argument("--runs", type=int, default=7, help="latency/energy runs (campaign: 7)")
    ap.add_argument("--memory-runs", type=int, default=3, help="memory readings (campaign: 3)")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    token = os.environ.get("PERFARENA_RUNNER_TOKEN", "")
    if not token:
        sys.exit("set PERFARENA_RUNNER_TOKEN to the runner's auth token")

    comps = set(args.competitions.split(","))
    jobs = [j for j in json.load(open(args.jobs)) if j["competition"] in comps]
    if args.languages:
        jobs = [j for j in jobs if j["language"] in set(args.languages.split(","))]
    if args.check:
        return check(args, token, jobs)
    nocode = [j["key"] for j in jobs if not j.get("code")]
    if nocode:
        sys.exit(f"{len(nocode)} jobs have no code (run fill_code.py first), e.g. {nocode[0]}")
    done = set()
    if os.path.exists(args.out):
        for line in open(args.out):
            try:
                done.add(json.loads(line)["key"])
            except (ValueError, KeyError):
                pass
    todo = [j for j in jobs if j["key"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(jobs)} jobs, {len(done)} already done, {len(todo)} to run", flush=True)

    t0 = time.time()
    for n, job in enumerate(todo, 1):
        timing = measure(args.runner, token, job, args.runs, ["time", "energy"], args.timeout)
        memory = measure(args.runner, token, job, args.memory_runs, ["memory"], args.timeout)
        truns, mruns = timing.get("runs") or [], memory.get("runs") or []
        rec = {k: job[k] for k in JOB_KEYS}
        rec["timing"] = {"status": status(timing),
                         "runs": [{k: r.get(k) for k in RUN_KEYS} for r in truns]}
        rec["memory"] = {"status": status(memory),
                         "memory_mb": [r.get("memory_mb") for r in mruns if r.get("memory_mb") is not None],
                         "method": (mruns[0].get("raw_response") or {}).get("memory_method") if mruns else None}
        first = (truns or mruns or [{}])[0]
        rec["toolchain"], rec["machine"] = first.get("toolchain"), first.get("machine")
        rec["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(args.out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        rate = (time.time() - t0) / n
        us = [r.get("time_us_per_op") for r in truns if r.get("time_us_per_op")]
        print(f"[{n}/{len(todo)}] {job['competition']} {job['language']} {job['model']} {job['title_slug']}: "
              f"timing {rec['timing']['status']} ({len(truns)} runs, "
              f"{(sum(us) / len(us)) if us else float('nan'):.3g} us), memory {rec['memory']['status']} "
              f"{rec['memory']['memory_mb']} | {rate:.0f} s/job, ~{rate * (len(todo) - n) / 3600:.1f} h left",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
