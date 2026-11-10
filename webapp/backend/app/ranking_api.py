"""HTTP layer of the efficiency rankings (method: ranking.py).

    GET /api/efficiency-rankings   win probabilities, tied leaders, Pareto levels,
                                   pass@1, and cost per attempted solution
    GET /api/head-to-head          two entrants on the cells that both solved
    POST /api/admin/golden-baseline  store the frozen golden baseline as a
                                   reference entrant (golden_baseline.py)
    POST /api/admin/rankings/refresh  recompute every stored ranking now

The efficiency rankings are stored and recomputed in the background
(ranking_store.py), because the bootstrap behind the tied leaders refits the
model thousands of times. The other responses are cached per request and data
version.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from . import app_settings
from . import ranking as rk
from .auth import current_admin
from .db import get_session
from . import integrity
from .models import (
    Agent, Attempt, Competition, CompetitionProblem, Harness, Run, SolutionReview,
    Submission, SubmissionStatus, User,
)

router = APIRouter()

# Agent kind of a reference entrant (the golden baseline): listed, never ranked.
REFERENCE_KIND = "reference"

# CodeCarbon's total energy adds a RAM estimate that it does not measure: in its
# default "machine" mode a constant power from the host's RAM size (10 W on the
# campaign host, 16 GB = 2 DIMMs x 5 W) over the whole tracker window. The
# ranking uses the RAPL package energy only, as the paper does
# (figs/scripts/gen_paper_data.py package_energy). CodeCarbon also takes no final
# reading in a window under 50 ms, so such a whole-program run has no energy.
CODECARBON_RAM_W = 10.0
MIN_ENERGY_WINDOW_MS = 50.0


def package_energy(energy_j, runtime_ms, energy_tool, whole_program):
    """Package energy of an attempt from its stored total (per call on LeetCode,
    per run on a whole-program competition); other energy tools pass through."""
    if energy_j is None or runtime_ms is None or energy_tool != "codecarbon":
        return energy_j
    if whole_program and runtime_ms < MIN_ENERGY_WINDOW_MS:
        return None
    e = energy_j - CODECARBON_RAM_W * runtime_ms / 1000.0
    return e if e > 0 else None


def first_memory_readings(session: Session, attempt_ids: list[int]) -> dict[int, float]:
    """The memory value of each attempt, as in the paper: one reading per
    solution, the reading of its first accepted run (by run index). An attempt
    with more readings (a memory re-measure writes reading i to the i-th
    accepted run) gives its first one. An attempt with no run reading is not in
    the result, so its stored average stands."""
    out: dict[int, float] = {}
    ids = sorted(set(attempt_ids))
    for i in range(0, len(ids), 900):            # stay under SQLite's variable limit
        rows = session.exec(select(Run.attempt_id, Run.run_index, Run.id, Run.memory_mb).where(
            Run.attempt_id.in_(ids[i:i + 900]), Run.status_msg == "Accepted",
            Run.memory_mb.is_not(None))).all()
        for aid, _idx, _rid, mem in sorted(rows, key=lambda r: (r[0], r[1] or 0, r[2])):
            out.setdefault(aid, mem)
    return out


def ranking_settings(session: Session | None = None,
                     competition: str | None = None) -> rk.Settings:
    """The global ranking settings, with the tie margins of `competition`
    (Competition.ranking_tie_pct) in place of the global ones it names. A
    competition margin is one percentage, or a {language: percent} dict; the
    global margin then covers the languages that the dict does not name."""
    tie_pct = {"latency": app_settings.get_float("ranking.tie_latency_pct", 1.84),
               "energy": app_settings.get_float("ranking.tie_energy_pct", 1.84),
               "memory": app_settings.get_float("ranking.tie_memory_pct", 9.83)}
    if session is not None and competition:
        comp = session.exec(select(Competition).where(Competition.slug == competition)).first()
        if comp is not None and comp.ranking_tie_pct:
            for m, v in comp.ranking_tie_pct.items():
                if m in tie_pct:
                    tie_pct[m] = ({"*": tie_pct[m], **{lang: float(x) for lang, x in v.items()}}
                                  if isinstance(v, dict) else float(v))
    return rk.Settings(
        tie_pct=tie_pct,
        bootstrap=app_settings.get_int("ranking.bootstrap_samples", 10000),
        min_cells=app_settings.get_int("ranking.min_cells_per_language", 3),
        min_problem_clusters=app_settings.get_int("ranking.min_problem_clusters", 30),
    )


def data_version(session: Session) -> tuple:
    """Changes whenever a result, an attempt, a submission, or a verdict on a
    solution changes."""
    return (session.exec(select(func.max(Run.id))).one(),
            session.exec(select(func.max(Attempt.id))).one(),
            session.exec(select(func.count(Attempt.id)).where(Attempt.final_status.is_not(None))).one(),
            session.exec(select(func.max(Submission.id))).one(),
            session.exec(select(func.count(Submission.id)).where(Submission.deleted_at.is_not(None))).one(),
            session.exec(select(func.count(SolutionReview.attempt_id))).one(),
            session.exec(select(func.max(SolutionReview.reviewed_at))).one())


def load(session: Session, competition: str, *, harness: str | None = None,
         agent: str | None = None, agent_kind: str | None = None, rounds: str | None = None,
         prompt_id: int | None = None, language: str | None = None,
         cost_of=None, created_before: datetime | None = None) -> dict:
    """Entrants and cells of one competition under the filters. `rounds` is
    "single" (one round) or "multi" (refinement); None keeps both.
    `created_before` keeps only submissions created up to that moment. A
    solution labelled invalid counts as solved (pass@1) but enters no cell, so
    it has no duel and sets no golden-baseline value."""
    comp = session.exec(select(Competition).where(Competition.slug == competition)).first()
    if comp is None:
        raise HTTPException(404, "competition not found")
    problem_ids = {cp.problem_id for cp in session.exec(
        select(CompetitionProblem).where(CompetitionProblem.competition_id == comp.id)).all()}
    invalid = integrity.invalid_attempt_ids(session)
    q = (select(Submission).where(Submission.competition_id == comp.id,
                                  Submission.status == SubmissionStatus.judged,
                                  Submission.deleted_at.is_(None)))
    if language:
        q = q.where(Submission.language == language)
    if prompt_id is not None:
        q = q.where(Submission.prompt_id == prompt_id)
    if created_before is not None:
        q = q.where(Submission.created_at <= created_before)
    if rounds == "single":
        q = q.where(Submission.n_rounds == 1)
    elif rounds == "multi":
        q = q.where(Submission.n_rounds > 1)
    subs = session.exec(q).all()

    harness_slug = {h.id: h.slug for h in session.exec(select(Harness)).all()}
    eff_harness = {s.id: harness_slug.get(s.harness_id or comp.harness_id) for s in subs}
    if not harness:
        # One harness per ranking: by default the one that scored most submissions.
        counts = Counter(h for h in eff_harness.values() if h)
        harness = counts.most_common(1)[0][0] if counts else None
    subs = [s for s in subs if eff_harness[s.id] == harness]

    agents = {a.id: a for a in session.exec(select(Agent)).all()}
    direct = next((a for a in agents.values() if a.slug == "direct"), None)

    def agent_of(s: Submission):
        return agents.get(s.agent_id) if s.agent_id is not None else direct

    if agent:
        subs = [s for s in subs if (agent_of(s).slug if agent_of(s) else "direct") == agent]
    if agent_kind:
        subs = [s for s in subs if (agent_of(s).kind if agent_of(s) else "direct") == agent_kind]

    attempts = session.exec(select(Attempt).where(
        Attempt.submission_id.in_([s.id for s in subs]))).all() if subs else []
    by_sub: dict[int, list] = defaultdict(list)
    for a in attempts:
        if a.problem_id in problem_ids:
            by_sub[a.submission_id].append(a)

    # One main submission per (entrant, language): the one that covers the
    # most problems, then the newest. As in the paper's data selection, a
    # problem it lacks comes from the latest other submission of the entrant in
    # that language with the same prompt on the same machine (a re-run that
    # completes a sweep that stopped early).
    chosen: dict[tuple[str, str], Submission] = {}
    candidates: dict[tuple[str, str], list] = defaultdict(list)
    entrants: dict[str, rk.Entrant] = {}
    for s in subs:
        ag = agent_of(s)
        a_slug = ag.slug if ag else "direct"
        key = rk.entrant_key(s.model_name, a_slug, s.n_rounds)
        if key not in entrants:
            entrants[key] = rk.Entrant(
                key=key, model_name=s.model_name, model_version=s.model_version,
                agent_slug=a_slug, agent_name=ag.name if ag else "direct",
                agent_kind=ag.kind if ag else "direct", n_rounds=s.n_rounds,
                n_samples=s.n_samples_per_problem)
        candidates[(key, s.language)].append(s)
        cover = len({a.problem_id for a in by_sub[s.id]})
        cur = chosen.get((key, s.language))
        if cur is None or (cover, s.created_at) > (
                len({a.problem_id for a in by_sub[cur.id]}), cur.created_at):
            chosen[(key, s.language)] = s

    first_memory = first_memory_readings(
        session, [a.id for atts in by_sub.values() for a in atts if a.final_status == "Accepted"])
    cells: dict[str, dict] = {m: defaultdict(dict) for m in rk.METRICS}
    finals_by_cell: dict = defaultdict(dict)       # (lang, pid) -> key -> (attempt, sub)
    invalid_by_cell: dict = defaultdict(dict)      # the same for solutions labelled invalid
    languages = sorted({lang for _, lang in chosen})
    for (key, lang), s in chosen.items():
        e = entrants[key]
        rerun = (s.n_samples_per_problem or 1) == 1
        finals = {pid: (a, s) for (_sid, pid), a in
                  rk.final_attempts(by_sub[s.id], latest=rerun).items()}
        for o in sorted(candidates[(key, lang)], key=lambda o: o.created_at):
            if o is s or o.prompt_id != s.prompt_id or o.machine_id != s.machine_id:
                continue
            for (_sid, pid), a in rk.final_attempts(by_sub[o.id], latest=rerun).items():
                if pid not in finals or finals[pid][1] is not s:
                    finals[pid] = (a, o)
        e.submission_ids.extend(sorted({s.id} | {o.id for _a, o in finals.values()}))
        e.languages.append(lang)
        e.n_cells += len(problem_ids)
        for pid, (a, src) in finals.items():
            e.n_attempted += 1
            if a.final_status != "Accepted":
                continue
            e.n_solved += 1
            if a.id in invalid:
                invalid_by_cell[(lang, pid)][key] = (a, src)
                continue
            finals_by_cell[(lang, pid)][key] = (a, src)
            for m, attr in rk.METRICS.items():
                v = getattr(a, attr)
                if m == "energy":
                    v = package_energy(v, a.avg_runtime_ms, src.energy_tool,
                                       comp.kind != "leetcode")
                elif m == "memory":
                    v = first_memory.get(a.id, v)
                if v is not None and v > 0:
                    cells[m][(lang, pid)][key] = v
        if cost_of is not None:
            # Every attempt of the main submission (re-runs were paid for too),
            # plus the attempts of each filled-in problem.
            paid = [(a, s) for a in by_sub[s.id]] + [
                (a, o) for pid, (_f, o) in finals.items() if o is not s
                for a in by_sub[o.id] if a.problem_id == pid]
            for a, src in paid:
                c = cost_of(a, src)
                if c is not None:
                    e.cost_usd += c
                    e.n_costed += 1
    entrants = {k: e for k, e in entrants.items() if e.submission_ids}
    return dict(competition=comp, harness=harness, entrants=entrants,
                cells={m: dict(c) for m, c in cells.items()}, languages=languages,
                n_problems=len(problem_ids), finals=dict(finals_by_cell),
                invalid_finals=dict(invalid_by_cell))


def _cost_function(session: Session):
    from .main import _model_pricing_lookup, _recompute_attempt_cost  # avoid a cycle
    pricing = _model_pricing_lookup(session)

    def cost_of(a: Attempt, s: Submission):
        live = _recompute_attempt_cost(a, s.model_name, s.model_version, pricing)
        return live if live is not None else a.cost_usd
    return cost_of


def efficiency_rankings(session: Session, competition: str, *, harness=None, agent=None,
                        agent_kind=None, rounds=None, prompt_id=None, language=None,
                        bootstrap: bool = True, fresh: bool = False) -> dict:
    """The stored ranking (ranking_store.py), or a new one when none is stored
    or `fresh` is set. Without the bootstrap, the ranking is computed and not
    stored."""
    filters = dict(harness=harness, agent=agent, agent_kind=agent_kind, rounds=rounds,
                   prompt_id=prompt_id, language=language)
    if not bootstrap:
        return compute_efficiency_rankings(session, competition, bootstrap=False, **filters)
    from . import ranking_store                     # avoid an import cycle
    return ranking_store.get(session, competition, fresh=fresh, **filters)


def compute_efficiency_rankings(session: Session, competition: str, *, harness=None,
                                agent=None, agent_kind=None, rounds=None, prompt_id=None,
                                language=None, bootstrap: bool = True) -> dict:
    settings = ranking_settings(session, competition)
    if not bootstrap:
        settings.bootstrap = 0
    d = load(session, competition, harness=harness, agent=agent, agent_kind=agent_kind,
             rounds=rounds, prompt_id=prompt_id, language=language,
             cost_of=_cost_function(session))
    # A reference (the golden baseline) and a partial run (fewer than a
    # third of its cells attempted, as MIN_ATTEMPTED_FRACTION on the
    # submissions leaderboard) are listed but not ranked.
    reference = {k for k, e in d["entrants"].items() if e.agent_kind == REFERENCE_KIND}
    partial = {k for k, e in d["entrants"].items()
               if e.n_attempted * 3 < e.n_cells} - reference
    keys = sorted(k for k in d["entrants"] if k not in partial | reference)
    res = rk.rank(d["cells"], keys, settings, d["n_problems"], single_language=bool(language))
    rows = []
    for k in sorted(d["entrants"]):
        e = d["entrants"][k]
        metrics = {}
        for m in rk.METRICS:
            r = res[m]
            metrics[m] = dict(rated=k in r["rated"], win_prob=r["win_prob"].get(k),
                              rating=r["ratings"].get(k),
                              tied_leader=k in r["tied_leaders"] and k in r["rated"])
        rows.append(dict(
            key=k, model_name=e.model_name, model_version=e.model_version,
            workflow=e.workflow, agent_slug=e.agent_slug, agent_name=e.agent_name,
            agent_kind=e.agent_kind, n_rounds=e.n_rounds, n_samples=e.n_samples,
            languages=sorted(e.languages), submission_ids=sorted(e.submission_ids),
            pass_at_1=e.pass_at_1, n_cells=e.n_cells, n_attempted=e.n_attempted,
            n_solved=e.n_solved, cost_per_attempt_usd=e.cost_per_attempt_usd,
            partial=k in partial, reference=k in reference,
            pareto_level=res["pareto"]["level"].get(k),
            metrics=metrics))
    for m in rk.METRICS:
        ordered = sorted((r for r in rows if r["metrics"][m]["rated"]),
                         key=lambda r: -r["metrics"][m]["win_prob"])
        for i, r in enumerate(ordered, start=1):
            r["metrics"][m]["rank"] = i
    rows.sort(key=lambda r: (not r["reference"], not r["metrics"]["latency"]["rated"],
                             -(r["metrics"]["latency"]["win_prob"] or 0)))
    summary = {m: dict(sig_pairs=res[m]["sig_pairs"], n_pairs=res[m]["n_pairs"],
                       resampled=res[m]["unit"], tied_leaders=res[m]["tied_leaders"],
                       n_cells=res[m]["n_duel_cells"], tie_pct=settings.tie_pct[m])
               for m in rk.METRICS}
    return dict(competition=competition, harness=d["harness"], language=language,
                filters=dict(agent=agent, agent_kind=agent_kind, rounds=rounds,
                             prompt_id=prompt_id),
                languages=d["languages"], n_problems=d["n_problems"],
                bootstrap=settings.bootstrap, min_cells=settings.min_cells,
                computed_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                entrants=rows, metrics=summary, pareto_edges=res["pareto"]["edges"],
                pareto_metrics=res["pareto"]["metrics"])


@router.get("/api/efficiency-rankings")
def get_efficiency_rankings(
    competition: str,
    harness: str | None = Query(None, description="harness slug; default: the one "
                                "that scored most submissions of the competition"),
    agent: str | None = Query(None, description="workflow (agent slug), e.g. direct"),
    agent_kind: str | None = Query(None, description="agent kind, e.g. agentic"),
    rounds: str | None = Query(None, pattern="^(single|multi)$"),
    prompt_id: int | None = None,
    language: str | None = Query(None, description="rank inside one language"),
    session: Session = Depends(get_session),
):
    """Paper-style efficiency ranking: Bradley-Terry win probabilities from
    per-cell duels, tied leaders, Pareto levels, pass@1, and cost."""
    return efficiency_rankings(session, competition, harness=harness, agent=agent,
                               agent_kind=agent_kind, rounds=rounds, prompt_id=prompt_id,
                               language=language)


@router.get("/api/head-to-head")
def get_head_to_head(
    competition: str,
    a: str = Query(..., description="entrant key (model|agent|rN) or model name"),
    b: str = Query(..., description="entrant key (model|agent|rN) or model name"),
    harness: str | None = None,
    language: str | None = None,
    session: Session = Depends(get_session),
):
    """Two entrants on the cells that both solved: the geometric-mean ratio of
    a's values to b's, and the shares of cells that a wins, ties, and loses."""
    settings = ranking_settings(session, competition)
    key = ("h2h", competition, a, b, harness, language, json.dumps(settings.tie_pct, sort_keys=True),
           data_version(session))

    def compute() -> dict:
        d = load(session, competition, harness=harness, language=language)

        def resolve(x: str) -> str:
            if x in d["entrants"]:
                return x
            hits = [k for k, e in d["entrants"].items() if e.model_name == x]
            direct = [k for k in hits if d["entrants"][k].agent_slug == "direct"
                      and d["entrants"][k].n_rounds == 1]
            if direct or hits:
                return (direct or hits)[0]
            raise HTTPException(404, f"no entrant {x!r} in {competition}")
        ka, kb = resolve(a), resolve(b)
        return dict(competition=competition, harness=d["harness"], language=language,
                    a=ka, b=kb, metrics={m: dict(rk.head_to_head(d["cells"][m], ka, kb,
                                                                 settings.tie_pct[m]),
                                                 tie_pct=settings.tie_pct[m])
                                         for m in rk.METRICS})
    return rk.CACHE.get_or_compute(key, compute)


@router.post("/api/admin/rankings/refresh", status_code=202)
def post_rankings_refresh(admin: User = Depends(current_admin)):
    """Recompute every stored efficiency ranking now, in the background.
    `started` is false when a recompute is already running."""
    from . import ranking_store                     # avoid an import cycle
    return {"started": ranking_store.refresh_now()}


def per_language_point_ranks(session: Session, competition: str) -> dict:
    """Per-language point estimates (no bootstrap) for the home-page bump
    charts: language -> metric -> best-first [(label, win_prob)]."""
    settings = ranking_settings(session, competition)
    key = ("bump", competition, settings.min_cells, json.dumps(settings.tie_pct, sort_keys=True),
           data_version(session))

    def compute() -> dict:
        d = load(session, competition)
        keys = sorted(k for k, e in d["entrants"].items() if e.agent_kind != REFERENCE_KIND)
        label = {k: (e.model_name if (e.agent_slug == "direct" and e.n_rounds == 1)
                     else f"{e.model_name} ({e.workflow})") for k, e in d["entrants"].items()}
        ranks: dict[str, dict] = {m: {} for m in ("latency", "energy", "memory")}
        for lang in d["languages"]:
            for m in ranks:
                cells = {c: v for c, v in d["cells"][m].items() if c[0] == lang}
                duels = rk.duel_matrices(cells, keys, settings.tie_pct[m])
                sub = rk.rated_keys(duels, keys, settings.min_cells)
                if len(sub) < 2:
                    continue
                point = rk.fit(sum(W for _, _, W in rk.restrict(duels, keys, sub)))
                wp = rk.avg_win_prob({k: float(point[i]) for i, k in enumerate(sub)})
                ranks[m][lang] = [dict(model=label[k], win_prob=wp[k])
                                  for k in sorted(sub, key=lambda k: -wp[k])]
        return dict(languages=d["languages"], metrics=list(ranks), ranks=ranks)
    return rk.CACHE.get_or_compute(key, compute)


class GoldenExclusion(BaseModel):
    """An accepted solution that never sets a golden value."""
    model: str
    language: str
    problem: str                             # title slug


class GoldenBaselineIn(BaseModel):
    competition: str
    frozen_on: date = date(2026, 10, 10)     # end of the paper's campaign
    models: list[str] | None = None          # default: every non-partial entrant
    languages: list[str] | None = None       # default: every language of the pool
    harness: str | None = None               # default: the competition's main harness
    exclude: list[GoldenExclusion] | None = None


@router.post("/api/admin/golden-baseline", status_code=201)
def post_golden_baseline(body: GoldenBaselineIn, admin: User = Depends(current_admin),
                         session: Session = Depends(get_session)):
    """Store the golden baseline of one competition as reference submissions,
    copied from the attempts of submissions created on or before `frozen_on`.
    Nothing is generated or measured. 409 when one already exists."""
    from . import golden_baseline
    try:
        return golden_baseline.build(session, body.competition, frozen_on=body.frozen_on,
                                     user_id=admin.id, models=body.models,
                                     languages=body.languages, harness=body.harness, cost_of=_cost_function(session),
                                     exclude=[(x.model, x.language, x.problem)
                                              for x in body.exclude or []])
    except ValueError as e:
        raise HTTPException(409 if "already has" in str(e) else 400, str(e))
