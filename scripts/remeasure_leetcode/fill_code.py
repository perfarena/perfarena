#!/usr/bin/env python3
"""Fill in the code of the jobs that the local snapshot lacked, from the
production solutions export (GET /api/datasets/solutions), one request per
(LLM, language), one after the other, with a pause between them.

    python3 fill_code.py --jobs jobs.json --out jobs_full.json \
        --backend https://perfarena.ngrok.app

A job matches an exported solution by submission, problem, and logged_at.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request


def stamp(t):
    return (t or "").replace("T", " ")[:19]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", required=True)
    ap.add_argument("--pause", type=float, default=5.0, help="seconds between requests")
    args = ap.parse_args()

    jobs = json.load(open(args.jobs))
    todo = [j for j in jobs if not j.get("code")]
    groups = sorted({(j["model"], j["language"]) for j in todo})
    print(f"{len(jobs)} jobs, {len(todo)} without code, {len(groups)} requests", flush=True)
    found = {}
    for model, lang in groups:
        want = {(j["submission_id"], j["title_slug"], j["logged_at"]) for j in todo
                if (j["model"], j["language"]) == (model, lang)}
        q = urllib.parse.urlencode({"language": lang, "model": model, "only_accepted": "true"})
        req = urllib.request.Request(f"{args.backend.rstrip('/')}/api/datasets/solutions?{q}",
                                     headers={"ngrok-skip-browser-warning": "1"})
        n = 0
        with urllib.request.urlopen(req, timeout=1800) as r:
            for line in r:
                rec = json.loads(line)
                k = (rec["submission_id"], rec["problem_slug"], stamp(rec.get("logged_at")))
                if k in want and rec.get("code"):
                    found[k] = rec["code"]
                    n += 1
        print(f"  {model} {lang}: {n} of {len(want)} found", flush=True)
        time.sleep(args.pause)
    for j in jobs:
        if not j.get("code"):
            j["code"] = found.get((j["submission_id"], j["title_slug"], j["logged_at"]))
    missing = [j["key"] for j in jobs if not j.get("code")]
    json.dump(jobs, open(args.out, "w"))
    print(f"wrote {args.out}; still without code: {len(missing)}" + (f", e.g. {missing[:3]}" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
