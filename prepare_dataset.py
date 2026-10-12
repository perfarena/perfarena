"""Split the raw HF parquet into structures the bot can use.

Outputs (under ./data):
  problems.parquet   one row per LeetCode problem (deduped on title_slug).
  solutions.parquet  (slug, lang, code, source_row_id) — one row per code
                     block extracted from `content`, filtered to LeetCode-
                     submittable shape (uses `class Solution`).

Usage:
    python prepare_dataset.py [--src train-00000-of-00001.parquet] [--out data]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd


LANG_MAP = {
    "python": "python3", "py": "python3", "python3": "python3",
    "java": "java",
    "cpp": "cpp", "c++": "cpp",
    "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "c": "c",
    "csharp": "csharp", "cs": "csharp",
    "go": "golang", "golang": "golang",
    "rust": "rust", "rs": "rust",
    "kotlin": "kotlin", "kt": "kotlin",
    "ruby": "ruby", "rb": "ruby",
    "swift": "swift",
    "scala": "scala",
    "php": "php",
}

FENCE = re.compile(r"```([a-zA-Z0-9+#_-]*)\s*\n(.*?)```", re.DOTALL)


def extract_blocks(text: str) -> list[tuple[str, str]]:
    if not isinstance(text, str):
        return []
    out = []
    for tag, body in FENCE.findall(text):
        lang = LANG_MAP.get(tag.lower())
        body = body.strip()
        if lang and body:
            out.append((lang, body))
    return out


def looks_leetcode_shaped(lang: str, code: str) -> bool:
    """Heuristic: does this look like a LeetCode submission, not a free-standing script?"""
    if lang == "python3":
        return "class Solution" in code
    if lang in ("java", "cpp", "csharp"):
        return "class Solution" in code
    if lang == "javascript" or lang == "typescript":
        # LeetCode JS uses top-level functions like `var twoSum = function(...) {...}`
        return ("var " in code and "function" in code) or "= function" in code
    if lang == "golang":
        return "func " in code
    if lang == "rust":
        return "impl Solution" in code
    return True  # be permissive for languages we don't know well


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="train-00000-of-00001.parquet")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(exist_ok=True)

    df = pq.read_table(args.src).to_pandas()
    df = df.reset_index(drop=False).rename(columns={"index": "source_row_id"})
    print(f"loaded {len(df):,} raw rows")

    # ---- problems.parquet: one row per slug -------------------------------
    has_slug = df.dropna(subset=["title_slug"])
    problems = (
        has_slug.sort_values("source_row_id")
        .drop_duplicates("title_slug", keep="first")
        [["id", "title_slug", "tag", "level", "question_content", "question_hints"]]
        .reset_index(drop=True)
    )
    pq.write_table(pa.Table.from_pandas(problems, preserve_index=False), out / "problems.parquet")
    print(f"wrote {out/'problems.parquet'}: {len(problems):,} problems")

    # ---- solutions.parquet: one row per extracted code block --------------
    rows = []
    for _, r in has_slug.iterrows():
        for lang, code in extract_blocks(r["content"]):
            rows.append({
                "source_row_id": r["source_row_id"],
                "title_slug": r["title_slug"],
                "id": r["id"],
                "level": r["level"],
                "lang": lang,
                "code": code,
                "leetcode_shaped": looks_leetcode_shaped(lang, code),
            })
    sols = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(sols, preserve_index=False), out / "solutions.parquet")

    print(f"wrote {out/'solutions.parquet'}: {len(sols):,} extracted blocks")
    print(f"  leetcode-shaped:     {int(sols['leetcode_shaped'].sum()):,}")
    print("  by language (shaped):")
    print(sols[sols["leetcode_shaped"]]["lang"].value_counts().to_string())


if __name__ == "__main__":
    main()
