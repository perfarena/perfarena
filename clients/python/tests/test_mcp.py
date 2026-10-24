"""The MCP server, driven by an in-process MCP client against the real
backend."""
from __future__ import annotations

import json
import time

import anyio
from mcp import Client

from fakes import GOOD, fenced
from perfarena.llm import FakeProvider, register_provider
from perfarena.mcp_server import build_server


def _call(server, name: str, args: dict | None = None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, args or {})
    return anyio.run(go)


def _json(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _tools(server) -> dict:
    async def go():
        async with Client(server) as c:
            return {t.name: t for t in (await c.list_tools()).tools}
    return anyio.run(go)


def test_tool_sets_and_annotations(arena, tmp_path):
    plain = _tools(build_server(arena, jobs_dir=tmp_path))
    admin = _tools(build_server(arena, allow_admin=True, jobs_dir=tmp_path))
    assert {"list_problems", "get_problem", "submit_solution", "wait_for_verdict",
            "start_run", "get_run"} <= set(plain)
    assert "add_replica" not in plain and "add_replica" in admin
    assert plain["get_problem"].annotations.read_only_hint is True
    assert plain["submit_solution"].annotations.read_only_hint is False
    assert admin["add_replica"].annotations.destructive_hint is True


def test_read_tools(arena, oracles, tmp_path):
    s = build_server(arena, jobs_dir=tmp_path)
    probs = _json(_call(s, "list_problems", {"competition": "leetcode-99-perf"}))["problems"]
    assert "two-sum" in {p["title_slug"] for p in probs}
    p = _json(_call(s, "get_problem", {"slug": "two-sum", "language": "python3"}))
    assert p["starter_code"].startswith("class Solution") and "Solve **two-sum**" in p["statement"]
    orc = _json(_call(s, "list_oracles"))
    assert all("config" not in h for h in orc["harnesses"])
    reps = _json(_call(s, "list_replicas", {"oracle": oracles[0]}))
    assert reps["policy"] == "primary" and len(reps["replicas"]) == 1
    err = _call(s, "get_problem", {"slug": "no-such-problem"})
    assert err.is_error and "404" in err.content[0].text


def test_submit_wait_and_read_attempt(arena, oracles, tmp_path):
    s = build_server(arena, jobs_dir=tmp_path)
    harness, oracle = oracles
    out = _json(_call(s, "submit_solution", {
        "problem": "two-sum", "language": "python3", "code": GOOD, "model": "mcp-agent",
        "harness": harness, "correctness_oracle": oracle}))
    st = _json(_call(s, "wait_for_verdict", {"attempt_id": out["attempt_id"],
                                              "timeout_s": 30}))
    assert st["verdict"] == "Accepted" and st["terminal"]
    att = _json(_call(s, "get_attempt", {"attempt_id": out["attempt_id"]}))
    assert att["code"] == GOOD and len(att["runs"]) == 2


def test_write_tools_need_an_admin_key(user_arena, oracles, tmp_path):
    s = build_server(user_arena, jobs_dir=tmp_path)
    r = _call(s, "submit_solution", {"problem": "two-sum", "language": "python3",
                                     "code": GOOD, "model": "m", "harness": oracles[0]})
    assert r.is_error and "admin API key" in r.content[0].text
    assert not _call(s, "list_problems").is_error


def test_start_run_job(arena, oracles, tmp_path):
    register_provider("scripted-good")(lambda spec: FakeProvider(fenced(GOOD)))
    s = build_server(arena, jobs_dir=tmp_path)
    spec = {"model": {"name": "mcp-run", "provider": "scripted-good"},
            "oracles": {"performance": oracles[0], "correctness": oracles[1]},
            "languages": ["python3"], "problems": ["two-sum"], "poll_timeout_s": 30}
    job = _json(_call(s, "start_run", {"spec": spec}))
    for _ in range(100):
        view = _json(_call(s, "get_run", {"job_id": job["job_id"]}))
        if view["status"] != "running":
            break
        time.sleep(0.1)
    assert view["status"] == "done", view
    assert view["result"]["completed"] == 1
    assert "verdict" in {e["kind"] for e in view["recent_events"]}
    bad = _call(s, "start_run", {"spec": {"model": {"name": "x"}}})
    assert bad.is_error


def test_resources_and_prompt(arena, tmp_path):
    s = build_server(arena, jobs_dir=tmp_path)

    async def go():
        async with Client(s) as c:
            res = await c.read_resource("perfarena://problems/two-sum")
            pr = await c.get_prompt("solve_problem", {"slug": "two-sum", "language": "python3"})
            return res, pr
    res, pr = anyio.run(go)
    text = res.contents[0].text
    assert text.startswith("# Two-Sum") and "## Starter code" in text
    msg = pr.messages[0].content.text
    assert "Solve this LeetCode problem `two-sum` in Python 3." in msg
