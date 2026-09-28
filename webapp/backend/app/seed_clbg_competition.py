"""Seed the CLBG (Computer Language Benchmarks Game) problem set + competition.

Reads the canonical spec from the perfarena fork's problems.yaml
(``Energy-Languages/perfarena/configs/problems.yaml``) — the same
source of truth the perfarena harness itself consumes — so any spec
tweaks propagate here on re-run.

Idempotent: existing Problem rows are updated in place; the
`clbg-10` Competition is created if missing and re-membered
otherwise.

Usage inside the perfarena container:

    docker exec -i perfarena python3 -m app.seed_clbg_competition \\
        --problems-yaml /workspace/perfarena/configs/problems.yaml

Or bind-mount the fork read-only at /workspace and drop `--problems-yaml`.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from sqlmodel import Session, select

from .db import engine
from .models import (
    Competition, CompetitionProblem, Harness, Problem,
)


# Reserved id band so CLBG rows never collide with LC's real question ids.
# LeetCode question ids top out around 3800 today; 900000+ is safe forever.
_CLBG_EXTERNAL_ID_BASE = 900_000

_COMPETITION_SLUG = "clbg-10"
_COMPETITION_NAME = "CLBG — 10 energy benchmarks"
_COMPETITION_DESC = (
    "The 10 Computer Language Benchmarks Game problems used by the "
    "greensoftwarelab/Energy-Languages harness. Each submission is "
    "scored by the arena-local harness on wall-clock runtime, peak RSS, "
    "and package-domain energy (RAPL on Linux; CodeCarbon/powermetrics "
    "on macOS). Unlike LeetCode competitions, there are no test-case "
    "inputs — every problem has one canonical N (`default_argument`) at "
    "which measurement runs, and a small `validation_n` where output is "
    "diff'd against a reference file for correctness."
)
_HARNESS_SLUG = "arena-local"


def _render_content(p: dict) -> str:
    """Turn one problems.yaml entry into a self-contained markdown blob
    used as `problem.content` (the SPA renders this on the problem
    detail page)."""
    parts = [
        f"# {p['name']}",
        "",
        p["description"].strip(),
        "",
        "## Input",
        p["input_spec"].strip(),
        "",
        "## Output",
        "```",
        p["output_spec"].rstrip(),
        "```",
        "",
        "## Invocation",
        p["invocation_hint"].strip(),
        "",
        f"**Default N:** `{p['default_argument']}` · "
        f"**Validation N:** `{p['validation_n']}`",
    ]
    return "\n".join(parts)


def seed(problems_yaml: Path) -> None:
    with problems_yaml.open() as fh:
        raw = yaml.safe_load(fh)
    problem_specs = raw["problems"]
    assert len(problem_specs) == 10, \
        f"expected 10 CLBG problems, found {len(problem_specs)}"

    with Session(engine) as s:
        # ---- Problems ----
        pid_by_slug: dict[str, int] = {}
        for i, spec in enumerate(problem_specs):
            slug = spec["key"]
            existing = s.exec(
                select(Problem).where(Problem.title_slug == slug)
            ).first()
            payload = dict(
                external_id=_CLBG_EXTERNAL_ID_BASE + i,
                title_slug=slug,
                title=spec["name"],
                # No formal difficulty in CLBG. "Hard" is the closest
                # analogue — every problem here rewards real optimization
                # effort, unlike LC easies. Tag `clbg` so filters can peel
                # them off from LC data everywhere in the SPA.
                level="Hard",
                tags=f"clbg,{spec['algorithm_class']}",
                content=_render_content(spec),
                hints=None,
            )
            if existing is None:
                p = Problem(**payload)
                s.add(p); s.flush()
                pid_by_slug[slug] = p.id
                print(f"  [+] problem {slug!r} → id={p.id}")
            else:
                for k, v in payload.items():
                    setattr(existing, k, v)
                s.add(existing)
                pid_by_slug[slug] = existing.id
                print(f"  [~] updated problem {slug!r} (id={existing.id})")
        s.commit()

        # ---- Harness lookup ----
        harness = s.exec(
            select(Harness).where(Harness.slug == _HARNESS_SLUG)
        ).first()
        if harness is None:
            raise SystemExit(
                f"harness {_HARNESS_SLUG!r} not found — seed the harness "
                "registry first (arena-local is created by the default "
                "startup migration)."
            )

        # ---- Competition ----
        comp = s.exec(
            select(Competition).where(Competition.slug == _COMPETITION_SLUG)
        ).first()
        if comp is None:
            comp = Competition(
                slug=_COMPETITION_SLUG,
                name=_COMPETITION_NAME,
                description=_COMPETITION_DESC,
                kind="custom",
                status="active",
                harness_id=harness.id,
                # Left null — CLBG's correctness signal is `make validate`'s
                # diff against a reference output, which the arena-local
                # harness handler runs alongside measurement. No separate
                # correctness oracle row is needed for this competition.
                correctness_oracle_id=None,
            )
            s.add(comp); s.flush()
            print(f"  [+] competition {_COMPETITION_SLUG!r} → id={comp.id}")
        else:
            comp.name = _COMPETITION_NAME
            comp.description = _COMPETITION_DESC
            comp.kind = "custom"
            comp.status = "active"
            comp.harness_id = harness.id
            s.add(comp)
            print(f"  [~] updated competition {_COMPETITION_SLUG!r} "
                  f"(id={comp.id})")
        s.commit()

        # ---- Membership: replace ----
        for m in s.exec(
            select(CompetitionProblem).where(
                CompetitionProblem.competition_id == comp.id
            )
        ).all():
            s.delete(m)
        for slug in pid_by_slug:
            s.add(CompetitionProblem(
                competition_id=comp.id, problem_id=pid_by_slug[slug],
            ))
        s.commit()
        print(f"  [=] membership: {len(pid_by_slug)} problem(s) attached")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--problems-yaml",
        default="/workspace/perfarena/configs/problems.yaml",
        type=Path,
        help="Path to the perfarena fork's problems.yaml (mount the "
             "Energy-Languages tree at /workspace to use the default).",
    )
    args = ap.parse_args()
    if not args.problems_yaml.exists():
        raise SystemExit(f"problems.yaml not found at {args.problems_yaml}")
    print(f"seeding CLBG from {args.problems_yaml} …")
    seed(args.problems_yaml)
    print("done.")


if __name__ == "__main__":
    main()
