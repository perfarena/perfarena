"""One-shot: seed the "LeetCode 99 Hard Problems" competition.

Selection: the 99 Hard problems (from `data/runtime_ranking.json`) with the
LONGEST median Python runtime (`p50_ms`, descending), skipping any whose
content isn't available in `data/problems.parquet` so every selected
problem can be seeded fully offline. Premium-only problems are excluded
upstream by `app.rank_problems_by_runtime` already, so this script
doesn't need to re-check.

Why "longer runtime" matters: a problem whose median accepted submission
takes 1 s discriminates models more meaningfully than one that finishes
in 5 ms (LeetCode's per-run timing noise dominates the latter). Selecting
the slow tail gives a benchmark set where runtime % differences are
actually signal.

Idempotent: re-running on a DB that already has the competition is a
no-op for unchanged rows; new problems get linked, dropped ones stay
linked (the script doesn't unlink). Pair with `app.fetch_code_snippets
--sampled-only` after this to populate per-language starter code.

CLI:
    python -m app.seed_leetcode_hard_competition
    python -m app.seed_leetcode_hard_competition --dry-run
    python -m app.seed_leetcode_hard_competition --slug leetcode-99-hard-perf \\
        --name "LeetCode 99 Hard Problems"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sqlmodel import Session, select

from .db import engine, init_db
from .models import Competition, CompetitionProblem, Problem


# Paths default to the source-tree layout; override via flag for ops who
# run this against a different ARENA_DB. Both files ship in the data/
# directory at the repo root.
DEFAULT_RANKING = Path(__file__).parent.parent.parent.parent / "data" / "runtime_ranking.json"
DEFAULT_PARQUET = Path(__file__).parent.parent.parent.parent / "data" / "problems.parquet"


def _select_top_99_hard(ranking_path: Path, parquet_path: Path) -> tuple[list[dict], list[dict]]:
    """Return (selected, skipped) — both lists of ranking-row dicts.

    `selected` is the 99 hardest-by-p50_ms problems whose content lives
    in the parquet (so we can seed them offline). `skipped` is everything
    higher-ranked that didn't have content available — reported so the
    operator can decide whether to fetch them from LeetCode later.
    """
    if not ranking_path.exists():
        raise SystemExit(f"ranking file not found: {ranking_path}")
    if not parquet_path.exists():
        raise SystemExit(f"parquet not found: {parquet_path}")
    rows = json.loads(ranking_path.read_text())
    hard = [r for r in rows if r.get("level") == "Hard"]
    hard.sort(key=lambda r: r.get("p50_ms") or 0, reverse=True)
    df = pd.read_parquet(parquet_path)
    parquet_slugs = set(df["title_slug"])
    selected: list[dict] = []
    skipped: list[dict] = []
    for r in hard:
        if r["title_slug"] in parquet_slugs:
            if len(selected) < 99:
                selected.append(r)
        else:
            if len(selected) < 99:
                skipped.append(r)
    return selected, skipped


def _ensure_problem_seeded(
    session: Session, slug: str, df: pd.DataFrame,
) -> Problem | None:
    """Return the Problem row for `slug`, seeding from the parquet if
    it's not in the DB yet. Returns None when the slug isn't in the
    parquet either (the caller has already filtered, so this is just
    defence-in-depth)."""
    existing = session.exec(
        select(Problem).where(Problem.title_slug == slug)
    ).first()
    if existing is not None:
        return existing
    matched = df[df["title_slug"] == slug]
    if matched.empty:
        return None
    row = matched.iloc[0]
    # Title isn't in the parquet — derive a Title-Case display name from
    # the slug. The fetch_code_snippets pass overrides this with the
    # real title from LeetCode if you run it later.
    title = " ".join(w.capitalize() for w in slug.split("-"))
    problem = Problem(
        external_id=int(row["id"]),
        title_slug=slug,
        title=title,
        level=str(row["level"]),
        tags=str(row.get("tag") or ""),
        content=str(row.get("question_content") or ""),
        hints=str(row.get("question_hints")) if row.get("question_hints") else None,
        code_snippets=None,    # populated later by `app.fetch_code_snippets`
    )
    session.add(problem); session.commit(); session.refresh(problem)
    return problem


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Seed the LeetCode 99 Hard Problems competition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--ranking", type=Path, default=DEFAULT_RANKING)
    ap.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    ap.add_argument("--slug", default="leetcode-99-hard-perf",
                    help="competition slug (default: leetcode-99-hard-perf)")
    ap.add_argument("--name", default="LeetCode 99 Hard Problems",
                    help="display name")
    ap.add_argument("--dry-run", action="store_true",
                    help="report selections without writing anything")
    args = ap.parse_args()

    init_db()

    selected, skipped = _select_top_99_hard(args.ranking, args.parquet)
    if len(selected) < 99:
        print(f"WARNING: only {len(selected)} Hard problems available in the "
              f"parquet — wanted 99", file=sys.stderr)

    print(f"competition: {args.slug!r}  ({args.name!r})")
    print(f"selected: {len(selected)} Hard problems by descending p50_ms")
    print(f"  fastest of the selected:  p50={selected[-1]['p50_ms']:>6} ms  {selected[-1]['title_slug']}")
    print(f"  slowest of the selected:  p50={selected[0]['p50_ms']:>6} ms  {selected[0]['title_slug']}")
    if skipped:
        print(f"skipped (no parquet content; would have been higher-ranked): {len(skipped)}")
        for r in skipped:
            print(f"  p50={r['p50_ms']:>6}ms   {r['title_slug']}")

    if args.dry_run:
        print("\n(dry-run — no DB writes)")
        return

    df = pd.read_parquet(args.parquet)
    with Session(engine) as session:
        # Find/create the competition.
        comp = session.exec(
            select(Competition).where(Competition.slug == args.slug)
        ).first()
        if comp is None:
            comp = Competition(
                slug=args.slug, name=args.name,
                description=(
                    "99 Hard LeetCode problems with the longest median "
                    "accepted Python runtime, selected from "
                    "data/runtime_ranking.json. Picked for runtime-"
                    "discrimination — the median p50_ms ranges from "
                    f"~{selected[-1]['p50_ms']:.0f} ms to "
                    f"~{selected[0]['p50_ms']:.0f} ms, where small "
                    "differences in algorithmic quality are big enough "
                    "to read above LeetCode's per-run timing noise."
                ),
                kind="leetcode", status="active",
            )
            session.add(comp); session.commit(); session.refresh(comp)
            print(f"\ncreated competition '{comp.slug}' (id={comp.id})")
        else:
            print(f"\nfound existing competition '{comp.slug}' (id={comp.id})")

        # Ensure each problem is in the DB and linked.
        existing_pids = {
            cp.problem_id for cp in session.exec(
                select(CompetitionProblem).where(
                    CompetitionProblem.competition_id == comp.id
                )
            ).all()
        }
        n_new = n_linked = 0
        for r in selected:
            slug = r["title_slug"]
            problem = _ensure_problem_seeded(session, slug, df)
            if problem is None:
                print(f"  WARN: {slug} not in parquet — skipped",
                      file=sys.stderr)
                continue
            if problem.id not in existing_pids:
                session.add(CompetitionProblem(
                    competition_id=comp.id, problem_id=problem.id,
                ))
                n_linked += 1
        if n_linked:
            session.commit()
        # Re-count after writes.
        n_linked_total = len(session.exec(
            select(CompetitionProblem).where(
                CompetitionProblem.competition_id == comp.id
            )
        ).all())
        print(f"  linked {n_linked} new problems to '{comp.slug}'")
        print(f"  total problems in '{comp.slug}': {n_linked_total}")
        print(
            "\nNEXT STEP: run `python -m app.fetch_code_snippets --sampled-only` "
            "to populate per-language starter code for the new problems."
        )


if __name__ == "__main__":
    main()
