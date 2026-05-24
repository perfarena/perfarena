"""One-time sampler: 33 random problems per difficulty level.

Reads `data/problems.parquet`, samples deterministically with a fixed RNG seed,
and writes `data/sampled_problems.json`. From then on, `seed.py` and
`pull_baselines.py` both consume that JSON, so the same 99 problems are the
only ones in the system and in the baseline pipeline.

Run as:
    python -m app.sample            # writes data/sampled_problems.json
    python -m app.sample --seed 7   # different sample
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import pandas as pd

PARQUET = Path(__file__).parent.parent.parent.parent / "data" / "problems.parquet"
OUTPUT = Path(__file__).parent.parent.parent.parent / "data" / "sampled_problems.json"

LEVELS = ("Easy", "Medium", "Hard")
PER_LEVEL = 33


def sample(seed: int = 1337) -> list[dict]:
    df = pd.read_parquet(PARQUET).drop_duplicates("title_slug", keep="first")
    rng = random.Random(seed)
    picked: list[dict] = []
    for lvl in LEVELS:
        pool = df[df["level"] == lvl]
        if len(pool) < PER_LEVEL:
            raise SystemExit(f"only {len(pool)} {lvl} problems in parquet; need {PER_LEVEL}")
        idxs = rng.sample(range(len(pool)), PER_LEVEL)
        for i in idxs:
            r = pool.iloc[i]
            picked.append({
                "external_id": int(r["id"]),
                "title_slug": r["title_slug"],
                "title": r["title_slug"].replace("-", " ").title(),
                "level": r["level"],
                "tags": r["tag"] or "",
                "content": r["question_content"] or "",
                "hints": None if pd.isna(r.get("question_hints")) else r["question_hints"],
            })
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    picked = sample(args.seed)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Strip the bulky content/tags from the index summary — the JSON itself is
    # the source of truth for downstream scripts.
    OUTPUT.write_text(json.dumps(picked, indent=2, ensure_ascii=False))
    print(f"wrote {OUTPUT} ({len(picked)} problems, seed={args.seed})")
    by_level = {lvl: sum(1 for p in picked if p["level"] == lvl) for lvl in LEVELS}
    print(f"  by level: {by_level}")


if __name__ == "__main__":
    main()
