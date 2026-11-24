#!/usr/bin/env python3
"""Write the memory re-measure jobs: one per accepted campaign solution of a
competition, with its source code from a database snapshot, for the platform
script scripts/remeasure_memory.py.

    PERFARENA_CACHE=<cache> python3 analysis/memory_jobs.py \
        --db ~/.cache/perfarena-paper/snapshots/arena-20261009-1915.db \
        --competition leetcode-99-perf --out memory_jobs.json
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(__file__))
import gen_paper_data as g  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--competition", default="leetcode-99-perf")
ap.add_argument("--out", required=True)
args = ap.parse_args()

models = [m for m in g.DEFAULT_MODELS if m not in g.EXCLUDE_MODELS]
base = os.environ.get("ARENA_BASE", "http://offline.invalid")
sel = g.campaign_submissions(base, "", args.competition, models)
langs = [s for s, _ in g.competition_languages(base, "", args.competition, models)]
db = sqlite3.connect(f"file:{os.path.expanduser(args.db)}?mode=ro", uri=True)
jobs = []
for (m, lang), (_sub, per) in sorted(sel.items()):
    if lang not in langs:
        continue
    for slug, a in sorted(per.items()):
        if not a.get("accepted"):
            continue
        row = db.execute("select code from attempt where id = ?", (a["attempt_id"],)).fetchone()
        if not row or not row[0]:
            print(f"no code for attempt {a['attempt_id']} ({m} {lang} {slug})", file=sys.stderr)
            continue
        jobs.append({"attempt_id": a["attempt_id"], "model": m, "language": lang,
                     "title_slug": slug, "code": row[0]})
json.dump(jobs, open(args.out, "w"))
print(f"wrote {len(jobs)} jobs to {args.out}")
