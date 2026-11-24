#!/usr/bin/env python3
"""Run-to-run variation of the latency and memory that the LeetCode judge
reports (Section 5).

    python3 analysis/judge_variation.py

Reads the cached submissions of leetcode-99-perf whose performance oracle is
the LeetCode judge, and keeps every program that the judge accepted in all of
its 5 submissions with both a latency and a memory reading. For each program
and metric, the statistic is the ratio of the largest of the 5 readings to the
smallest (max/min, 1.00 = identical readings). The script reports the median,
mean, and maximum of this ratio over the programs.

Output: figs/data/leetcode_judge_variation.json
"""
import json, os, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "out"
CACHE = Path(os.environ.get("PERFARENA_CACHE",
                            Path.home() / ".cache" / "perfarena-paper" / "snapshot-20261011-2237"))
COMP, JUDGE, SUBMISSIONS = "leetcode-99-perf", "leetcode", 5


def items(path):
    d = json.loads(path.read_text())
    return d if isinstance(d, list) else d.get("items") or d.get("attempts") or d.get("submissions") or []


def main():
    subs = [s for s in items(CACHE / "submissions.json")
            if s.get("competition_slug") == COMP and s.get("harness_slug") == JUDGE]
    programs = []
    for s in subs:
        path = CACHE / f"attempts_{s['id']}.json"
        if not path.exists():
            continue
        for a in items(path):
            runs = [r for r in a.get("runs") or [] if r.get("status_msg") == "Accepted"
                    and (r.get("runtime_ms") or 0) > 0 and (r.get("memory_mb") or 0) > 0]
            if len(runs) == SUBMISSIONS:
                programs.append(runs)
    out = {"competition": COMP, "oracle": JUDGE, "submissions_per_program": SUBMISSIONS,
           "statistic": "max/min of the readings of one program",
           "n_programs": len(programs), "n_submissions": len(subs)}
    for metric, key in (("latency", "runtime_ms"), ("memory", "memory_mb")):
        ratios = [max(r[key] for r in p) / min(r[key] for r in p) for p in programs]
        out[metric] = {"median": statistics.median(ratios), "mean": statistics.mean(ratios),
                       "max": max(ratios)}
    dst = ROOT / "figs" / "data" / "leetcode_judge_variation.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
