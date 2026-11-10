"""Sample the top-N problems per difficulty by solution count and dump each
problem's solutions into <out>/<slug>/<lang>/solution-<nr>.<ext>.

Usage:
    python sample_solutions.py [--top 30] [--out sampled_solutions]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


LANG_EXT = {
    "python3": "py",
    "java": "java",
    "cpp": "cpp",
    "javascript": "js",
    "typescript": "ts",
    "csharp": "cs",
    "c": "c",
    "golang": "go",
    "rust": "rs",
    "kotlin": "kt",
    "ruby": "rb",
    "swift": "swift",
    "scala": "scala",
    "php": "php",
}


def pick_top_problems(solutions: pd.DataFrame, top_n: int) -> pd.DataFrame:
    counts = (
        solutions.groupby(["level", "title_slug"], as_index=False)
        .size()
        .rename(columns={"size": "n_solutions"})
        .sort_values(["level", "n_solutions", "title_slug"], ascending=[True, False, True])
    )
    return counts.groupby("level", as_index=False, group_keys=False).head(top_n)


def write_solutions(solutions: pd.DataFrame, picked: pd.DataFrame, out_dir: Path) -> dict:
    selected = solutions.merge(picked[["title_slug"]], on="title_slug", how="inner")
    selected = selected.sort_values(["title_slug", "lang", "source_row_id"], kind="stable")

    written = 0
    skipped_langs: set[str] = set()
    for (slug, lang), group in selected.groupby(["title_slug", "lang"], sort=False):
        ext = LANG_EXT.get(lang)
        if ext is None:
            skipped_langs.add(lang)
            continue
        lang_dir = out_dir / slug / lang
        lang_dir.mkdir(parents=True, exist_ok=True)
        for i, code in enumerate(group["code"].tolist(), start=1):
            (lang_dir / f"solution-{i}.{ext}").write_text(code, encoding="utf-8")
            written += 1
    return {"written": written, "skipped_langs": skipped_langs}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solutions", default="data/solutions.parquet")
    ap.add_argument("--top", type=int, default=30, help="problems per difficulty level")
    ap.add_argument("--out", default="sampled_solutions")
    args = ap.parse_args()

    solutions = pd.read_parquet(args.solutions)
    picked = pick_top_problems(solutions, args.top)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    picked.to_csv(out_dir / "_selected_problems.csv", index=False)

    stats = write_solutions(solutions, picked, out_dir)

    by_level = picked["level"].value_counts().to_dict()
    print(f"Selected problems by level: {by_level}")
    print(f"Wrote {stats['written']} solution files to {out_dir}/")
    if stats["skipped_langs"]:
        print(f"Skipped unknown languages: {sorted(stats['skipped_langs'])}")


if __name__ == "__main__":
    main()
