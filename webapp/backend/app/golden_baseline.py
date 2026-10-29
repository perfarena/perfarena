"""The golden baseline as a reference submission.

The golden baseline of the PerfArena paper holds, for each problem, language,
and metric, the best correct value that any LLM of the campaign produced,
frozen at the end of the campaign. This module stores it on the platform as
one submission per language, copied from the attempts that the platform
already holds. Nothing is generated, dispatched, or measured again.

A golden cell takes each metric from the LLM that is best in that metric,
so one cell can combine the latency of one LLM with the memory of another.
Its code, tokens, generation time, and cost are those of the latency winner.
The rankings list the golden baseline as a reference row and keep it out of
the Bradley-Terry fit (ranking_api.REFERENCE_KIND).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time

from sqlmodel import Session, select

from . import ranking as rk
from .models import (
    Agent, Attempt, Competition, Harness, Problem, Run, Submission,
    SubmissionStatus,
)

AGENT_SLUG = "golden-baseline"
MODEL_NAME = "golden-baseline"


def _agent(session: Session, frozen_on: date, user_id: int) -> Agent:
    from .ranking_api import REFERENCE_KIND
    agent = session.exec(select(Agent).where(Agent.slug == AGENT_SLUG)).first()
    if agent is None:
        agent = Agent(
            slug=AGENT_SLUG, name="Golden baseline", kind=REFERENCE_KIND,
            description=(
                "Reference, not a model. For each problem, language, and metric, "
                "the best correct solution that the campaign LLMs produced, frozen "
                f"on {frozen_on:%d %b %Y}. Copied from stored results: nothing was "
                "generated or measured again. Listed on the leaderboards, never ranked."),
            created_by_user_id=user_id)
        session.add(agent)
        session.commit()
        session.refresh(agent)
    return agent


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def build(session: Session, competition: str, *, frozen_on: date, user_id: int,
          models: list[str] | None = None, languages: list[str] | None = None,
          harness: str | None = None, cost_of=None,
          exclude: list[tuple[str, str, str]] | None = None) -> dict:
    """Create the golden-baseline submissions of one competition from the
    direct, single-round submissions created on or before `frozen_on`.
    `models` limits the pool (default: every entrant that is not partial) and
    `languages` the languages (default: every language of the pool).
    `exclude` lists (model, language, problem slug) solutions that stay
    accepted but never set a golden value, such as a solution that skips the
    work of the benchmark. Raises ValueError when a golden baseline already
    exists, a requested model or language has no submission in the pool, or
    an exclusion matches no solution of the pool."""
    from .ranking_api import load            # avoid an import cycle
    comp = session.exec(select(Competition).where(Competition.slug == competition)).first()
    if comp is None:
        raise ValueError(f"no competition {competition!r}")
    existing = session.exec(select(Submission).where(
        Submission.competition_id == comp.id, Submission.model_name == MODEL_NAME,
        Submission.deleted_at.is_(None))).first()
    if existing is not None:
        raise ValueError(f"{competition} already has a golden baseline (submission "
                         f"#{existing.id}); delete it first")

    cutoff = datetime.combine(frozen_on, time.max)
    d = load(session, competition, harness=harness, agent="direct", rounds="single",
             created_before=cutoff)
    entrants = d["entrants"]
    pool = {k for k, e in entrants.items()
            if (e.model_name in models if models is not None else e.n_attempted * 3 >= e.n_cells)}
    missing = sorted(set(models or []) - {entrants[k].model_name for k in pool})
    if missing:
        raise ValueError(f"no submission created on or before {frozen_on} for {missing}")
    if not pool:
        raise ValueError(f"no submission of {competition} in the pool")
    pool_langs = {lang for k in pool for lang in entrants[k].languages}
    missing = sorted(set(languages or []) - pool_langs)
    if missing:
        raise ValueError(f"no submission of the pool in {missing}")
    langs = [lang for lang in d["languages"]
             if lang in pool_langs and (languages is None or lang in languages)]

    slugs: dict[int, str] = {}

    def slug(pid: int) -> str:
        if pid not in slugs:
            slugs[pid] = session.get(Problem, pid).title_slug
        return slugs[pid]
    excluded = {tuple(x) for x in exclude or []}
    # A solution labelled invalid (SolutionReview) never sets a golden value
    # either: load() leaves it out of the finals. It still matches an exclusion.
    found = {(entrants[k].model_name, lang, slug(pid))
             for f in (d["finals"], d["invalid_finals"])
             for (lang, pid), by_key in f.items() for k in by_key if k in pool}
    missing = sorted(excluded - found)
    if missing:
        raise ValueError(f"no solution of the pool for the exclusions {missing}")

    harness_row = session.exec(select(Harness).where(Harness.slug == d["harness"])).first()
    agent = _agent(session, frozen_on, user_id)
    version = f"frozen {frozen_on.isoformat()}"
    pool_models = sorted(entrants[k].model_name for k in pool)
    contributions: Counter = Counter()        # (model, metric) -> best values supplied
    created = []

    for lang in langs:
        cells = {pid: {k: v for k, v in by_key.items() if k in pool}
                 for (l, pid), by_key in d["finals"].items() if l == lang}
        cells = {pid: c for pid, c in cells.items() if c}
        if not cells:
            continue
        sources = [s for c in cells.values() for _a, s in c.values()]

        def common(attr):
            vals = [getattr(s, attr) for s in sources if getattr(s, attr) is not None]
            return Counter(vals).most_common(1)[0][0] if vals else None
        oracle_id = common("correctness_oracle_id") or comp.correctness_oracle_id
        sub = Submission(
            user_id=user_id, model_name=MODEL_NAME, model_version=version, language=lang,
            prompt_template=(f"Golden baseline frozen on {frozen_on:%d %b %Y}: for each problem "
                             "and metric, the best correct solution of the campaign LLMs. "
                             "Copied from stored results, not generated or measured."),
            model_params={"reference": AGENT_SLUG, "frozen_on": frozen_on.isoformat(),
                          "pool": pool_models,
                          "excluded": sorted(list(x) for x in excluded if x[1] == lang)},
            status=SubmissionStatus.judged, competition_id=comp.id,
            n_samples_per_problem=1, n_rounds=1, agent_id=agent.id,
            harness_id=harness_row.id if harness_row else None,
            correctness_oracle_id=oracle_id, machine_id=common("machine_id"),
            energy_tool=common("energy_tool"), total_problems=d["n_problems"])
        session.add(sub)
        session.commit()
        session.refresh(sub)

        sums: dict[str, list[float]] = {f: [] for f in (
            "avg_runtime_ms", "best_runtime_ms", "avg_memory_mb", "best_memory_mb",
            "avg_runtime_energy_j", "best_runtime_energy_j")}
        for pid, c in sorted(cells.items()):
            # The winner of a metric has the lowest value; a tie goes to the
            # first entrant key, so a rebuild picks the same solutions.
            winner = {}
            for m, attr in rk.METRICS.items():
                vals = [(getattr(a, attr), k) for k, (a, _s) in c.items()
                        if getattr(a, attr) is not None and getattr(a, attr) > 0
                        and (entrants[k].model_name, lang, slug(pid)) not in excluded]
                winner[m] = min(vals)[1] if vals else None
            src = winner["latency"] or winner["energy"] or winner["memory"] or min(c)
            sa, ss = c[src]

            def take(metric, field):
                k = winner[metric]
                return getattr(c[k][0], field) if k else None
            provenance = {m: ({"model": entrants[k].model_name, "attempt_id": c[k][0].id,
                               "submission_id": c[k][1].id} if k else None)
                          for m, k in winner.items()}
            for m, k in winner.items():
                if k:
                    contributions[(entrants[k].model_name, m)] += 1
            att = Attempt(
                submission_id=sub.id, problem_id=pid, code=sa.code,
                inference_trace="Golden baseline: " + ", ".join(
                    f"{m} from {p['model']} (attempt #{p['attempt_id']})"
                    for m, p in provenance.items() if p) + ". Code, tokens, and cost from "
                    f"{entrants[src].model_name} (attempt #{sa.id}).",
                trace={"golden_baseline": {"frozen_on": frozen_on.isoformat(),
                                           "sources": provenance,
                                           "code_tokens_cost_from": sa.id}},
                round_index=0, n_runs=1, n_accepted_runs=1, final_status="Accepted",
                avg_runtime_ms=take("latency", "avg_runtime_ms"),
                best_runtime_ms=take("latency", "best_runtime_ms"),
                avg_runtime_energy_j=take("energy", "avg_runtime_energy_j"),
                best_runtime_energy_j=take("energy", "best_runtime_energy_j"),
                avg_memory_mb=take("memory", "avg_memory_mb"),
                best_memory_mb=take("memory", "best_memory_mb"),
                avg_iterations=sa.avg_iterations,
                response_tokens=sa.response_tokens, prompt_tokens=sa.prompt_tokens,
                wall_clock_ms=sa.wall_clock_ms,
                cost_usd=cost_of(sa, ss) if cost_of else sa.cost_usd,
                # An old timestamp and a finished run keep every dispatch path
                # (startup recovery, scheduler) away from these rows.
                logged_at=cutoff)
            session.add(att)
            session.flush()
            session.add(Run(attempt_id=att.id, run_index=1, status_msg="Accepted",
                            runtime_ms=att.avg_runtime_ms, memory_mb=att.avg_memory_mb,
                            runtime_energy_j=att.avg_runtime_energy_j,
                            harness_id=sub.harness_id, correctness_oracle_id=oracle_id))
            for f in sums:
                if getattr(att, f) is not None:
                    sums[f].append(getattr(att, f))

        sub.total_solved = len(cells)
        sub.pass_at_1 = sub.pass_at_full = len(cells) / d["n_problems"]
        sub.pass_at_k = 1.0                     # every submitted attempt is correct
        for f, xs in sums.items():
            setattr(sub, f, _mean(xs))
        session.add(sub)
        session.commit()
        created.append({"submission_id": sub.id, "language": lang, "n_cells": len(cells)})

    return {"competition": competition, "harness": d["harness"],
            "frozen_on": frozen_on.isoformat(), "model_version": version,
            "pool": pool_models, "submissions": created,
            "contributions": {m: {metric: n for (mm, metric), n in contributions.items()
                                  if mm == m} for m in pool_models}}
