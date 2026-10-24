"""Parity with the client scripts, function by function: the same inputs go
through the scripts' code (webapp/examples) and the SDK, and must give the
same result. The scripts stay the reference implementation."""
from __future__ import annotations

import json
import os
import random
import sqlite3

import httpx
import pytest

import iterative_refine as ir
import matrix_sweep as ms
import submit_with_local_model as sw
from conftest import CLBG_CONTENT
from perfarena import codegen, prompts
from perfarena.api.resources import find_matching_submission
from perfarena.generate import Generator
from perfarena.languages import LANGUAGE_DISPLAY, MEASURABLE_LANGUAGES
from perfarena.llm import FakeProvider
from perfarena.llm.ollama import OllamaProvider
from perfarena.llm.openai import OpenAIProvider, is_reasoning_model
from perfarena.pricing import OPENAI_PRICING_USD_PER_1M, PriceBook
from perfarena.prompts import PromptBuilder, RoundState
from perfarena.runs.cells import interleave_by_level

LANGS = sorted(LANGUAGE_DISPLAY)


def test_tables_match():
    assert LANGUAGE_DISPLAY == sw.LANGUAGE_DISPLAY
    assert MEASURABLE_LANGUAGES == sw.MEASURABLE_LANGUAGES
    assert codegen.LANGUAGE_FINGERPRINTS == sw._LANGUAGE_FINGERPRINTS
    assert prompts.CLBG_ARGV_HINT == sw._CLBG_ARGV_HINT
    assert prompts.CLBG_JAVA_CLASS == sw._CLBG_JAVA_CLASS
    assert OPENAI_PRICING_USD_PER_1M == sw.OPENAI_PRICING_USD_PER_1M


RAW_CASES = [
    "```python\nclass Solution:\n    pass\n```",
    "<think>use ```cpp\nwrong()\n```</think>\n```python\nprint(1)\n```",
    "text\n```\nbare fence\n```\nmore",
    "no fence at all\n  x = 1",
    "```py\nfirst\n```\n```python\nsecond\n```",
    "```php\n<?php\ndeclare(strict_types=1);\nfunction f() {}\n?>\n```",
    "```php\nfunction f() { return 1; }\n```",
    "```java\npublic class Main {}\n```\nPERFARENA-FLAGS: -O3 -march=native\n"
    "> PERFARENA-FLAGS: -O2 ; rm -rf /",
]


@pytest.mark.parametrize("raw", RAW_CASES)
@pytest.mark.parametrize("lang", ["python3", "php", "cpp", "java"])
@pytest.mark.parametrize("clbg", [False, True])
def test_extraction(raw, lang, clbg):
    fence = LANGUAGE_DISPLAY[lang][1]
    want = sw._normalize_code_for_lc(
        sw._extract_code(sw.re.sub(r"<think>.*?</think>", "", raw, flags=sw.re.DOTALL),
                         fence_lang=fence), lang, clbg)
    got = codegen.CodePipeline().run(raw, language=lang, fence=fence, is_clbg=clbg)
    assert got == want
    assert codegen.looks_like(got, lang) == sw._looks_like(got, lang)
    assert codegen.extract_compile_flags(raw) == sw.extract_compile_flags(raw)
    flags = codegen.extract_compile_flags(raw)
    assert codegen.sanitize_compile_flags(flags) == sw.sanitize_compile_flags(flags)


def _db_corpus(n: int = 3000):
    """Real model answers from a DB copy (opt-in: PERFARENA_PARITY_DB)."""
    path = os.environ.get("PERFARENA_PARITY_DB")
    if not path:
        return []
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = db.execute(
        "select s.language, a.trace, p.content from attempt a join submission s "
        "on s.id = a.submission_id join problem p on p.id = a.problem_id "
        "where a.trace is not null order by a.id").fetchall()
    random.Random(7).shuffle(rows)
    return [(lang, (json.loads(t) or {}).get("raw_response") or "", c)
            for lang, t, c in rows[:n] if lang in LANGUAGE_DISPLAY]


@pytest.mark.skipif(not os.environ.get("PERFARENA_PARITY_DB"),
                    reason="set PERFARENA_PARITY_DB to a copy of arena.db")
def test_extraction_on_real_answers():
    corpus = _db_corpus()
    assert len(corpus) > 100
    for lang, raw, content in corpus:
        fence, clbg = LANGUAGE_DISPLAY[lang][1], sw._is_clbg_statement(content or "")
        want = sw._normalize_code_for_lc(sw._extract_code(
            sw.re.sub(r"<think>.*?</think>", "", raw, flags=sw.re.DOTALL), fence), lang, clbg)
        got = codegen.CodePipeline().run(raw, language=lang, fence=fence, is_clbg=clbg)
        assert got == want
        assert codegen.looks_like(got, lang) == sw._looks_like(got, lang)


def _registry_prompts():
    from app.main import _DEFAULT_PROMPTS
    return [{"id": i, **p} for i, p in enumerate(_DEFAULT_PROMPTS, start=1)]


def _script_prompts(prompt, problem, lang, extras):
    """The (system, user) pair the scripts' Ollama provider puts on the wire."""
    sent = {}

    def fake_post(url, body, timeout):
        sent["messages"] = body["messages"]
        return 200, {"message": {"content": "```python\nx = 1\n```"}}
    orig = sw._ollama_post
    sw._ollama_post = fake_post
    try:
        sw._ollama_generate_impl("http://x", "m", problem["content"], problem["title_slug"],
                                 {"temperature": 0.0}, lang, registry_prompt=prompt,
                                 extras=extras)
    finally:
        sw._ollama_post = orig
    return sent["messages"][0]["content"], sent["messages"][1]["content"]


PROBLEM = {"title_slug": "two-sum", "content": "Find two numbers.",
           "hints": '["hash map", "one pass"]',
           "code_snippets": {"python3": "class Solution:\n    def twoSum(self): ..."}}
CLBG = {"title_slug": "fannkuch-redux", "content": CLBG_CONTENT, "hints": None}
HISTORY = [{"round_index": 0, "code": "def f(): return 1",
            "judge": {"status": "Wrong Answer", "fail_input": "[1,2]",
                      "fail_expected": "3", "fail_got": "1", "fail_message": "bad"}}]


@pytest.mark.parametrize("lang", ["python3", "java", "php", "rust"])
@pytest.mark.parametrize("target", [None, "latency", "memory"])
@pytest.mark.parametrize("hints", [False, True])
def test_prompts_on_the_wire(lang, target, hints):
    for prompt in [None, *_registry_prompts()]:
        for problem in (PROBLEM, CLBG):
            for r, history in ((0, []), (1, HISTORY)):
                fence = LANGUAGE_DISPLAY[lang][1]
                extras = {
                    "{{optimization_directive}}": sw.build_optimization_directive(target),
                    "{{hints_block}}": sw.build_hints_block(hints, problem.get("hints")),
                    "{{code_template}}": sw.build_code_template_block(
                        problem.get("code_snippets"), lang),
                    "{{round_instruction}}": ir.build_round_instruction(r, 3) if r else "",
                    "{{rounds_history}}": ir.build_rounds_history(history, target, fence),
                }
                want = _script_prompts(prompt, problem, lang, extras)
                got = PromptBuilder(target=target, hints=hints).build(
                    prompt, problem, lang, RoundState(r, 3, history))
                assert got == want, (prompt and prompt["name"], problem["title_slug"], r)


def test_judge_distillation_and_feedback():
    att = {"status": "Wrong Answer", "accepted": False, "runs": [
        {"error_log": None},
        {"error_log": {"error_message": "boom", "last_testcase": "[3]",
                       "expected_output": "1", "code_output": "2"}}]}
    assert prompts.distill_judge(att) == ir.distill_judge_result(att)
    ok = {"status": "Accepted", "accepted": True, "avg_runtime_ms": 3.5,
          "avg_memory_mb": 9, "avg_runtime_percentile": 80.2, "avg_memory_percentile": 40}
    assert prompts.distill_judge(ok) == ir.distill_judge_result(ok)
    for target in (None, "latency", "memory"):
        hist = [{"round_index": 0, "code": "x = 1\n", "judge": ir.distill_judge_result(ok)}]
        assert prompts.rounds_history(hist, target, "python") == \
            ir.build_rounds_history(hist, target, "python")
    for total in (1, 2, 5):
        for r in range(total):
            assert prompts.round_instruction(r, total) == ir.build_round_instruction(r, total)


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini-2025-08-07", "gpt-5-chat-latest",
                                   "o3", "o4-mini", "gpt-4.1-mini", "gpt-4o", "unknown-x",
                                   "o1-pro", "gpt-5.0-chat"])
def test_cost_and_reasoning_detection(model):
    assert is_reasoning_model(model) == sw._is_reasoning_model(model)
    for usage in ({"prompt_tokens": 1200, "completion_tokens": 300},
                  {"prompt_tokens": 5000, "completion_tokens": 800,
                   "prompt_tokens_details": {"cached_tokens": 4096}},
                  {"input_tokens": 10, "output_tokens": 5, "prompt_tokens": 10,
                   "completion_tokens": 5, "input_tokens_details": {"cached_tokens": 99}},
                  {}):
        for provider in ("openai", "ollama", "canned-fallback"):
            trace = {"provider": provider, "model": model,
                     "provider_meta": {"usage": usage}}
            assert PriceBook().cost_usd(trace) == pytest.approx(sw.call_cost_usd(trace))


def test_interleave_matches_sweep():
    rng = random.Random(3)
    for _ in range(20):
        probs = [{"title_slug": f"p{i}", "level": rng.choice(["Easy", "Medium", "Hard", ""])}
                 for i in range(rng.randint(0, 30))]
        assert interleave_by_level(probs) == ms._interleave_by_level(probs)


def test_cell_matching_matches_script():
    rng = random.Random(5)
    subs = [{"id": i, "model_name": rng.choice(["m", "n"]), "model_version": "v",
             "language": rng.choice(["python3", "cpp"]),
             "fully_accepted": rng.random() < 0.3,
             "prompt_id": rng.choice([None, 1, 2]), "include_hints": rng.random() < 0.5,
             "optimization_target": rng.choice([None, "latency"]),
             "n_rounds": rng.choice([1, 3]),
             "competition_slug": rng.choice([None, "leetcode-99-perf", "clbg-10"]),
             "harness_slug": rng.choice([None, "h1", "h2"]),
             "created_at": f"2026-10-{rng.randint(10, 28)}T00:00:{i:02d}"} for i in range(60)]
    for _ in range(200):
        cell = {"model_name": rng.choice(["m", "n"]), "model_version": "v",
                "language": rng.choice(["python3", "cpp"]),
                "prompt_id": rng.choice([None, 1, 2]), "include_hints": rng.random() < 0.5,
                "optimization_target": rng.choice([None, "latency"]),
                "n_rounds": rng.choice([1, 3]), "competition_slug": rng.choice(
                    ["leetcode-99-perf", "clbg-10"]), "harness_slug": rng.choice(["h1", "h2"]),
                "include_fully_accepted": rng.random() < 0.5}
        orig = sw.http_json
        sw.http_json = lambda *a, **k: subs
        try:
            want = sw.find_existing_submission(
                "http://x", "k", cell["model_name"], cell["model_version"], cell["language"],
                prompt_id=cell["prompt_id"], include_hints=cell["include_hints"],
                optimization_target=cell["optimization_target"], n_rounds=cell["n_rounds"],
                competition_slug=cell["competition_slug"], harness_slug=cell["harness_slug"],
                include_fully_accepted=cell["include_fully_accepted"])
        finally:
            sw.http_json = orig
        assert find_matching_submission(subs, cell) == want


# ---------- provider wire parity ------------------------------------------------------


class _Wire:
    """Capture the SDK's provider POSTs and answer them."""

    def __init__(self, monkeypatch, answers):
        self.sent, self.answers = [], list(answers)
        monkeypatch.setattr("perfarena.llm.http.httpx.post", self.post)

    def post(self, url, json=None, headers=None, timeout=None):
        import json as _json
        self.sent.append((url, _json.loads(_json.dumps(json))))   # a copy: callers mutate
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))


def _script_openai(monkeypatch, answers, model, params):
    sent, queue = [], list(answers)

    def fake(url, key, body, timeout):
        sent.append((url, body))
        return queue.pop(0)
    monkeypatch.setattr(sw, "_openai_post", fake)
    code, trace = sw.OpenAIProvider("https://api.test/v1", "k").generate(
        model, PROBLEM["content"], "two-sum", params, "python3")
    return sent, code, trace


def _strip(trace):
    return {k: v for k, v in trace.items() if k not in ("created_at", "wall_clock_ms")}


@pytest.mark.parametrize("model, params, answer", [
    ("gpt-4.1-mini", {"temperature": 0.2, "top_p": 0.9, "seed": 4, "junk": 1},
     (200, {"choices": [{"message": {"content": "<think>hmm</think>```python\nx = 1\n```"},
                         "finish_reason": "stop"}], "usage": {"prompt_tokens": 9},
            "system_fingerprint": "fp1", "model": "gpt-4.1-mini-2025"})),
    ("gpt-5", {"temperature": 0.2, "reasoning_effort": "high", "max_completion_tokens": 900},
     (200, {"output": [{"type": "reasoning", "summary": [{"text": "plan"}]},
                       {"type": "message", "content": [
                           {"type": "output_text", "text": "```python\ny = 2\n```"}]}],
            "usage": {"input_tokens": 3, "output_tokens": 4}, "status": "completed"})),
])
def test_openai_wire_parity(monkeypatch, model, params, answer):
    want_sent, want_code, want_trace = _script_openai(monkeypatch, [answer], model, params)
    wire = _Wire(monkeypatch, [json.loads(json.dumps(answer))])
    gen = Generator(OpenAIProvider("https://api.test/v1", "k")).generate(
        model, None, PROBLEM, "python3", params)
    assert wire.sent == want_sent
    assert gen.code == want_code and _strip(gen.trace) == _strip(want_trace)


def test_ollama_wire_parity_with_think_retry(monkeypatch):
    answers = [(400, {"error": "model does not support thinking"}),
               (200, {"message": {"content": "```python\nz = 3\n```", "thinking": "t"},
                      "eval_count": 5, "done_reason": "stop", "extra": 1})]
    sent = []
    queue = list(answers)

    def fake(url, body, timeout):
        sent.append((url, json.loads(json.dumps(body))))
        return queue.pop(0)
    monkeypatch.setattr(sw, "_ollama_post", fake)
    want_code, want_trace = sw.OllamaProvider("http://ol:11434").generate(
        "m", PROBLEM["content"], "two-sum", {"temperature": 0.1}, "python3")
    wire = _Wire(monkeypatch, answers)
    gen = Generator(OllamaProvider("http://ol:11434")).generate(
        "m", None, PROBLEM, "python3", {"temperature": 0.1})
    assert [(u, b) for u, b in wire.sent] == sent
    assert gen.code == want_code and _strip(gen.trace) == _strip(want_trace)


def test_fake_provider_feeds_the_same_trace_shape():
    gen = Generator(FakeProvider("```python\nq = 1\n```")).generate(
        "m", None, PROBLEM, "python3", {"temperature": 0.0})
    assert set(gen.trace) == {"prompt", "system_prompt", "raw_response", "reasoning", "model",
                              "provider", "endpoint", "params", "created_at", "wall_clock_ms",
                              "provider_meta"}
