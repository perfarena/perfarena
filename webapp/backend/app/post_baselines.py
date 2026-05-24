"""Load baseline JSON files into the running PerfArena via the admin API.

Reads either the combined `data/baselines.json` (default) or every individual
`data/baselines/*.json`, strips the puller's `_meta`/`anchor_submission_id`/
`fetched_at` decorations, batches the rows, and POSTs to
`/api/admin/baselines` with the admin API key.

Run:
    export ARENA_API_KEY=arena_xxxxxxxx           # an admin key
    python -m app.post_baselines                  # combined JSON
    python -m app.post_baselines --per-pair       # all data/baselines/*.json
    python -m app.post_baselines --base-url http://localhost:8000 --batch 100
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
COMBINED_JSON = DATA_DIR / "baselines.json"
PER_PAIR_DIR = DATA_DIR / "baselines"

ROW_KEYS = (
    "title_slug", "language",
    "runtime_ms_avg", "runtime_ms_expert",
    "memory_mb_avg", "memory_mb_expert",
    "source",
)


def _strip(row: dict) -> dict:
    out = {k: row[k] for k in ROW_KEYS if k in row}
    out.setdefault("source", "leetcode-distribution")
    return out


def _load_rows(per_pair: bool) -> list[dict]:
    if per_pair:
        files = sorted(PER_PAIR_DIR.glob("*.json"))
        if not files:
            raise SystemExit(f"no per-pair files in {PER_PAIR_DIR}/")
        return [json.loads(f.read_text()) for f in files]
    if not COMBINED_JSON.exists():
        raise SystemExit(f"{COMBINED_JSON} not found — run `python -m app.pull_baselines` first")
    return json.loads(COMBINED_JSON.read_text())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("ARENA_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.environ.get("ARENA_API_KEY"))
    ap.add_argument("--per-pair", action="store_true",
                    help="load every data/baselines/*.json instead of the combined file")
    ap.add_argument("--batch", type=int, default=200,
                    help="rows per POST (default 200)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be sent but don't POST")
    args = ap.parse_args()

    if not args.api_key:
        raise SystemExit("set --api-key or ARENA_API_KEY (an admin key)")

    rows = [_strip(r) for r in _load_rows(args.per_pair)]
    print(f"loaded {len(rows)} baseline rows")
    if args.dry_run:
        print(json.dumps(rows[:3], indent=2))
        return

    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.api_key}"}
    upserted_total = 0
    skipped_total: list[str] = []
    with httpx.Client(timeout=60) as client:
        for i in range(0, len(rows), args.batch):
            chunk = rows[i:i + args.batch]
            r = client.post(
                f"{base}/api/admin/baselines",
                headers=headers, json={"baselines": chunk},
            )
            if r.status_code != 200:
                print(f"  batch {i}-{i+len(chunk)}: HTTP {r.status_code} {r.text[:200]}",
                      file=sys.stderr)
                raise SystemExit(2)
            body = r.json()
            upserted_total += body["upserted"]
            skipped_total.extend(body["skipped"])
            print(f"  batch {i:>5}-{i+len(chunk):<5}: upserted {body['upserted']}/{len(chunk)} "
                  f"(skipped: {len(body['skipped'])})")

    print(f"\ndone — upserted {upserted_total} rows")
    if skipped_total:
        print(f"  skipped {len(skipped_total)} unknown pairs (first 5): {skipped_total[:5]}")


if __name__ == "__main__":
    main()
