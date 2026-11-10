"""Anti-contamination problem rewriter — CSV round-trip.

Workflow
--------
1.  Export the source competition's problems to a CSV with one row per
    problem and columns for the BOTH the canonical fields (slug, title,
    function_name, content, code_snippets) AND the to-be-filled rewrite
    fields (new_slug, new_title, new_function_name, new_content).

2.  Hand-edit the CSV (or delegate to an LLM / subagent) so every row
    has a semantically-equivalent but lexically-distinct rephrase. Keep
    `class Solution` unchanged — only the function name moves.

3.  Import the CSV — creates a NEW competition + 99 new Problem rows
    (one per original), each carrying `original_problem_id` and the
    function-name substitution map. Dispatch (see dispatch.py) routes
    these variants back to LC under the original's title_slug AND
    rewrites the LLM's code with `function_name → original_function_name`
    before submission.

Why
---
Strong models trained on public LeetCode solutions ace the canonical 99
in 1-shot — runtime/memory numbers are dominated by memorisation, not
algorithmic skill. A rephrased problem set with distinct function names
forces the model to actually solve the problem from scratch. Comparing
a model's score on the original vs. the rewritten set gives a
contamination delta — the gap is a proxy for how much of the headline
score is memorised lookup vs. real ability.

CLI
---
    # 1) dump CSV for editing
    python -m app.seed_rewritten_competition export \
        --source-slug leetcode-99-perf \
        --out data/leetcode-99-rewritten-statements.csv

    # 2) (manual / agent-driven editing — fill new_* columns)

    # 3) load CSV → create new competition + variant Problems
    python -m app.seed_rewritten_competition import \
        --in data/leetcode-99-rewritten-statements.csv \
        --source-slug leetcode-99-perf \
        --dest-slug leetcode-99-rewritten \
        --dest-name "LeetCode 99 (rewritten)"

The importer is idempotent on the dest competition (re-running updates
the variants in place rather than spawning duplicates). Safe to iterate
on the CSV without rebuilding from scratch.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from sqlmodel import Session, select

from .db import engine, init_db
from .models import Competition, CompetitionProblem, Problem


# ---------- function-name extraction ---------------------------------------

# Per-language regex for "what is the entry-point function name in this
# starter snippet?" We try Python first because it has the simplest
# signature shape; if the problem has no python3 snippet we fall back to
# the others. LC's convention is camelCase across every language, so
# whichever language we look at, the answer is the same string.
_FN_PATTERNS: list[tuple[str, str]] = [
    # python3: `def twoSum(self, ...)` — captures `twoSum`
    ("python3", r"^\s*def\s+(\w+)\s*\("),
    # java: `public ... methodName(` — captures the method NAME, not the
    # access modifier. Generic-parameter sigs (`<T> int foo(`) handled.
    ("java", r"public\s+(?:static\s+)?(?:<[^>]+>\s+)?[\w\[\]<>?, ]+?\s+(\w+)\s*\("),
    ("cpp", r"\b[\w:<>*&\s]+?\s+(\w+)\s*\([^)]*\)\s*\{"),
    ("csharp", r"public\s+[\w\[\]<>?, ]+\s+(\w+)\s*\("),
    ("javascript", r"^\s*(?:var|let|const)\s+(\w+)\s*=\s*function|^\s*function\s+(\w+)"),
    ("golang", r"^\s*func\s+(?:\([^)]+\)\s+)?(\w+)\s*\("),
    ("rust", r"^\s*pub\s+fn\s+(\w+)|^\s*fn\s+(\w+)"),
]


def extract_function_name(code_snippets: dict | None) -> str | None:
    """Best-effort: return the LeetCode entry-point function name. Returns
    None when no language match — operator can fill it in by hand in the
    CSV."""
    if not isinstance(code_snippets, dict):
        return None
    for lang, pattern in _FN_PATTERNS:
        snippet = code_snippets.get(lang)
        if not snippet:
            continue
        m = re.search(pattern, snippet, flags=re.MULTILINE)
        if m:
            # Some patterns have an alternation with two capture groups;
            # pick whichever matched.
            for g in m.groups():
                if g:
                    return g
    return None


def substitute_function_name(code_snippets: dict, old: str, new: str) -> dict:
    """Apply `old → new` rewrite to every language snippet. Simple text
    replace — the snippets are short and the function name shape (camelCase)
    is distinctive enough that false positives are improbable."""
    if not isinstance(code_snippets, dict) or old == new:
        return code_snippets or {}
    return {
        lang: (snip.replace(old, new) if isinstance(snip, str) else snip)
        for lang, snip in code_snippets.items()
    }


# ---------- CSV columns -----------------------------------------------------
#
# Header order is fixed so DictReader/DictWriter agree and humans can
# eyeball the file. The first block of columns is ORIGINAL data — read-only
# reference. The "new_*" block is what gets edited.

CSV_FIELDS = [
    # Read-only reference columns (echo of the source problem).
    "original_id",
    "original_slug",
    "original_title",
    "original_level",
    "original_function_name",
    "original_content",
    "original_code_snippets_json",
    # Editable columns — fill these in.
    "new_slug",
    "new_title",
    "new_function_name",
    "new_content",
]


# ---------- export ----------------------------------------------------------

def cmd_export(args: argparse.Namespace) -> None:
    """Dump source competition's problems to CSV for hand-editing."""
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    init_db()                              # ensure additive columns exist before SELECT

    with Session(engine) as session:
        comp = session.exec(
            select(Competition).where(Competition.slug == args.source_slug)
        ).first()
        if comp is None:
            print(f"error: no competition with slug {args.source_slug!r}",
                  file=sys.stderr)
            sys.exit(2)
        cp_rows = session.exec(
            select(CompetitionProblem).where(
                CompetitionProblem.competition_id == comp.id
            )
        ).all()
        if not cp_rows:
            print(f"error: competition {args.source_slug!r} has no problems",
                  file=sys.stderr)
            sys.exit(2)
        problem_ids = [cp.problem_id for cp in cp_rows]
        problems = session.exec(
            select(Problem).where(Problem.id.in_(problem_ids))
        ).all()
        # Stable ordering — level then title — so re-running export gives
        # the same row ordering for VCS-friendly diffs.
        problems.sort(key=lambda p: (p.level, p.title.lower()))

    n_written = 0
    n_missing_fn = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for p in problems:
            fn = extract_function_name(p.code_snippets)
            if fn is None:
                n_missing_fn += 1
            writer.writerow({
                "original_id":                 p.id,
                "original_slug":               p.title_slug,
                "original_title":              p.title,
                "original_level":              p.level,
                "original_function_name":      fn or "",
                "original_content":            p.content or "",
                "original_code_snippets_json": json.dumps(p.code_snippets or {},
                                                          ensure_ascii=False),
                # Edit-me block — left blank intentionally.
                "new_slug":                    "",
                "new_title":                   "",
                "new_function_name":           "",
                "new_content":                 "",
            })
            n_written += 1

    print(f"wrote {n_written} row(s) to {out_path}")
    if n_missing_fn:
        print(f"  WARN: couldn't auto-extract original_function_name on "
              f"{n_missing_fn} row(s) — fill those in by hand before import")


# ---------- import ----------------------------------------------------------

def _slug_ok(slug: str) -> bool:
    """Match the loose LeetCode convention: lowercase letters/digits/dashes."""
    return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug))


def _validate_row(row: dict, line_no: int) -> list[str]:
    """Return a list of validation errors for one row. Empty list = OK."""
    problems: list[str] = []
    for k in ("original_slug", "original_id", "original_function_name",
              "new_slug", "new_title", "new_function_name", "new_content"):
        if not (row.get(k) or "").strip():
            problems.append(f"  line {line_no}: {k} is empty")
    if (row.get("new_slug") or "").strip() and not _slug_ok(row["new_slug"]):
        problems.append(
            f"  line {line_no}: new_slug {row['new_slug']!r} must be lowercase "
            f"letters/digits/dashes only"
        )
    if (row.get("new_function_name") or "").strip():
        if not re.fullmatch(r"[A-Za-z_]\w*", row["new_function_name"]):
            problems.append(
                f"  line {line_no}: new_function_name "
                f"{row['new_function_name']!r} isn't a valid identifier"
            )
    return problems


def cmd_import(args: argparse.Namespace) -> None:
    """Load CSV → create/update the destination competition + variants."""
    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"error: {in_path} not found", file=sys.stderr)
        sys.exit(2)
    init_db()                              # ensure additive columns exist

    with in_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("error: CSV has no data rows", file=sys.stderr)
        sys.exit(2)

    # Validate ALL rows up front — failing fast in the middle of the import
    # leaves the DB partially populated, which is hard to recover from.
    errors: list[str] = []
    for i, row in enumerate(rows, start=2):       # +1 for header, +1 for 1-indexed
        errors.extend(_validate_row(row, i))
    if errors:
        print(f"error: {len(errors)} validation problem(s):", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(2)

    with Session(engine) as session:
        # 1. Source competition — for sanity check + provenance.
        source_comp = session.exec(
            select(Competition).where(Competition.slug == args.source_slug)
        ).first()
        if source_comp is None:
            print(f"error: source competition {args.source_slug!r} not found",
                  file=sys.stderr)
            sys.exit(2)

        # 2. Destination competition — create if missing.
        dest_comp = session.exec(
            select(Competition).where(Competition.slug == args.dest_slug)
        ).first()
        if dest_comp is None:
            dest_comp = Competition(
                slug=args.dest_slug,
                name=args.dest_name,
                description=(
                    "Anti-contamination variant of "
                    f"`{source_comp.slug}` — same task set, rephrased "
                    "statements and function names. Submissions are "
                    "transparently routed to the original problems on "
                    "LeetCode at dispatch time."
                ),
                kind="leetcode",
                status="active",
            )
            session.add(dest_comp); session.commit(); session.refresh(dest_comp)
            print(f"created competition {dest_comp.slug!r} (id={dest_comp.id})")
        else:
            print(f"using existing competition {dest_comp.slug!r} "
                  f"(id={dest_comp.id})")

        # 3. Per-row: create OR update the variant Problem, link to dest comp.
        n_new = 0
        n_updated = 0
        for row in rows:
            original = session.get(Problem, int(row["original_id"]))
            if original is None or original.title_slug != row["original_slug"]:
                print(f"  WARN: original id={row['original_id']!r} / slug "
                      f"{row['original_slug']!r} mismatch — skipping",
                      file=sys.stderr)
                continue
            # Pre-existing variant? Match on dest competition + original.
            existing_variant = session.exec(
                select(Problem).where(
                    Problem.original_problem_id == original.id
                )
            ).all()
            # We may have multiple variants if the user did several import
            # rounds; pick the one already linked to dest_comp, if any.
            link_for_dest = None
            for v in existing_variant:
                cp = session.exec(
                    select(CompetitionProblem).where(
                        CompetitionProblem.competition_id == dest_comp.id,
                        CompetitionProblem.problem_id == v.id,
                    )
                ).first()
                if cp is not None:
                    link_for_dest = v
                    break

            rewritten_snippets = substitute_function_name(
                original.code_snippets or {},
                row["original_function_name"],
                row["new_function_name"],
            )

            if link_for_dest is None:
                variant = Problem(
                    # We deliberately re-use external_id so leaderboard
                    # grouping that keys on it (rare) keeps working. The
                    # title_slug is what URL routing keys off.
                    external_id=original.external_id,
                    title_slug=row["new_slug"],
                    title=row["new_title"],
                    level=original.level,
                    tags=original.tags,
                    content=row["new_content"],
                    hints=None,          # no hints on rewrites (don't leak)
                    code_snippets=rewritten_snippets,
                    original_problem_id=original.id,
                    function_name=row["new_function_name"],
                    original_function_name=row["original_function_name"],
                )
                session.add(variant); session.commit(); session.refresh(variant)
                session.add(CompetitionProblem(
                    competition_id=dest_comp.id, problem_id=variant.id,
                ))
                session.commit()
                n_new += 1
            else:
                # Update in place — supports iterating on the CSV without
                # spawning duplicate Problem rows.
                v = link_for_dest
                v.title_slug = row["new_slug"]
                v.title = row["new_title"]
                v.content = row["new_content"]
                v.code_snippets = rewritten_snippets
                v.function_name = row["new_function_name"]
                v.original_function_name = row["original_function_name"]
                session.add(v); session.commit()
                n_updated += 1

        print(f"done: created {n_new}, updated {n_updated}, "
              f"competition '{dest_comp.slug}' has {n_new + n_updated} variants")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Anti-contamination competition rewriter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("export", help="dump source competition to CSV")
    ex.add_argument("--source-slug", default="leetcode-99-perf",
                    help="competition to export (default: leetcode-99-perf)")
    ex.add_argument("--out", required=True, help="CSV output path")
    ex.set_defaults(func=cmd_export)

    im = sub.add_parser("import", help="create competition + variants from CSV")
    im.add_argument("--in", dest="in_path", required=True,
                    help="CSV input path")
    im.add_argument("--source-slug", default="leetcode-99-perf",
                    help="canonical competition the CSV originated from")
    im.add_argument("--dest-slug", default="leetcode-99-rewritten",
                    help="slug for the new variants competition")
    im.add_argument("--dest-name", default="LeetCode 99 (rewritten)",
                    help="display name for the new variants competition")
    im.set_defaults(func=cmd_import)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
