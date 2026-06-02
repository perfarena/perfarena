"""Recompute `mode_share` (max-bucket %) for every entry in runtime_ranking.

WHY THIS EXISTS
---------------
The original noise filter checks `p50 ≥ 10ms`, `spread_ms ≥ 10ms`, and
`n_buckets ≥ 15`. Those pass for a problem like `single-number` whose
histogram has p10=26 / p50=26 / p95=4205 — looks discriminative on paper.

But LeetCode's histogram aggregates everything `< 26 ms` into a single
floor bucket. For `single-number`, **90 % of accepted submissions sit in
the 26 ms bucket**. A submitter whose code runs in 1 ms (genuinely faster)
gets reported as 26 ms — indistinguishable from the bulk of the
population. The fast half of the histogram is collapsed; only the slow
tail discriminates anything.

The honest filter: drop any problem where the dominant bucket holds more
than ~50 % of submissions. That catches the "everyone clusters at the
resolution floor" case.

WHAT THIS DOES
--------------
1. Re-queries LeetCode's `submissionDistribution` for each anchor in
   `runtime_ranking.json` (no new anchors needed — the same ones are
   still valid).
2. Computes `mode_share` = max bucket percentage.
3. Writes the augmented file in place — every existing field preserved,
   plus the new `mode_share` column. `fix_parquet.py`'s rebucket step
   then filters on it.

Run:
    .venv/bin/python -m app.add_mode_share              # ~7 min at 2 req/s
    .venv/bin/python -m app.add_mode_share --rate 4     # ~3.5 min
    .venv/bin/python -m app.add_mode_share --skip-existing   # resume an interrupted run
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import leetcode_dist

REPO_ROOT = Path(__file__).parent.parent.parent.parent
RUNTIME_RANKING_JSON = REPO_ROOT / "data" / "runtime_ranking.json"


_Q = """
query q($sid: Int!) {
  submissionDetails(submissionId: $sid) {
    runtimeDistribution
  }
}
"""


def _mode_share(distribution: list) -> float:
    """Return the max bucket percentage; 0 for an empty distribution."""
    if not distribution:
        return 0.0
    return max(float(p) for _, p in distribution)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=2.0)
    ap.add_argument("--skip-existing", action="store_true",
                    help="don't re-query rows that already have a mode_share field")
    args = ap.parse_args()

    if not RUNTIME_RANKING_JSON.exists():
        raise SystemExit(f"{RUNTIME_RANKING_JSON} not found")
    rows = json.loads(RUNTIME_RANKING_JSON.read_text())
    todo = [(i, r) for i, r in enumerate(rows)
            if not (args.skip_existing and "mode_share" in r)]
    print(f"runtime_ranking rows: {len(rows)}  to measure: {len(todo)}")

    client = leetcode_dist.build_client()
    delay = 1.0 / max(args.rate, 0.01)
    measured = failed = 0
    try:
        for n, (i, r) in enumerate(todo, 1):
            sid = r.get("anchor_submission_id")
            if not sid:
                failed += 1
                continue
            try:
                resp = client.post("/graphql", json={
                    "query": _Q, "variables": {"sid": int(sid)},
                })
            except Exception:                                    # noqa: BLE001
                failed += 1
                time.sleep(delay)
                continue
            if resp.status_code != 200:
                failed += 1
                time.sleep(delay)
                continue
            try:
                data = resp.json()["data"]["submissionDetails"]
                rt = json.loads(data["runtimeDistribution"])
                ms = _mode_share(rt.get("distribution") or [])
            except (KeyError, json.JSONDecodeError, TypeError):
                failed += 1
                time.sleep(delay)
                continue
            rows[i]["mode_share"] = round(ms, 3)
            measured += 1
            if n % 50 == 0 or n == len(todo):
                # Atomic incremental save — interrupted runs keep their progress.
                tmp = RUNTIME_RANKING_JSON.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(rows, indent=2))
                tmp.replace(RUNTIME_RANKING_JSON)
                print(f"[{n}/{len(todo)}] measured={measured} failed={failed} "
                      f"(last: {r['title_slug']} mode_share={ms:.1f}%)",
                      flush=True)
            time.sleep(delay)
    finally:
        client.close()
    # final save
    tmp = RUNTIME_RANKING_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(RUNTIME_RANKING_JSON)
    print(f"\ndone — measured={measured} failed={failed}")


if __name__ == "__main__":
    main()
