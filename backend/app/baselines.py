"""LeetCode-population baseline reference performance per (problem, language).

Two tiers per pair: `avg` (population mean) and `expert` (top decile-ish).
The cohort is everyone who's submitted on LeetCode for that problem/language —
LeetCode doesn't separate human-authored from AI-assisted submissions, so
think of these as "the LeetCode submission distribution" rather than "humans".

For now we synthesize plausible distributions from the problem's difficulty
level and a per-language efficiency multiplier — the table is the schema we'd
populate from a real public submissions corpus.

Internal note: the corresponding DB columns and SQLModel attributes still
use `time_human_avg` / `time_human_expert` / `mem_human_avg` /
`mem_human_expert` — renaming those would force a SQLite migration with
no semantic gain. The user-visible labels in the UI all say "LeetCode".

Run as: python -m app.baselines [--reset]
"""
from __future__ import annotations

import argparse
import random

from sqlmodel import Session, delete, select

from .db import engine, init_db
from .models import Baseline, Problem


# Per-language multiplier for runtime relative to a "neutral" baseline (~1.0 for python3).
# Memory uses a flatter spread because LeetCode reports include the runtime baggage.
LANG_RUNTIME_MULT = {
    "c": 0.08, "cpp": 0.10, "rust": 0.12, "golang": 0.30,
    "java": 0.45, "csharp": 0.50, "scala": 0.55, "kotlin": 0.55,
    "swift": 0.40,
    "javascript": 0.80, "typescript": 0.85,
    "python3": 1.0, "ruby": 1.4, "php": 1.6,
}
LANG_MEMORY_MULT = {
    "c": 0.40, "cpp": 0.45, "rust": 0.55, "golang": 0.65,
    "java": 1.10, "csharp": 1.00, "scala": 1.20, "kotlin": 1.10,
    "swift": 0.80,
    "javascript": 0.95, "typescript": 0.95,
    "python3": 1.0, "ruby": 1.05, "php": 1.0,
}
LEVEL_BASE_RUNTIME = {"Easy": 60.0, "Medium": 180.0, "Hard": 420.0}   # ms, python3 scale
LEVEL_BASE_MEMORY = {"Easy": 16.5, "Medium": 17.5, "Hard": 18.5}      # MB, python3 scale


def _baseline_for(level: str, language: str, seed: int) -> tuple[float, float, float, float]:
    rng = random.Random(seed)
    rt_mult = LANG_RUNTIME_MULT.get(language, 1.0)
    mem_mult = LANG_MEMORY_MULT.get(language, 1.0)

    rt_avg = LEVEL_BASE_RUNTIME[level] * rt_mult * rng.uniform(0.8, 1.3)
    rt_expert = rt_avg * rng.uniform(0.30, 0.55)  # ~2-3x faster than avg

    mem_avg = LEVEL_BASE_MEMORY[level] * mem_mult * rng.uniform(0.9, 1.15)
    mem_expert = mem_avg * rng.uniform(0.80, 0.92)

    return rt_avg, rt_expert, mem_avg, mem_expert


def seed_baselines(session: Session, languages: list[str], reset: bool = False) -> int:
    if reset:
        session.exec(delete(Baseline))
        session.commit()
    problems = session.exec(select(Problem)).all()
    inserted = 0
    for p in problems:
        for lang in languages:
            existing = session.exec(
                select(Baseline).where(
                    Baseline.problem_id == p.id, Baseline.language == lang,
                )
            ).first()
            if existing:
                continue
            seed = hash((p.id, lang)) & 0xFFFFFFFF
            rt_avg, rt_expert, mem_avg, mem_expert = _baseline_for(p.level, lang, seed)
            session.add(Baseline(
                problem_id=p.id, language=lang,
                runtime_ms_avg=round(rt_avg, 2),
                runtime_ms_expert=round(rt_expert, 2),
                memory_mb_avg=round(mem_avg, 3),
                memory_mb_expert=round(mem_expert, 3),
                source="synthetic",
            ))
            inserted += 1
        if inserted % 5000 == 0 and inserted:
            session.commit()
    session.commit()
    return inserted


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="delete existing baselines before regenerating")
    ap.add_argument("--languages", nargs="+", default=list(LANG_RUNTIME_MULT.keys()))
    args = ap.parse_args()

    init_db()
    with Session(engine) as s:
        n = seed_baselines(s, args.languages, reset=args.reset)
        print(f"baselines inserted: {n} (problems × {len(args.languages)} langs)")


if __name__ == "__main__":
    main()
