#!/usr/bin/env python3
"""Write the JS/TS re-measure jobs: one per accepted campaign solution that the
paper ranks, with what the push needs to find the attempt again on the
production database (submission, problem, logged_at) and its code from a local
database snapshot (code that the snapshot lacks stays null; fill_code.py
fetches it).

Runs on the machine that holds the paper repo and its API cache:
    ARENA_BASE=http://offline.invalid PERFARENA_CACHE=~/.cache/perfarena-paper/snapshot-20261027-js4 \
    python3 make_jobs.py --db ~/.cache/perfarena-paper/snapshots/arena-20261011-2237.db --out jobs.json
"""
import argparse
import json
import os
import random
import sqlite3
import sys

PAPER = os.path.expanduser("~/repos/research/leetcode_crawler/paper/paper-perfarena/figs/scripts")
sys.path.insert(0, PAPER)
import gen_paper_data as g  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True, help="local database snapshot with attempt code")
ap.add_argument("--out", required=True)
ap.add_argument("--competitions", default="leetcode-99-perf,clbg-10")
ap.add_argument("--languages", default="javascript,typescript")
ap.add_argument("--seed", type=int, default=29)
args = ap.parse_args()

langs = args.languages.split(",")
models = [m for m in g.DEFAULT_MODELS if m not in g.EXCLUDE_MODELS]
base = os.environ.get("ARENA_BASE", "http://offline.invalid")
db = sqlite3.connect(f"file:{os.path.expanduser(args.db)}?mode=ro", uri=True)


def stamp(t):
    return (t or "").replace("T", " ")[:19]


code_of = {}
for sid, slug, logged, code in db.execute(
        "select a.submission_id, p.title_slug, a.logged_at, a.code from attempt a "
        "join problem p on p.id = a.problem_id"):
    code_of[(sid, slug, stamp(logged))] = code

rng = random.Random(args.seed)
jobs = []
for comp in args.competitions.split(","):
    sel = g.campaign_submissions(base, "", comp, models)
    cells = {}
    for (m, lang), (sub, per) in sel.items():
        if lang not in langs:
            continue
        fill = sub.get("_fill_from") or {}
        for slug, a in per.items():
            if not a.get("accepted"):
                continue
            sid = fill.get(slug, sub["id"])
            logged = stamp(a.get("submitted_at"))
            cells.setdefault((slug, lang), []).append(dict(
                key=f"{comp}|{lang}|{m}|{slug}", competition=comp, model=m, language=lang,
                title_slug=slug, submission_id=sid, logged_at=logged,
                cache_attempt_id=a.get("attempt_id"), code=code_of.get((sid, slug, logged))))
    # Order: problem by problem, JavaScript and TypeScript side by side, the LLMs
    # of a cell in a random order, so the solutions that duel are measured
    # minutes apart and no LLM is always first.
    for slug in sorted({s for s, _ in cells}):
        for lang in langs:
            group = cells.get((slug, lang), [])
            rng.shuffle(group)
            jobs += group
json.dump(jobs, open(args.out, "w"))
missing = [j for j in jobs if not j["code"]]
by = {}
for j in jobs:
    by[(j["competition"], j["language"])] = by.get((j["competition"], j["language"]), 0) + 1
print(f"wrote {len(jobs)} jobs to {args.out}: {by}")
print(f"{len(missing)} without code in the snapshot (fill_code.py fetches them), "
      f"submissions {sorted({j['submission_id'] for j in missing})}")
