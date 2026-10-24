"""Arena.run end to end: the real backend, real dispatch to a fake runner,
and a scripted provider."""
from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.db import engine
from app.models import Attempt, Submission

from conftest import make_oracles
from fakes import BAD, GOOD, FakeRunner, fenced
from perfarena import PolicyMismatch, RunSpec
from perfarena.errors import ProviderError
from perfarena.llm import FakeProvider
from perfarena.runs import Collector, EventBus, GenerationCache, Ledger, cache_salt


def _spec(oracles, **kw) -> RunSpec:
    harness, oracle = oracles
    data = {"model": {"name": kw.pop("model", "model-a"), "provider": "fake",
                      "params": {"temperature": 0.0}},
            "oracles": {"performance": harness, "correctness": oracle},
            "languages": ["python3"], "problems": ["two-sum", "add-digits"],
            "recovery_wait_s": 0, "poll_timeout_s": 30}
    data.update(kw)
    return RunSpec.from_dict(data)


def _run(arena, spec, tmp_path, provider, *, ledger="ledger.json", **kw):
    col = Collector()
    res = arena.run(spec, ledger=tmp_path / ledger, events=EventBus(col),
                    provider_factory=lambda m: provider, sleep=lambda s: None, **kw)
    return res, col


def _attempts(sid: int) -> list[Attempt]:
    with Session(engine) as s:
        return s.exec(select(Attempt).where(Attempt.submission_id == sid)
                      .order_by(Attempt.id)).all()


def test_single_shot(arena, oracles, runner, tmp_path):
    prov = FakeProvider(fenced(GOOD), reasoning="sum the range with one loop, O(n)")
    res, col = _run(arena, _spec(oracles), tmp_path, prov)
    assert (res.completed, res.failed, res.skipped) == (2, 0, 0)
    (sid,) = res.submissions.values()
    atts = _attempts(sid)
    assert [a.final_status for a in atts] == ["Accepted", "Accepted"]
    t = atts[0].trace
    assert t["provider"] == "fake" and t["reasoning"] == "sum the range with one loop, O(n)"
    assert "Solve **two-sum**" in t["prompt"] and t["raw_response"].startswith("Here you go")
    assert atts[0].inference_trace == "sum the range with one loop, O(n)"
    with Session(engine) as s:
        sub = s.get(Submission, sid)
        assert (sub.n_rounds, sub.n_samples_per_problem, sub.model_version) == (1, 1, "fake")
    assert runner.n("/validate") == 2 and runner.n("/measure") == 2
    assert {"run_started", "cell_started", "generated", "submitted", "verdict",
            "cell_done", "run_done"} <= set(col.kinds())


def test_resume_and_covered_cells(arena, oracles, tmp_path):
    prov = FakeProvider(fenced(GOOD))
    spec = _spec(oracles, model="model-b")
    first, _ = _run(arena, spec, tmp_path, prov)
    again, col = _run(arena, spec, tmp_path, prov)
    assert again.completed == 0 and col.events[0].data["skipped"] == 2
    fresh, _ = _run(arena, spec, tmp_path, prov, ledger="other.json")
    assert fresh.completed == 2
    assert {c["reason"] for c in fresh.cells} == {"already_in_existing_submission"}
    assert len(prov.requests) == 2                         # only the first run generated
    assert fresh.submissions == first.submissions          # found, not created


def test_rounds_feed_back_the_verdict(arena, oracles, tmp_path):
    prov = FakeProvider([fenced(BAD), fenced(GOOD)])
    spec = _spec(oracles, model="model-c", problems=["two-sum"], prompts=["experiment-system"],
                 rounds={"n": 3, "stop_on_accept": True})
    res, _ = _run(arena, spec, tmp_path, prov)
    (cell,) = res.cells
    assert cell["solved_at_round"] == 1 and cell["rounds_used"] == 2
    atts = _attempts(cell["submission_id"])
    assert [(a.round_index, a.final_status) for a in atts] == [
        (0, "Wrong Answer"), (1, "Accepted")]
    assert atts[1].previous_attempt_id == atts[0].id
    second_prompt = prov.requests[1].user_prompt + prov.requests[1].system_prompt
    assert "PREVIOUS ATTEMPT — LEETCODE JUDGE FEEDBACK" in second_prompt
    assert "VERDICT: Wrong Answer" in second_prompt and "return -1" in second_prompt
    assert "This is round 2 of 3." in second_prompt


def test_refine_without_stop_runs_every_round(arena, oracles, tmp_path):
    spec = _spec(oracles, model="model-d", problems=["two-sum"],
                 rounds={"n": 3, "stop_on_accept": False})
    res, _ = _run(arena, spec, tmp_path, FakeProvider(fenced(GOOD)))
    assert res.cells[0]["rounds_used"] == 3


def test_k_samples_spread_temperatures(arena, oracles, tmp_path):
    prov = FakeProvider(fenced(GOOD))
    spec = _spec(oracles, model="model-e", problems=["two-sum"], sampling={"k": 3})
    res, _ = _run(arena, spec, tmp_path, prov)
    assert [r.params["temperature"] for r in prov.requests] == [0.0, 0.25, 0.5]
    atts = _attempts(res.cells[0]["submission_id"])
    assert len(atts) == 3 and len(res.cells[0]["samples"]) == 3


def test_wrong_language_is_a_prefailed_attempt(arena, oracles, runner, tmp_path):
    java = fenced("public class Solution { public int f() { return 1; } }", "java")
    res, _ = _run(arena, _spec(oracles, model="model-f", problems=["two-sum"]), tmp_path,
                  FakeProvider(java))
    (cell,) = res.cells
    assert cell["status"] == "failed" and cell["reason"] == "wrong_language_extracted"
    assert cell["transient"] is False and runner.n("/measure") == 0
    (a,) = _attempts(cell["submission_id"])
    assert a.prefailed_reason == "wrong_language_extracted"


class _Raises(FakeProvider):
    def __init__(self, err):
        super().__init__("")
        self.err = err

    def complete(self, req):
        self.requests.append(req)
        raise self.err


@pytest.mark.parametrize("err, verdict, transient", [
    (ProviderError("bad request", provider="fake", status=400), "generation_error", False),
    (ProviderError("too slow", provider="fake", timeout=True), "generation_timeout", False),
    (ProviderError("HTTP 503", provider="fake", status=503, transient=True), None, True),
])
def test_generation_failures(arena, oracles, tmp_path, err, verdict, transient):
    model = f"model-g{verdict or 'x'}"
    res, _ = _run(arena, _spec(oracles, model=model, problems=["two-sum"]), tmp_path,
                  _Raises(err))
    (cell,) = res.cells
    assert cell["status"] == "failed" and cell["transient"] is transient
    if verdict:
        (a,) = _attempts(cell["submission_id"])
        assert a.prefailed_reason == verdict and a.code == ""
        assert a.trace["provider_meta"]["failure"] == verdict
    else:
        assert cell["submission_id"] is None             # nothing submitted, retried later
    led = Ledger(tmp_path / "ledger.json")
    key = next(iter(led.items()))
    assert led.should_skip(key) is (not transient)


def test_runner_down_is_dispatch_failed(arena, tmp_path):
    down = FakeRunner()
    down.down = True
    oracles = make_oracles(down)
    res, col = _run(arena, _spec(oracles, model="model-h", problems=["two-sum"]),
                    tmp_path, FakeProvider(fenced(GOOD)))
    (cell,) = res.cells
    assert cell["status"] == "dispatch_failed" and "unreachable" in cell["warning"]
    assert "dispatch_failed" in col.kinds()
    led = Ledger(tmp_path / "ledger.json")
    assert not led.should_skip(next(iter(led.items())))   # retried next run
    down.stop()


def test_replica_policy_is_checked_not_changed(arena, oracles, tmp_path):
    spec = _spec(oracles, replicas={"performance": "split_repeats"})
    with pytest.raises(PolicyMismatch, match="uses replica policy 'primary'"):
        _run(arena, spec, tmp_path, FakeProvider(fenced(GOOD)))
    ok = _spec(oracles, model="model-i", problems=["two-sum"],
               replicas={"performance": "primary"})
    assert _run(arena, ok, tmp_path, FakeProvider(fenced(GOOD)))[0].completed == 1


def test_cache_is_shared_with_matrix_sweep(arena, oracles, tmp_path):
    """A generation cached by matrix_sweep's functions is reused by the SDK
    (same directory layout, key, and salt), with no model call."""
    import argparse
    import matrix_sweep as ms
    cache_dir = tmp_path / "sweep_cache"
    args = argparse.Namespace(target=None, hints=False, temperature=0.0, rounds=1,
                              output=tmp_path / "sweep_log.json")
    key = ms._tuple_key("model-j", "inline", "python3", "two-sum")
    ms._save_cached_code(args.output, key, GOOD, {"provider": "fake", "model": "model-j",
                                                   "raw_response": fenced(GOOD)},
                         ms._cache_salt(args))
    assert ms._cache_salt(args) == cache_salt(target=None, hints=False, temperature=0.0,
                                              rounds=1)
    prov = FakeProvider(fenced(BAD))
    res, col = _run(arena, _spec(oracles, model="model-j", problems=["two-sum"]), tmp_path,
                    prov, cache=GenerationCache(cache_dir))
    assert prov.requests == [] and res.cells[0]["verdict"] == "Accepted"
    assert any(e.kind == "generated" and e.data["cached"] for e in col.events)


def test_clbg_problem_gets_the_program_shape(arena, oracles, tmp_path):
    prov = FakeProvider(fenced("import sys\nprint(int(sys.argv[1]))"))
    _run(arena, _spec(oracles, model="model-k", problems=["spin"]), tmp_path, prov)
    assert "COMPLETE, self-contained Python 3 program" in prov.requests[0].system_prompt


def test_missing_code_snippet_skips(arena, oracles, tmp_path):
    spec = _spec(oracles, model="model-l", problems=["two-sum"], languages=["rust"],
                 prompts=["experiment-system-with-shape"])
    res, _ = _run(arena, spec, tmp_path, FakeProvider(fenced(GOOD)))
    assert res.skipped == 1 and res.cells[0]["reason"] == "no_code_snippet_for_language"


def test_new_submission_mode(arena, oracles, tmp_path):
    spec = _spec(oracles, model="model-m", problems=["two-sum"], new_submission=True)
    a, _ = _run(arena, spec, tmp_path, FakeProvider(fenced(GOOD)), ledger="a.json")
    b, _ = _run(arena, spec, tmp_path, FakeProvider(fenced(GOOD)), ledger="b.json")
    assert a.submissions != b.submissions


def test_non_admin_runs_fail_cleanly(user_arena, oracles, tmp_path):
    res, _ = _run(user_arena, _spec(oracles, model="model-n", problems=["two-sum"]),
                  tmp_path, FakeProvider(fenced(GOOD)))
    assert res.failed == 1 and "403" in res.cells[0]["reason"]


def test_submit_solution_and_wait(arena, oracles):
    harness, oracle = oracles
    out = arena.submit_solution("two-sum", "python3", GOOD, model="agent-x",
                                harness=harness, correctness=oracle, wait_s=30)
    assert out["state"]["verdict"] == "Accepted" and out["state"]["terminal"]
    again = arena.submit_solution("add-digits", "python3", GOOD, model="agent-x",
                                  harness=harness, correctness=oracle)
    assert again["submission_id"] == out["submission_id"]
