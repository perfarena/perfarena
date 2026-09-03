"""Delete the local-perf-harness attempts for the LC problems that had harness
bugs (fixed 2026-09-02) so a re-run re-measures them cleanly on the now-fixed
harness. Scope is deliberately narrow:

  * only the 10 harness-affected problems (SLUGS below),
  * only submissions on the arena-local / arena-linux-local harnesses
    (the local perf harness — NOT the leetcode judge),
  * only NON-Accepted attempts (an attempt with any Accepted run is KEPT,
    since its measurement is real).

Each deleted attempt's Run rows go first (SQLite has no cascade). Dry-run by
default; nothing is written without --apply.

    # from the backend host, in the app context (adjust to your deploy):
    docker exec -i perfarena python -m app.purge_harness_attempts             # dry-run
    docker exec -i perfarena python -m app.purge_harness_attempts --apply     # delete
"""
from __future__ import annotations

import argparse

from sqlmodel import Session, select

from .db import engine
from .models import Attempt, Harness, Problem, Run, Submission

SLUGS = [
    "finding-mk-average",
    "random-pick-index",
    "design-a-stack-with-increment-operation",
    "stream-of-characters",
    "word-ladder",
    "evaluate-the-bracket-pairs-of-a-string",
    "cousins-in-binary-tree",
    "minimum-depth-of-binary-tree",
    "linked-list-components",
    "middle-of-the-linked-list",
]
LOCAL_HARNESS_SLUGS = ["arena-local", "arena-linux-local"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default: dry-run, no writes)")
    args = ap.parse_args()

    with Session(engine) as s:
        pid = {p.title_slug: p.id for p in s.exec(
            select(Problem).where(Problem.title_slug.in_(SLUGS))).all()}
        problem_ids = set(pid.values())
        hids = {h.slug: h.id for h in s.exec(
            select(Harness).where(Harness.slug.in_(LOCAL_HARNESS_SLUGS))).all()}
        harness_ids = set(hids.values())
        if not problem_ids:
            print("no matching problems found; nothing to do."); return
        if not harness_ids:
            print(f"no local-perf harness ({LOCAL_HARNESS_SLUGS}) found; nothing to do."); return
        print(f"problems: {len(problem_ids)}/{len(SLUGS)} resolved · "
              f"local harnesses: {hids}")

        sub_ids = {sub.id for sub in s.exec(
            select(Submission).where(Submission.harness_id.in_(harness_ids))).all()}
        if not sub_ids:
            print("no submissions on the local perf harness; nothing to do."); return

        atts = s.exec(select(Attempt).where(
            Attempt.problem_id.in_(problem_ids),
            Attempt.submission_id.in_(sub_ids),
        )).all()

        rev = {v: k for k, v in pid.items()}
        to_delete: list[tuple[Attempt, list[Run]]] = []
        for a in atts:
            runs = s.exec(select(Run).where(Run.attempt_id == a.id)).all()
            if not any(r.status_msg == "Accepted" for r in runs):
                to_delete.append((a, runs))

        by_slug: dict[str, int] = {}
        n_runs = 0
        for a, runs in to_delete:
            by_slug[rev.get(a.problem_id, str(a.problem_id))] = \
                by_slug.get(rev.get(a.problem_id, str(a.problem_id)), 0) + 1
            n_runs += len(runs)

        print(f"\n{'APPLY' if args.apply else 'DRY-RUN'}: "
              f"{len(to_delete)} non-Accepted attempt(s) (+{n_runs} run rows) "
              f"across {len(sub_ids)} local-harness submission(s)")
        for slug, n in sorted(by_slug.items()):
            print(f"  {slug:45} {n}")
        if not to_delete:
            print("nothing to delete."); return

        if args.apply:
            for a, runs in to_delete:
                for r in runs:
                    s.delete(r)
                s.delete(a)
            s.commit()
            print(f"\ndeleted {len(to_delete)} attempts + {n_runs} runs. "
                  f"Re-run the sweep over these problems to re-measure.")
        else:
            print("\n(dry-run — re-run with --apply to delete)")


if __name__ == "__main__":
    main()
