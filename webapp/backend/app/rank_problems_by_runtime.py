"""Pick performance-DISCRIMINATIVE problems for the system.

Goal: avoid problems where every accepted solution lands at the judge's noise
floor (≈ 0-5 ms regardless of how clever the code is). Those problems can't
discriminate fast vs slow models — their runtime numbers are dominated by
LeetCode's per-submission timing wobble, not by algorithmic merit.

For every problem with an anchor submission_id, pull the real LeetCode
`submissionDistribution`, compute the histogram's signal shape (p10 / p50 /
p95 / dynamic range), drop the ones below the noise/spread thresholds, then
pick the top N per difficulty level by whichever signal you care about.

Anchor sources (in order):
  1. data/anchors.json       — {slug: {lang: submission_id}}
  2. The DB's Run table      — the latest accepted Run.submission_id_external
                                for the same (problem, language).
Problems with neither anchor are skipped; the script reports coverage.

Output:
  data/sampled_problems.json — top N per level (consumed by seed.py --reset)
  data/runtime_ranking.json  — every anchored problem with its signals,
                                so the choice is transparent and auditable.

Run:
  # default: rank by ABSOLUTE spread (p95 − p10) in ms — "how many ms of room
  # is there for a model to differentiate itself on this problem?"
  python -m app.rank_problems_by_runtime

  # or by RELATIVE spread (p95/p10) — "how many × faster is the top decile
  # vs the bottom?" — better when problem timescales differ wildly.
  python -m app.rank_problems_by_runtime --rank-by spread_ratio

  # tweak thresholds; defaults skip p50<10ms or (p95-p10)<10ms.
  python -m app.rank_problems_by_runtime \\
      --min-median-ms 15 --min-spread-ms 20 --per-level 33 --rate 1
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
from sqlmodel import Session, select

from . import leetcode_dist
from .db import engine
from .models import Attempt, Problem, Run, Submission

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
PARQUET = DATA_DIR / "problems.parquet"
SAMPLED_JSON = DATA_DIR / "sampled_problems.json"
RANKING_JSON = DATA_DIR / "runtime_ranking.json"
ANCHORS_JSON = DATA_DIR / "anchors.json"

LEVELS = ("Easy", "Medium", "Hard")


# ---------- histogram signal math (sourced from a real distribution) --------

def _percentile(buckets: list[tuple[float, float]], p: float) -> float:
    """Walk the CDF; return the bucket value where cumulative density first
    crosses `p` (percent, 0-100). Buckets need not be pre-sorted."""
    sorted_b = sorted(buckets, key=lambda x: x[0])
    cum = 0.0
    for v, pct in sorted_b:
        cum += pct
        if cum >= p:
            return v
    return sorted_b[-1][0] if sorted_b else 0.0


def signals(runtime_dist_json: str) -> dict:
    payload = json.loads(runtime_dist_json)
    buckets = [(float(k), float(pct)) for k, pct in payload["distribution"]]
    if not buckets:
        return {}
    p10 = _percentile(buckets, 10.0)
    p50 = _percentile(buckets, 50.0)
    p95 = _percentile(buckets, 95.0)
    return {
        "p10_ms": round(p10, 3),
        "p50_ms": round(p50, 3),
        "p95_ms": round(p95, 3),
        "spread_ms": round(p95 - p10, 3),                  # absolute room
        "spread_ratio": round(p95 / max(p10, 1.0), 3),     # relative room
        "n_buckets": len(buckets),
    }


# ---------- anchor resolution -----------------------------------------------

def _load_explicit_anchors() -> dict[str, dict[str, int]]:
    if not ANCHORS_JSON.exists():
        return {}
    try:
        return json.loads(ANCHORS_JSON.read_text())
    except json.JSONDecodeError:
        return {}


def _db_anchors(preferred_lang: str) -> dict[str, int]:
    """Latest accepted Run.submission_id_external per problem, preferring
    `preferred_lang` when multiple languages are available."""
    out: dict[str, int] = {}
    pref: dict[str, int] = {}
    with Session(engine) as s:
        rows = s.exec(
            select(Run)
            .where(Run.status_msg == "Accepted")
            .where(Run.submission_id_external.is_not(None))
            .order_by(Run.created_at.desc())
        ).all()
        for r in rows:
            a = s.get(Attempt, r.attempt_id)
            if not a:
                continue
            p = s.get(Problem, a.problem_id)
            sub = s.get(Submission, a.submission_id)
            if not p or not sub or sub.deleted_at is not None:
                continue
            try:
                sid = int(r.submission_id_external)
            except (TypeError, ValueError):
                continue
            if sub.language == preferred_lang and p.title_slug not in pref:
                pref[p.title_slug] = sid
            if p.title_slug not in out:
                out[p.title_slug] = sid
    # Preferred-lang wins; falls back to any-lang.
    return {**out, **pref}


def resolve_anchor(slug: str, explicit: dict[str, dict[str, int]],
                   db: dict[str, int], lang: str) -> int | None:
    per_lang = explicit.get(slug) or {}
    sid = per_lang.get(lang) or next(iter(per_lang.values()), None)
    if sid:
        try: return int(sid)
        except (TypeError, ValueError): pass
    return db.get(slug)


# ---------- ranking ---------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-level", type=int, default=33)
    ap.add_argument("--lang", default="python3",
                    help="preferred anchor language; falls back to any")
    ap.add_argument("--rank-by", default="spread_ratio",
                    choices=("spread_ratio", "spread_ms", "p50_ms", "p95_ms"),
                    help="signal to rank by once noise-floor problems are dropped. "
                         "spread_ratio (default) is test-count-invariant — fair when "
                         "comparing problems whose harnesses run different numbers "
                         "of tests; spread_ms is the absolute ms-of-room.")
    ap.add_argument("--min-median-ms", type=float, default=10.0,
                    help="drop problems whose p50 runtime is below this (default 10 ms)")
    ap.add_argument("--min-spread-ms", type=float, default=10.0,
                    help="drop problems whose p95-p10 gap is below this (default 10 ms)")
    ap.add_argument("--min-buckets", type=int, default=15,
                    help="drop problems whose histogram has fewer than this many "
                         "buckets (default 15) — thin histograms can sneak past "
                         "the spread thresholds by accident and don't have enough "
                         "submissions for the percentile estimates to be stable.")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="requests per second to LeetCode (default 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the ranking but don't write sampled_problems.json")
    ap.add_argument("--list-missing-anchors", action="store_true",
                    help="scan the parquet, print + write data/missing_anchors.txt "
                         "with every problem that has no anchor — exits without "
                         "calling LeetCode. Use this to see exactly how much of "
                         "the parquet is reachable today.")
    args = ap.parse_args()

    if not PARQUET.exists():
        raise SystemExit(f"{PARQUET} not found")
    df = pd.read_parquet(PARQUET).drop_duplicates("title_slug", keep="first")
    problems = [
        {
            "external_id": int(r["id"]),
            "title_slug": r["title_slug"],
            "title": r["title_slug"].replace("-", " ").title(),
            "level": r["level"],
            "tags": r["tag"] or "",
            "content": r["question_content"] or "",
            "hints": None if pd.isna(r.get("question_hints")) else r.get("question_hints"),
        }
        for _, r in df.iterrows() if r["level"] in LEVELS
    ]

    explicit = _load_explicit_anchors()
    db = _db_anchors(args.lang)
    anchored = [
        (p, sid) for p in problems
        if (sid := resolve_anchor(p["title_slug"], explicit, db, args.lang))
    ]
    print(f"parquet pool:       {len(problems)} problems "
          f"({sum(1 for p in problems if p['level']=='Easy')}E / "
          f"{sum(1 for p in problems if p['level']=='Medium')}M / "
          f"{sum(1 for p in problems if p['level']=='Hard')}H)")
    print(f"anchored:           {len(anchored)} "
          f"(explicit anchors.json: {sum(1 for p in problems if p['title_slug'] in explicit)}, "
          f"DB Run table: {sum(1 for p in problems if p['title_slug'] in db)})")
    print(f"missing anchors:    {len(problems) - len(anchored)} "
          f"(unreachable until you anchor them — see --list-missing-anchors)")

    if args.list_missing_anchors:
        missing = [p for p in problems
                   if not resolve_anchor(p["title_slug"], explicit, db, args.lang)]
        by = {lvl: sum(1 for p in missing if p["level"] == lvl) for lvl in LEVELS}
        out = DATA_DIR / "missing_anchors.txt"
        with out.open("w") as f:
            for p in missing:
                f.write(f"{p['title_slug']}\t{p['level']}\n")
        print(f"\nwrote {out} ({len(missing)} slugs, by level: {by})")
        print("To anchor: dispatch each slug once with ARENA_DISPATCH_ENABLED=1 "
              "(any accepted Run.submission_id_external becomes the anchor) "
              "or hand-curate data/anchors.json.")
        return

    if not anchored:
        raise SystemExit(
            "No anchors available. Populate data/anchors.json (slug→lang→sid) "
            "or run real-dispatch submissions so Run.submission_id_external "
            "fills in, then re-run."
        )

    client = leetcode_dist.build_client()
    delay = 1.0 / max(args.rate, 0.01)
    measured: list[dict] = []
    dropped_noise = 0
    failed = 0
    try:
        for i, (p, sid) in enumerate(anchored, 1):
            try:
                details = leetcode_dist.fetch_distribution(sid, client=client)
            except Exception:                                       # noqa: BLE001
                details = None
            if not details:
                failed += 1
                time.sleep(delay)
                continue
            sig = signals(details["runtimeDistribution"])
            if not sig:
                failed += 1; time.sleep(delay); continue
            if (sig["p50_ms"] < args.min_median_ms
                    or sig["spread_ms"] < args.min_spread_ms
                    or sig["n_buckets"] < args.min_buckets):
                dropped_noise += 1
                if i % 25 == 0 or i == len(anchored):
                    print(f"[{i}/{len(anchored)}] dropped {p['title_slug']:<40} "
                          f"p50={sig['p50_ms']} spread={sig['spread_ms']} (noise)")
                time.sleep(delay); continue
            p2 = {**p, **sig, "_anchor": sid, "_score": sig[args.rank_by]}
            measured.append(p2)
            if i % 25 == 0 or i == len(anchored):
                print(f"[{i}/{len(anchored)}] kept    {p['title_slug']:<40} "
                      f"p50={sig['p50_ms']} p95={sig['p95_ms']} spread={sig['spread_ms']}")
            time.sleep(delay)
    finally:
        client.close()

    print(f"\nmeasured: {len(measured)}  dropped(noise): {dropped_noise}  "
          f"failed(fetch): {failed}")

    by_level: dict[str, list[dict]] = {lvl: [] for lvl in LEVELS}
    for p in measured:
        by_level[p["level"]].append(p)

    picked: list[dict] = []
    print(f"\n=== top {args.per_level} per level by {args.rank_by} ===")
    for lvl in LEVELS:
        lvl_rows = sorted(by_level[lvl], key=lambda x: -x["_score"])
        top = lvl_rows[:args.per_level]
        print(f"\n{lvl}  ({len(lvl_rows)} discriminative, taking {len(top)})")
        for i, r in enumerate(top[:6], 1):
            print(f"  {i}. {r['title_slug']:<42} "
                  f"p10={r['p10_ms']:>6}  p50={r['p50_ms']:>6}  p95={r['p95_ms']:>7}  "
                  f"spread={r['spread_ms']:>6}  ratio={r['spread_ratio']:>5}×")
        if len(top) > 6:
            print(f"  … +{len(top) - 6} more")
        if len(top) < args.per_level:
            print(f"  WARNING: only {len(top)} discriminative problems in {lvl} — "
                  f"loosen --min-* thresholds or widen anchor coverage")
        picked += top

    # Transparency dump — every anchored+measured problem with its signals.
    RANKING_JSON.write_text(json.dumps([
        {
            "title_slug": r["title_slug"], "level": r["level"],
            "anchor_submission_id": r["_anchor"],
            "p10_ms": r["p10_ms"], "p50_ms": r["p50_ms"], "p95_ms": r["p95_ms"],
            "spread_ms": r["spread_ms"], "spread_ratio": r["spread_ratio"],
            "n_buckets": r["n_buckets"],
        }
        for lvl in LEVELS for r in sorted(by_level[lvl], key=lambda x: -x["_score"])
    ], indent=2))
    print(f"\nfull signals → {RANKING_JSON}")

    if args.dry_run:
        print("dry-run: sampled_problems.json NOT written")
        return

    clean_keys = ("external_id", "title_slug", "title", "level",
                  "tags", "content", "hints")
    clean = [{k: r[k] for k in clean_keys} for r in picked]
    SAMPLED_JSON.write_text(json.dumps(clean, indent=2, ensure_ascii=False))
    by = {lvl: sum(1 for p in clean if p["level"] == lvl) for lvl in LEVELS}
    print(f"wrote {SAMPLED_JSON} ({len(clean)} problems, by level: {by})")
    print("next: python -m app.seed --reset    # re-seeds the system")


if __name__ == "__main__":
    main()
