"""End-to-end parity: the client scripts (webapp/examples) and the SDK run the
same experiment against the same live backend, fake LLM, and fake runner,
and must produce the same prompts, submissions, and attempts. The scripts
run as real subprocesses, unchanged, so they are an independent reference."""
from __future__ import annotations

import os
import subprocess
import sys
from collections import defaultdict

import pytest
from sqlmodel import Session, select

from app.db import engine
from app.models import Attempt, Problem, Submission

from conftest import ADMIN_KEY, EXAMPLES, make_oracles
from fakes import BAD, GOOD, FakeOllama, FakeRunner, fenced, judge_by_marker
from perfarena import Arena, RunSpec, Settings

REASONING = "Iterate once and accumulate the answer; O(n) time, O(1) space."


def _answer(messages, model) -> str:
    """two-sum fails until the prompt carries the previous verdict."""
    user = messages[-1]["content"] + messages[0]["content"]
    if "two-sum" in user and "PREVIOUS ATTEMPT" not in user:
        return fenced(BAD)
    return fenced(GOOD)


@pytest.fixture
def world(live_url):
    runner = FakeRunner(judge=judge_by_marker)
    llm = FakeOllama(_answer, thinking=lambda m, model: REASONING,
                     models=tuple(f"{k}-{w}" for k in ("sweep", "single", "refine")
                                  for w in ("script", "sdk")))
    harness, oracle = make_oracles(runner)
    yield live_url, llm, harness, oracle
    runner.stop()
    llm.stop()


def _script(name: str, *args: str, live: str, tmp_path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ARENA_", "PERFARENA_"))}
    env.update({"ARENA_API_KEY": ADMIN_KEY, "ARENA_BASE_URL": live,
                "ARENA_OLLAMA_RETRIES": "0"})
    p = subprocess.run([sys.executable, str(EXAMPLES / name), "--base-url", live, *args],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    return p


def _sdk(live: str, tmp_path, **spec) -> None:
    arena = Arena(settings=Settings(base_url=live, api_key=ADMIN_KEY))
    res = arena.run(RunSpec.from_dict(spec), ledger=tmp_path / "sdk-ledger.json")
    assert res.failed == 0, res.cells


def _prompts_by_model(llm: FakeOllama) -> dict[str, list]:
    out = defaultdict(list)
    for r in llm.requests:
        out[r["model"]].append((r["messages"][0]["content"], r["messages"][1]["content"],
                                r["options"], r.get("think")))
    return out


def _submission(model: str) -> Submission:
    with Session(engine) as s:
        return s.exec(select(Submission).where(Submission.model_name == model)).one()


def _attempts(model: str) -> list[dict]:
    with Session(engine) as s:
        sub = s.exec(select(Submission).where(Submission.model_name == model)).one()
        rows = s.exec(select(Attempt, Problem).join(Problem, Problem.id == Attempt.problem_id)
                      .where(Attempt.submission_id == sub.id).order_by(Attempt.id)).all()
        out = []
        for a, p in rows:
            trace = {k: v for k, v in (a.trace or {}).items()
                     if k not in ("created_at", "wall_clock_ms", "model")}
            out.append({"slug": p.title_slug, "round": a.round_index, "code": a.code,
                        "summary": a.inference_trace, "prefailed": a.prefailed_reason,
                        "verdict": a.final_status, "chained": a.previous_attempt_id is not None,
                        "response_tokens": a.response_tokens, "prompt_tokens": a.prompt_tokens,
                        "cost_usd": a.cost_usd, "digest": a.model_digest, "trace": trace})
        return out


_SUB_FIELDS = ("language", "prompt_template", "prompt_id", "model_params",
               "n_samples_per_problem", "n_rounds", "include_hints", "optimization_target",
               "stop_on_accept", "competition_id", "harness_id", "correctness_oracle_id",
               "status", "total_solved", "total_problems")


def _diff(a: list[dict], b: list[dict]) -> list:
    out = []
    for x, y in zip(a, b):
        for k in x:
            if k == "trace":
                out += [("trace." + t, x[k].get(t), y[k].get(t)) for t in set(x[k]) | set(y[k])
                        if x[k].get(t) != y[k].get(t)]
            elif x[k] != y[k]:
                out.append((k, x[k], y[k]))
    return out


def _same_submission(a: str, b: str) -> None:
    sa, sb = _submission(a), _submission(b)
    assert {f: getattr(sa, f) for f in _SUB_FIELDS} == {f: getattr(sb, f) for f in _SUB_FIELDS}


def test_matrix_sweep_and_sdk_agree(world, tmp_path):
    live, llm, harness, oracle = world
    _script("matrix_sweep.py", "--provider", "ollama", "--ollama-url", llm.url,
            "--model", "sweep-script", "--prompts", "experiment-system",
            "--languages", "python3", "--slugs", "two-sum,add-digits",
            "--harness", harness, "--correctness-oracle", oracle, "--rounds", "2",
            "--temperature", "0", "--poll-seconds", "60", "--output",
            str(tmp_path / "sweep_log.json"), live=live, tmp_path=tmp_path)
    _sdk(live, tmp_path, model={"name": "sweep-sdk", "provider": "ollama",
                                "endpoint": llm.url, "params": {"temperature": 0.0}},
         prompts=["experiment-system"], languages=["python3"],
         problems=["two-sum", "add-digits"], oracles={"performance": harness,
                                                      "correctness": oracle},
         rounds={"n": 2, "stop_on_accept": True}, poll_timeout_s=60)
    sent = _prompts_by_model(llm)
    assert len(sent["sweep-script"]) == 3                 # two-sum twice, add-digits once
    assert sent["sweep-sdk"] == sent["sweep-script"]
    assert _diff(_attempts("sweep-sdk"), _attempts("sweep-script")) == []
    assert [a["verdict"] for a in _attempts("sweep-sdk")] == [
        "Wrong Answer", "Accepted", "Accepted"]
    assert {a["digest"] for a in _attempts("sweep-sdk")} == {"ollama:sha256:feedface"}
    _same_submission("sweep-sdk", "sweep-script")


def test_submit_script_and_sdk_agree(world, tmp_path):
    live, llm, harness, oracle = world
    _script("submit_with_local_model.py", "--provider", "ollama", "--ollama-url", llm.url,
            "--model", "single-script", "--slug", "add-digits", "--language", "python3",
            "--temperature", "0", "--harness", harness, "--correctness-oracle", oracle,
            "--poll-seconds", "60", live=live, tmp_path=tmp_path)
    _sdk(live, tmp_path, model={"name": "single-sdk", "provider": "ollama",
                                "endpoint": llm.url, "params": {"temperature": 0.0}},
         languages=["python3"], problems=["add-digits"],
         oracles={"performance": harness, "correctness": oracle}, poll_timeout_s=60)
    sent = _prompts_by_model(llm)
    assert sent["single-sdk"] == sent["single-script"]
    assert _diff(_attempts("single-sdk"), _attempts("single-script")) == []
    _same_submission("single-sdk", "single-script")


def test_iterative_refine_and_sdk_agree_after_round_zero(world, tmp_path):
    """iterative_refine fills {{round_instruction}} in round 0 ("This is
    round 1 of N"); matrix_sweep and the SDK leave it empty there. Every
    later round, and every attempt, must match."""
    live, llm, harness, oracle = world
    _script("iterative_refine.py", "--provider", "ollama", "--ollama-url", llm.url,
            "--model", "refine-script", "--slug", "two-sum", "--language", "python3",
            "--n-rounds", "2", "--temperature", "0", "--harness", harness,
            "--correctness-oracle", oracle, "--poll-seconds", "60",
            live=live, tmp_path=tmp_path)
    _sdk(live, tmp_path, model={"name": "refine-sdk", "provider": "ollama",
                                "endpoint": llm.url, "params": {"temperature": 0.0}},
         prompts=["experiment-system"], languages=["python3"], problems=["two-sum"],
         oracles={"performance": harness, "correctness": oracle},
         rounds={"n": 2, "stop_on_accept": False}, poll_timeout_s=60)
    sent = _prompts_by_model(llm)
    (s0, u0, *_), (s1, u1, *_) = sent["refine-script"]
    (d0, v0, *_), (d1, v1, *_) = sent["refine-sdk"]
    assert "This is round 1 of 2." in s0 + u0 and "This is round 1 of 2." not in d0 + v0
    assert (s1, u1) == (d1, v1)
    keep = ("slug", "round", "code", "prefailed", "verdict", "chained")
    assert ([{k: a[k] for k in keep} for a in _attempts("refine-sdk")]
            == [{k: a[k] for k in keep} for a in _attempts("refine-script")])
