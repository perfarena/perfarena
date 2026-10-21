"""Efficiency rankings (ranking.py, ranking_api.py): the method of the paper.

Unit tests pin the math (Bradley-Terry fit, tie margin, Holm, Pareto with
significance, final-attempt selection). End-to-end tests build a small
competition with three models in two languages, an agentic entrant, and a
two-round entrant, and check the endpoints and their filters.
"""
import math
from types import SimpleNamespace

import numpy as np
import pytest
from sqlmodel import Session, select

from conftest import ADMIN
from app import ranking as rk
from app.db import engine
from app.models import (
    Agent, Attempt, Competition, CompetitionProblem, Harness, Problem, Submission,
    SubmissionStatus, User,
)
from app.snapshot import build_report

COMP = "rank-test"
SLUGS = ["two-sum", "add-digits", "24-game", "valid-parentheses", "reverse-integer"]


# ---- unit tests -------------------------------------------------------------------

def test_fit_two_players_matches_closed_form():
    # A beats B 3 times out of 4; with the pseudo-count of a half tie per pair
    # the strength ratio is (3 + 0.25) / (1 + 0.25).
    W = np.array([[0.0, 3.0], [1.0, 0.0]])
    r = rk.fit(W)
    assert r[0] - r[1] == pytest.approx(400 * math.log10(3.25 / 1.25), abs=1e-6)
    assert r.mean() == pytest.approx(1000)


def test_duels_tie_within_margin_and_skip_unshared_cells():
    cells = {("py", 1): {"a": 100.0, "b": 101.0},       # 1% apart: tie at a 2% margin
             ("py", 2): {"a": 100.0, "b": 150.0},       # a wins
             ("py", 3): {"a": 100.0}}                   # b failed: no duel
    duels = rk.duel_matrices(cells, ["a", "b"], margin_pct=2.0)
    W = sum(d[2] for d in duels)
    assert len(duels) == 2
    assert W[0, 1] == 1.5 and W[1, 0] == 0.5


def test_holm_step_down():
    assert rk.holm([0.001, 0.02, 0.04]) == [True, True, True]
    assert rk.holm([0.001, 0.04, 0.03]) == [True, False, False]


def test_pareto_counts_only_significant_differences():
    keys = ["a", "b", "c"]
    beats = {"latency": {"a": ["b", "c"], "b": [], "c": []},
             "memory": {"a": [], "b": ["a"], "c": []}}
    p = rk.pareto(keys, beats)
    # a beats b in latency but loses in memory: neither dominates.
    assert p["level"]["a"] == 1 and p["level"]["b"] == 1
    assert p["level"]["c"] == 2 and ["a", "c"] in p["edges"]


def test_pareto_skips_a_metric_without_data():
    # LeetCode measures no energy: the Pareto levels use latency and memory only.
    # a and b split their duels, and both beat c on every cell.
    cells = {("py", i): {"a": 10.0 if i % 2 else 20.0, "b": 20.0 if i % 2 else 10.0,
                         "c": 40.0} for i in range(24)}
    s = rk.Settings(tie_pct={"latency": 1.0, "energy": 1.0, "memory": 1.0}, bootstrap=200)
    res = rk.rank({"latency": cells, "energy": {}, "memory": cells}, ["a", "b", "c"], s,
                  n_problems=24, single_language=True)
    assert res["pareto"]["metrics"] == ["latency", "memory"]
    assert res["pareto"]["level"] == {"a": 1, "b": 1, "c": 2}


def test_final_attempt_follows_the_first_chain():
    A = lambda i, sub, prob, rnd, prev: SimpleNamespace(        # noqa: E731
        id=i, submission_id=sub, problem_id=prob, round_index=rnd, previous_attempt_id=prev)
    rows = [A(1, 9, 5, 0, None), A(2, 9, 5, 1, 1), A(3, 9, 5, 0, None), A(4, 9, 5, 1, 3)]
    assert rk.final_attempts(rows)[(9, 5)].id == 2


def test_final_attempt_takes_the_latest_rerun_when_asked():
    A = lambda i, rnd, prev: SimpleNamespace(                    # noqa: E731
        id=i, submission_id=9, problem_id=5, round_index=rnd, previous_attempt_id=prev)
    rows = [A(1, 0, None), A(2, 0, None)]           # a one-sample submission re-ran problem 5
    assert rk.final_attempts(rows)[(9, 5)].id == 1
    assert rk.final_attempts(rows, latest=True)[(9, 5)].id == 2


def test_head_to_head_ratio_and_shares():
    cells = {("py", i): {"a": 50.0, "b": 100.0} for i in range(4)}
    cells[("py", 9)] = {"a": 100.0, "b": 100.5}
    h = rk.head_to_head(cells, "a", "b", margin_pct=1.0)
    assert h["n_shared"] == 5
    assert h["a_wins"] == pytest.approx(0.8) and h["ties"] == pytest.approx(0.2)
    assert h["ratio"] == pytest.approx(math.exp((4 * math.log(0.5) + math.log(100 / 100.5)) / 5))


# ---- end-to-end ------------------------------------------------------------------

def _seed_competition():
    with Session(engine) as s:
        if s.exec(select(Competition).where(Competition.slug == COMP)).first():
            return
        harness = s.exec(select(Harness).where(Harness.slug == "leetcode")).one()
        comp = Competition(slug=COMP, name="Ranking test", harness_id=harness.id,
                           compatible_harness_slugs=["leetcode"])
        s.add(comp); s.commit(); s.refresh(comp)
        pids = [s.exec(select(Problem).where(Problem.title_slug == sl)).one().id for sl in SLUGS]
        for pid in pids:
            s.add(CompetitionProblem(competition_id=comp.id, problem_id=pid))
        agent = s.exec(select(Agent).where(Agent.slug == "rank-agent")).first()
        if agent is None:
            agent = Agent(slug="rank-agent", name="Rank Agent", kind="agentic")
            s.add(agent); s.commit(); s.refresh(agent)
        user = s.exec(select(User)).first()

        def sub(model, lang, speed, *, agent_id=None, n_rounds=1, fail=(), cost=0.01,
                solved=None):
            row = Submission(user_id=user.id, model_name=model, model_version="v1",
                             language=lang, prompt_template="rank test prompt",
                             model_params={}, status=SubmissionStatus.judged,
                             competition_id=comp.id, harness_id=harness.id,
                             agent_id=agent_id, n_rounds=n_rounds)
            s.add(row); s.commit(); s.refresh(row)
            for i, pid in enumerate(pids[:solved] if solved else pids):
                ok = SLUGS[i] not in fail
                first = Attempt(submission_id=row.id, problem_id=pid, code="x",
                                inference_trace="t", round_index=0, n_runs=1, cost_usd=cost,
                                final_status=("Wrong Answer" if n_rounds > 1 else
                                              ("Accepted" if ok else "Wrong Answer")))
                if first.final_status == "Accepted":
                    first.avg_runtime_ms = speed * (i + 1)
                    first.avg_runtime_energy_j = speed * (i + 1) / 10
                    first.avg_memory_mb = 10.0
                s.add(first); s.commit(); s.refresh(first)
                if n_rounds > 1:       # the second round fixes the first
                    s.add(Attempt(submission_id=row.id, problem_id=pid, code="y",
                                  inference_trace="t", round_index=1, n_runs=1,
                                  previous_attempt_id=first.id, final_status="Accepted",
                                  cost_usd=cost, avg_runtime_ms=speed * (i + 1),
                                  avg_runtime_energy_j=speed * (i + 1) / 10,
                                  avg_memory_mb=10.0))
            s.commit()

        for lang in ("python3", "cpp"):
            sub("fast-llm", lang, 10.0)
            sub("mid-llm", lang, 20.0)
            sub("slow-llm", lang, 40.0, fail=("24-game",))
        sub("fast-llm", "python3", 5.0, agent_id=agent.id)          # agentic workflow
        sub("refine-llm", "python3", 15.0, n_rounds=2)                # two rounds
        sub("sparse-llm", "cpp", 1.0, solved=2)                       # 2 cpp problems only


@pytest.fixture(scope="module", autouse=True)
def _data():
    _seed_competition()


def _rows(client, **params):
    r = client.get("/api/efficiency-rankings", params={"competition": COMP, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_rankings_order_and_pass_at_1(client):
    out = _rows(client, agent="direct", rounds="single")
    by = {e["model_name"]: e for e in out["entrants"]}
    lat = {m: by[m]["metrics"]["latency"]["win_prob"] for m in ("fast-llm", "mid-llm", "slow-llm")}
    assert lat["fast-llm"] > lat["mid-llm"] > lat["slow-llm"]
    rank = {m: by[m]["metrics"]["latency"]["rank"] for m in lat}
    assert rank["fast-llm"] < rank["mid-llm"] < rank["slow-llm"]
    assert by["fast-llm"]["metrics"]["latency"]["tied_leader"]
    assert by["slow-llm"]["pass_at_1"] == pytest.approx(8 / 10)
    assert by["fast-llm"]["cost_per_attempt_usd"] == pytest.approx(0.01)
    assert out["harness"] == "leetcode" and out["n_problems"] == 5


def test_workflow_filters_and_entrants(client):
    keys = {e["key"] for e in _rows(client)["entrants"]}
    assert "fast-llm|rank-agent|r1" in keys and "fast-llm|direct|r1" in keys
    assert "refine-llm|direct|r2" in keys
    agentic = _rows(client, agent_kind="agentic")["entrants"]
    assert [e["key"] for e in agentic] == ["fast-llm|rank-agent|r1"]
    single = {e["key"] for e in _rows(client, rounds="single")["entrants"]}
    assert "refine-llm|direct|r2" not in single


def test_multi_round_entrant_uses_the_final_round(client):
    e = next(e for e in _rows(client)["entrants"] if e["key"] == "refine-llm|direct|r2")
    assert e["pass_at_1"] == pytest.approx(1.0)       # round 0 failed, round 1 passed
    assert e["cost_per_attempt_usd"] == pytest.approx(0.02)   # both rounds count


def test_language_view_hides_sparse_entrants(client):
    cpp = {e["model_name"]: e for e in _rows(client, language="cpp")["entrants"]}
    assert cpp["sparse-llm"]["metrics"]["latency"]["rated"] is False
    pooled = {e["model_name"]: e for e in _rows(client, agent="direct")["entrants"]}
    assert pooled["sparse-llm"]["metrics"]["latency"]["rated"] is True


def test_partial_runs_are_listed_but_not_ranked(client):
    # sparse-llm attempted 2 of its 5 cpp cells (40%), above the one-third bar.
    e = next(e for e in _rows(client)["entrants"] if e["model_name"] == "sparse-llm")
    assert e["partial"] is False and e["n_attempted"] == 2


def test_head_to_head_endpoint(client):
    r = client.get("/api/head-to-head", params={"competition": COMP, "a": "fast-llm",
                                                "b": "mid-llm"})
    assert r.status_code == 200, r.text
    lat = r.json()["metrics"]["latency"]
    assert lat["n_shared"] == 10 and lat["ratio"] == pytest.approx(0.5)
    assert lat["a_wins"] == pytest.approx(1.0)


def test_bump_chart_uses_win_probabilities(client):
    r = client.get("/api/efficiency-ranks", params={"competition": COMP})
    assert r.status_code == 200, r.text
    py = r.json()["ranks"]["latency"]["python3"]
    assert py[0]["model"] == "fast-llm (Rank Agent)"          # 5 ms beats 10 ms
    assert all(0 < e["win_prob"] < 1 for e in py)
    assert r.json()["metrics"] == ["latency", "energy", "memory"]
    assert "sparse-llm" not in [e["model"] for e in r.json()["ranks"]["latency"].get("cpp", [])]


def test_leaderboard_workflow_filter(client):
    r = client.get("/api/rankings", params={"competition": COMP, "agent": "rank-agent"})
    assert r.status_code == 200, r.text
    assert {e["agent_slug"] for e in r.json()} <= {"rank-agent"}


def test_rerun_and_fill_in_follow_the_paper():
    # The main sweep of fill-llm stopped after two problems and re-ran the
    # first one; a later submission with the same prompt filled in the third.
    from datetime import datetime
    from app.ranking_api import load
    with Session(engine) as s:
        harness = s.exec(select(Harness).where(Harness.slug == "leetcode")).one()
        comp = Competition(slug="fill-test", name="Fill test", harness_id=harness.id,
                           compatible_harness_slugs=["leetcode"])
        s.add(comp); s.commit(); s.refresh(comp)
        pids = [s.exec(select(Problem).where(Problem.title_slug == sl)).one().id
                for sl in SLUGS[:3]]
        for pid in pids:
            s.add(CompetitionProblem(competition_id=comp.id, problem_id=pid))
        user = s.exec(select(User)).first()

        def sub(created, runs):
            row = Submission(user_id=user.id, model_name="fill-llm", model_version="v1",
                             language="python3", prompt_template="p", model_params={},
                             status=SubmissionStatus.judged, competition_id=comp.id,
                             harness_id=harness.id, created_at=created)
            s.add(row); s.commit(); s.refresh(row)
            for pid, ms in runs:
                s.add(Attempt(submission_id=row.id, problem_id=pid, code="x", inference_trace="t",
                              round_index=0, n_runs=1, final_status="Accepted",
                              avg_runtime_ms=ms, cost_usd=0.01))
                s.commit()
            return row.id
        main = sub(datetime(2026, 10, 1), [(pids[0], 50.0), (pids[1], 20.0), (pids[0], 10.0)])
        fill = sub(datetime(2026, 10, 2), [(pids[2], 30.0)])
        d = load(s, "fill-test")
    e = d["entrants"]["fill-llm|direct|r1"]
    assert e.submission_ids == [main, fill] and e.n_attempted == 3
    assert d["cells"]["latency"][("python3", pids[0])]["fill-llm|direct|r1"] == 10.0
    assert d["cells"]["latency"][("python3", pids[2])]["fill-llm|direct|r1"] == 30.0


def test_snapshot_keeps_the_efficiency_ranking():
    with Session(engine) as s:
        report = build_report(s)
    comp = next(c for c in report["efficiency"] if c["competition"] == COMP)
    assert comp["entrants"] and "latency" in comp["entrants"][0]["metrics"]
