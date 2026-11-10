"""Replace 6 excluded LeetCode problems in the arena's competitions with
picks from LeetCodeDataset-v0.1.0-train.jsonl.

Excluded (contamination / test-case gap):
  Medium: design-a-stack-with-increment-operation, random-pick-index
  Hard:   finding-mk-average, maximize-score-after-n-operations,
          remove-invalid-parentheses, stream-of-characters

Selection criterion (matches the original 99):
  * Same difficulty as the excluded slug being replaced.
  * Runtime-distribution thresholds: p50_ms >= 10 AND spread_ms >= 10 AND
    n_buckets >= 20 (matches `rank_problems_by_runtime.py` defaults).
  * 2 Hards by p50_ms desc → shared between lc99-perf AND lc99-hard-perf
    (matches `seed_leetcode_hard_competition.py`).
  * 2 Hards by spread_ms desc (non-overlapping with the shared 2) →
    lc99-perf-only.
  * 2 Mediums by spread_ms desc → lc99-perf-only.

This variant is OFFLINE: candidates are drawn from the intersection of the
dataset and the existing `data/runtime_ranking.json` — no LC submissions
needed. Statement + code_snippets come from `data/problems.parquet` when
available and fall back to a single `question` GraphQL per missing pick
(read-only; not subject to the write-side 429s).

Pipeline:
  1. Load train dataset + runtime_ranking.json + problems.parquet.
  2. Filter candidates: in dataset AND in ranking AND passes thresholds AND
     NOT already in a target competition.
  3. Pick 6 per the criterion above.
  4. Resolve statement + tags + code_snippets for each pick (parquet →
     LC question fallback).
  5. Seed Problem rows.
  6. Update CompetitionProblem membership on lc99-perf and lc99-hard-perf.
  7. Hard-delete Attempts + Runs on the 6 excluded slugs.
  8. Append picked slugs' signals to `data/runtime_ranking.json` audit (no-op
     since they're already there — but we re-tag with a `source` marker).

Safety knobs:
  --dry-run       Print the plan; skip DB writes and any LC calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sqlmodel import Session, select

from .db import engine
from .models import (
    Attempt, Competition, CompetitionProblem, Problem, Run,
)


REPO_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
RANKING_JSON = DATA_DIR / "runtime_ranking.json"
PROBLEMS_PARQUET = DATA_DIR / "problems.parquet"

EXCLUDED_SLUGS = [
    ("design-a-stack-with-increment-operation", "Medium"),
    ("random-pick-index", "Medium"),
    ("finding-mk-average", "Hard"),
    ("maximize-score-after-n-operations", "Hard"),
    ("remove-invalid-parentheses", "Hard"),
    ("stream-of-characters", "Hard"),
]
EXCLUDED_SLUG_LIST = [s for s, _ in EXCLUDED_SLUGS]

TARGET_COMPETITIONS = ("leetcode-99-perf", "leetcode-99-hard-perf")

# Same thresholds as rank_problems_by_runtime.py defaults.
MIN_MEDIAN_MS = 10.0
MIN_SPREAD_MS = 10.0
MIN_BUCKETS = 20

N_SHARED_HARDS = 2      # go into both lc99-perf AND lc99-hard-perf
N_LCPERF_EXTRA_HARDS = 2
N_LCPERF_MEDIUMS = 2


# ---------- input loaders ----------------------------------------------------

def load_dataset_slugs(path: Path) -> set[str]:
    slugs: set[str] = set()
    with path.open() as f:
        for line in f:
            slugs.add(json.loads(line)["task_id"])
    return slugs


def load_ranking() -> dict[str, dict]:
    return {r["title_slug"]: r
            for r in json.loads(RANKING_JSON.read_text())}


def load_parquet_index() -> dict[str, dict]:
    """Return {slug: {title, level, tags, content, hints, external_id}}.
    Some slugs appear twice — we keep the first."""
    df = pd.read_parquet(PROBLEMS_PARQUET).drop_duplicates(
        "title_slug", keep="first",
    )
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        out[r["title_slug"]] = {
            "external_id": int(r["id"]),
            "title": r["title_slug"].replace("-", " ").title(),
            "level": r["level"],
            "tags": r["tag"] or "",
            "content": r["question_content"] or "",
            "hints": (None if pd.isna(r.get("question_hints"))
                      else r["question_hints"]),
        }
    return out


def slugs_already_in_competitions(session: Session) -> set[str]:
    slugs: set[str] = set()
    for row in session.exec(
        select(Problem.title_slug)
        .join(CompetitionProblem, CompetitionProblem.problem_id == Problem.id)
        .join(Competition, Competition.id == CompetitionProblem.competition_id)
        .where(Competition.slug.in_(TARGET_COMPETITIONS))
    ).all():
        # `.exec()` on a single-column select returns str, not a tuple.
        slugs.add(row if isinstance(row, str) else row[0])
    return slugs


# ---------- LC question fallback (for slugs not in parquet) ------------------

def fetch_question_from_lc(slug: str) -> dict | None:
    """Small read-only LC GraphQL. NOT subject to the write-side 429 that
    burned our earlier submission attempts. Used only for picks that
    aren't in `problems.parquet`."""
    from . import leetcode_dist
    import httpx
    query = (
        "query questionData($titleSlug: String!) {\n"
        "  question(titleSlug: $titleSlug) {\n"
        "    titleSlug title questionId difficulty\n"
        "    topicTags { name }\n"
        "    content hints\n"
        "    codeSnippets { lang langSlug code }\n"
        "  }\n"
        "}\n"
    )
    client = leetcode_dist.build_client()
    try:
        r = client.post("/graphql", json={
            "query": query, "variables": {"titleSlug": slug},
        }, timeout=30)
    except httpx.HTTPError as e:
        print(f"    ! {slug}: LC fetch failed: {e}", file=sys.stderr)
        return None
    if r.status_code != 200:
        print(f"    ! {slug}: LC HTTP {r.status_code}", file=sys.stderr)
        return None
    d = r.json().get("data", {}).get("question")
    if not d:
        return None
    return {
        "external_id": int(d["questionId"]),
        "title": d["title"] or slug.replace("-", " ").title(),
        "level": d["difficulty"],
        "tags": ",".join(t["name"] for t in (d["topicTags"] or [])),
        "content": d["content"] or "",
        "hints": (json.dumps(d["hints"]) if d.get("hints") else None),
    }


# ---------- pick + rank ------------------------------------------------------

def build_candidate_pool(
    dataset_slugs: set[str],
    ranking: dict[str, dict],
    already_in_comp: set[str],
) -> dict[str, list[dict]]:
    pool = {"Medium": [], "Hard": []}
    for slug in dataset_slugs & set(ranking.keys()):
        r = ranking[slug]
        lvl = r.get("level")
        if lvl not in pool:
            continue
        if r.get("p50_ms", 0) < MIN_MEDIAN_MS:
            continue
        if r.get("spread_ms", 0) < MIN_SPREAD_MS:
            continue
        if r.get("n_buckets", 0) < MIN_BUCKETS:
            continue
        if slug in already_in_comp:
            continue
        pool[lvl].append(r)
    return pool


def pick_replacements(
    pool: dict[str, list[dict]],
) -> tuple[list[dict], list[dict], list[dict]]:
    hards = list(pool["Hard"])
    mediums = list(pool["Medium"])
    need_h = N_SHARED_HARDS + N_LCPERF_EXTRA_HARDS
    if len(hards) < need_h:
        raise SystemExit(f"only {len(hards)} Hard candidates, need {need_h}")
    if len(mediums) < N_LCPERF_MEDIUMS:
        raise SystemExit(f"only {len(mediums)} Medium candidates, "
                         f"need {N_LCPERF_MEDIUMS}")
    shared = sorted(hards, key=lambda r: -r["p50_ms"])[:N_SHARED_HARDS]
    shared_set = {r["title_slug"] for r in shared}
    extra = [r for r in sorted(hards, key=lambda r: -r["spread_ms"])
             if r["title_slug"] not in shared_set][:N_LCPERF_EXTRA_HARDS]
    picked_mediums = sorted(mediums,
                            key=lambda r: -r["spread_ms"])[:N_LCPERF_MEDIUMS]
    return shared, extra, picked_mediums


# ---------- statement/tags resolution ---------------------------------------

def resolve_problem_data(slug: str, parquet_index: dict[str, dict],
                         *, allow_lc_fallback: bool) -> dict | None:
    """Return the fields needed to insert a Problem row. Prefers parquet
    (offline). Falls back to LC's `question` GraphQL for slugs missing
    from parquet (only 2 expected in practice)."""
    if slug in parquet_index:
        return parquet_index[slug]
    if not allow_lc_fallback:
        return None
    print(f"    · {slug} not in parquet — fetching from LC")
    return fetch_question_from_lc(slug)


# ---------- DB writes --------------------------------------------------------

def seed_problems(session: Session,
                  picks: list[dict]) -> dict[str, int]:
    slug_to_pid: dict[str, int] = {}
    for p in picks:
        slug = p["slug"]
        existing = session.exec(
            select(Problem).where(Problem.title_slug == slug)
        ).first()
        if existing:
            slug_to_pid[slug] = existing.id
            print(f"    already has Problem row: {slug} (id={existing.id})")
            continue
        data = p["data"]
        prob = Problem(
            external_id=data["external_id"],
            title_slug=slug,
            title=data["title"],
            level=data["level"],
            tags=data["tags"],
            content=data["content"],
            hints=data["hints"],
        )
        session.add(prob)
        session.flush()
        slug_to_pid[slug] = prob.id
        print(f"    seeded {slug} (id={prob.id})")
    session.commit()
    return slug_to_pid


def rewire_membership(
    session: Session,
    lc99_perf_adds: list[str],
    lc99_hard_perf_adds: list[str],
) -> None:
    def _get_comp(slug: str) -> Competition:
        c = session.exec(select(Competition).where(Competition.slug == slug)).first()
        if c is None:
            raise SystemExit(f"competition '{slug}' not found")
        return c

    def _pid(slug: str) -> int:
        p = session.exec(select(Problem).where(Problem.title_slug == slug)).first()
        if p is None:
            raise SystemExit(f"problem '{slug}' not seeded yet")
        return p.id

    for comp_slug in TARGET_COMPETITIONS:
        comp = _get_comp(comp_slug)
        # Remove excluded members present in THIS competition.
        for slug in EXCLUDED_SLUG_LIST:
            p = session.exec(
                select(Problem).where(Problem.title_slug == slug)
            ).first()
            if p is None:
                continue
            row = session.exec(
                select(CompetitionProblem)
                .where(CompetitionProblem.competition_id == comp.id,
                       CompetitionProblem.problem_id == p.id)
            ).first()
            if row is not None:
                session.delete(row)
                print(f"    {comp_slug}: removed {slug}")
        # Add replacements.
        adds = (lc99_perf_adds if comp_slug == "leetcode-99-perf"
                else lc99_hard_perf_adds)
        for slug in adds:
            pid = _pid(slug)
            already = session.exec(
                select(CompetitionProblem)
                .where(CompetitionProblem.competition_id == comp.id,
                       CompetitionProblem.problem_id == pid)
            ).first()
            if already is not None:
                print(f"    {comp_slug}: {slug} already linked — skip")
                continue
            session.add(CompetitionProblem(competition_id=comp.id,
                                            problem_id=pid))
            print(f"    {comp_slug}: added {slug}")
    session.commit()


def hard_delete_attempts_and_runs(session: Session,
                                   excluded_slugs: list[str]) -> None:
    pids = {
        (r if isinstance(r, int) else r[0])
        for r in session.exec(
            select(Problem.id).where(Problem.title_slug.in_(excluded_slugs))
        ).all()
    }
    if not pids:
        print("    no matching Problem rows found — nothing to delete")
        return
    attempts = session.exec(
        select(Attempt).where(Attempt.problem_id.in_(pids))
    ).all()
    aids = {a.id for a in attempts}
    n_runs = 0
    if aids:
        for r in session.exec(select(Run).where(Run.attempt_id.in_(aids))).all():
            session.delete(r); n_runs += 1
    for a in attempts:
        session.delete(a)
    session.commit()
    print(f"    hard-deleted {len(attempts)} Attempts + {n_runs} Runs "
          f"across {len(excluded_slugs)} excluded slug(s)")


def annotate_ranking(picked_slugs: list[str]) -> None:
    """Tag the existing runtime_ranking.json rows for the 6 picks with a
    `source: dataset-replacement` marker so an auditor can tell them
    apart from the original 99's picks. No new rows added — the entries
    already existed."""
    rows = json.loads(RANKING_JSON.read_text())
    marked = 0
    for r in rows:
        if r.get("title_slug") in picked_slugs:
            r["source"] = "dataset-replacement"
            marked += 1
    RANKING_JSON.write_text(json.dumps(rows, indent=2))
    print(f"    annotated {marked} row(s) in runtime_ranking.json")


# ---------- main -------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, type=Path,
                    help="LeetCodeDataset-v0.1.0-train.jsonl")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan; skip DB writes and any LC calls")
    args = ap.parse_args()

    if not args.dataset.exists():
        raise SystemExit(f"dataset not found: {args.dataset}")

    print("=== replace_excluded_problems ===")
    print(f"dataset:  {args.dataset}")
    print(f"excluded: {EXCLUDED_SLUG_LIST}")
    print(f"dry-run:  {args.dry_run}")
    print()

    dataset_slugs = load_dataset_slugs(args.dataset)
    ranking = load_ranking()
    parquet_index = load_parquet_index()

    with Session(engine) as session:
        already_in_comp = slugs_already_in_competitions(session)
        pool = build_candidate_pool(dataset_slugs, ranking, already_in_comp)
        print(f"candidate pool (in dataset ∩ ranking, thresholds cleared, "
              f"not in comp):")
        print(f"  Medium: {len(pool['Medium'])}")
        print(f"  Hard:   {len(pool['Hard'])}")
        print()

        shared, extra, mediums = pick_replacements(pool)

        print("=== picks ===")
        for label, group in (("shared Hards (p50 desc)", shared),
                             ("lc99-perf-only Hards (spread desc)", extra),
                             ("Mediums (spread desc)", mediums)):
            print(f"{label}:")
            for r in group:
                print(f"  {r['title_slug']:<55} "
                      f"p50={r['p50_ms']:>8.1f}ms  "
                      f"spread={r['spread_ms']:>8.1f}ms  "
                      f"buckets={r['n_buckets']}")
        print()

        # Resolve statement + tags for each pick.
        all_picks: list[dict] = []
        print("=== resolving problem data (parquet → LC fallback) ===")
        for r in shared + extra + mediums:
            slug = r["title_slug"]
            data = resolve_problem_data(
                slug, parquet_index,
                allow_lc_fallback=not args.dry_run,
            )
            if data is None:
                if args.dry_run:
                    # For dry-run, just fabricate placeholders so downstream
                    # printout stays readable.
                    data = {"external_id": 0, "title": slug, "level": r["level"],
                            "tags": "", "content": "", "hints": None}
                    print(f"  · {slug}: not in parquet (would fetch from LC)")
                else:
                    raise SystemExit(f"couldn't resolve statement for {slug}")
            else:
                where = ("parquet" if slug in parquet_index else "LC")
                print(f"  · {slug}: resolved from {where} "
                      f"({len(data['tags'].split(','))} tag(s))")
            all_picks.append({"slug": slug, "level": r["level"], "data": data})

        picked_slugs = [p["slug"] for p in all_picks]
        shared_slugs = [r["title_slug"] for r in shared]

        if args.dry_run:
            print()
            print("--- DRY RUN — no DB writes ---")
            print(f"would seed 6 Problem rows: {picked_slugs}")
            print(f"lc99-perf:      remove {len(EXCLUDED_SLUG_LIST)}, "
                  f"add {len(picked_slugs)}")
            print(f"lc99-hard-perf: remove Hard-excluded present, "
                  f"add {len(shared_slugs)} shared Hards: {shared_slugs}")
            return

        print()
        print("=== seeding Problem rows ===")
        seed_problems(session, all_picks)

        print()
        print("=== rewiring CompetitionProblem membership ===")
        rewire_membership(session,
                          lc99_perf_adds=picked_slugs,
                          lc99_hard_perf_adds=shared_slugs)

        print()
        print("=== hard-deleting Attempts + Runs on excluded slugs ===")
        hard_delete_attempts_and_runs(session, EXCLUDED_SLUG_LIST)

    print()
    print("=== annotating runtime_ranking.json ===")
    annotate_ranking(picked_slugs)

    print()
    print("=== done ===")


if __name__ == "__main__":
    main()
