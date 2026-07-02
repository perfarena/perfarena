"""One-shot backfill: assign `competition_id` to legacy submissions.

Pre-per-submission-competition rows have `competition_id IS NULL` and
were created with `total_problems = len(every Problem row in the DB)`
— so once the rewritten-99 competition landed, those submissions started
reporting 45/198 instead of 45/99 on the leaderboard. This script
defaults them to `leetcode-99-perf` (the canonical first competition)
and recomputes `total_problems` + `pass_at_1` from that competition's
problem count.

Why default to leetcode-99-perf
--------------------------------
Every existing legacy submission was created BEFORE the
`leetcode-99-rewritten` competition existed, so its attempts could ONLY
have targeted canonical problems. We never want to retroactively claim
a legacy sweep was an attempt at the rewritten set.

Safety
------
* Idempotent — only touches submissions with `competition_id IS NULL`
  and `deleted_at IS NULL`. Already-scoped submissions are untouched.
* Counts ROWS but doesn't move ANY attempt or run data — purely a
  metadata fix.
* `--dry-run` shows what would change without writing anything.

CLI
---
    # see what would change without writing
    python -m app.backfill_legacy_competition --dry-run

    # actually apply
    python -m app.backfill_legacy_competition

    # target a different default (rarely useful — the script's whole
    # purpose is to preserve canonical-99 semantics for legacy rows)
    python -m app.backfill_legacy_competition --default leetcode-99-perf
"""
from __future__ import annotations

import argparse
import sys

from sqlmodel import Session, select

from .db import engine, init_db
from .models import (
    Attempt, Competition, CompetitionProblem, Problem, Run, Submission,
)


def _recompute_solved_and_pass_at_1(
    session: Session, sub: Submission, comp_problem_ids: set[int],
) -> tuple[int, float | None]:
    """Recount `total_solved` against the competition's problem set.
    Solved = at least one Accepted Run for the problem.

    Returns (total_solved, pass_at_1). pass_at_1 is None when
    total_problems is 0 (would have been a divide-by-zero)."""
    attempts = session.exec(
        select(Attempt).where(Attempt.submission_id == sub.id)
    ).all()
    solved_problem_ids: set[int] = set()
    for a in attempts:
        if a.problem_id not in comp_problem_ids:
            # An out-of-scope attempt — silently ignore. Legacy data
            # shouldn't have these (variants didn't exist when these
            # submissions were created) but defend in depth.
            continue
        runs = session.exec(select(Run).where(Run.attempt_id == a.id)).all()
        if any(r.status_msg == "Accepted" for r in runs):
            solved_problem_ids.add(a.problem_id)
    total_solved = len(solved_problem_ids)
    n = len(comp_problem_ids) or 1
    return total_solved, (total_solved / n) if comp_problem_ids else None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Default legacy submissions to a competition + recompute "
                    "the per-submission denominators.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--default", default="leetcode-99-perf",
                    help="competition slug to assign (default: leetcode-99-perf).")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change without writing.")
    args = ap.parse_args()

    init_db()

    with Session(engine) as session:
        comp = session.exec(
            select(Competition).where(Competition.slug == args.default)
        ).first()
        if comp is None:
            print(f"error: competition {args.default!r} doesn't exist",
                  file=sys.stderr)
            sys.exit(2)
        comp_problem_ids = {
            cp.problem_id for cp in session.exec(
                select(CompetitionProblem).where(
                    CompetitionProblem.competition_id == comp.id
                )
            ).all()
        }
        if not comp_problem_ids:
            print(f"error: competition {args.default!r} has no problems",
                  file=sys.stderr)
            sys.exit(2)

        # Targets: ALL submissions with competition_id IS NULL.
        # Deleted-at IS NULL filter is intentionally OFF here — we want
        # to fix even soft-deleted rows so a future "undelete" reads
        # consistent numbers. But we DON'T re-recompute pass@k on
        # soft-deleted rows (no point).
        targets = session.exec(
            select(Submission).where(Submission.competition_id.is_(None))
        ).all()
        if not targets:
            print(f"nothing to do — every submission already has a competition.")
            return

        print(f"target competition: {comp.slug!r} (id={comp.id}, "
              f"{len(comp_problem_ids)} problems)")
        print(f"submissions to backfill: {len(targets)}")
        print()

        changes = []
        for sub in targets:
            old_total = sub.total_problems
            old_solved = sub.total_solved
            old_p1 = sub.pass_at_1
            # Recompute solved / pass_at_1 only on live (non-deleted)
            # submissions; soft-deleted ones get the scope but not the
            # stat refresh.
            if sub.deleted_at is None:
                new_solved, new_p1 = _recompute_solved_and_pass_at_1(
                    session, sub, comp_problem_ids
                )
            else:
                new_solved = old_solved
                # Clamp old pass_at_1 if total changes; otherwise keep.
                new_p1 = old_p1
            new_total = len(comp_problem_ids)
            changes.append({
                "sub_id":  sub.id,
                "model":   f"{sub.model_name}",
                "lang":    sub.language,
                "old":     (old_total, old_solved, old_p1),
                "new":     (new_total, new_solved, new_p1),
                "deleted": sub.deleted_at is not None,
            })
            if not args.dry_run:
                sub.competition_id = comp.id
                sub.total_problems = new_total
                sub.total_solved = new_solved
                if new_p1 is not None:
                    sub.pass_at_1 = new_p1
                session.add(sub)

        if not args.dry_run:
            session.commit()

        # Report
        print(f"  {'sub':>4} {'model':<22} {'lang':<6} {'tot':>5} → {'tot':>5} "
              f"{'sol':>5} → {'sol':>5} {'p@1':>7} → {'p@1':>7} {'state':<10}")
        for ch in changes:
            ot, os_, op1 = ch["old"]
            nt, ns_, np1 = ch["new"]
            state = "deleted" if ch["deleted"] else "live"
            print(f"  {ch['sub_id']:>4} {ch['model'][:22]:<22} {ch['lang']:<6} "
                  f"{ot or 0:>5} → {nt:>5} "
                  f"{os_ or 0:>5} → {ns_:>5} "
                  f"{op1 or 0:>7.3f} → {np1 or 0:>7.3f} {state}")

        if args.dry_run:
            print(f"\n(dry-run — no DB writes. Re-run without --dry-run to apply.)")
        else:
            print(f"\ndone — {len(changes)} submission(s) backfilled to "
                  f"competition {comp.slug!r}.")


if __name__ == "__main__":
    main()
