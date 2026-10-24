"""RunSpec validation and I/O, the schema-generated CLI, and the CLI and
MCP entry points against a live server."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import anyio
import pytest

from conftest import ADMIN_KEY, make_oracles
from fakes import GOOD, FakeOllama, FakeRunner, fenced, judge_by_marker
from perfarena import RunSpec, SpecError
from perfarena.cli import ALIASES, add_spec_flags, main, spec_fields, spec_from_args


def test_spec_validation():
    with pytest.raises(SpecError, match="unsupported languages"):
        RunSpec.from_dict({"model": {"name": "m"}, "oracles": {"performance": "h"},
                           "languages": ["cobol"]})
    with pytest.raises(SpecError):
        RunSpec.from_dict({"model": {"name": "m"}, "oracles": {"performance": "h"},
                           "rounds": {"n": 11}})
    with pytest.raises(SpecError, match="not both"):
        RunSpec.from_dict({"model": {"name": "m"}, "models": [{"name": "n"}],
                           "oracles": {"performance": "h"}})
    with pytest.raises(SpecError):
        RunSpec.from_dict({"model": {"name": "m"}, "oracles": {"performance": "h"},
                           "typo_field": 1})


def test_spec_builder_yaml_and_derived_values():
    spec = (RunSpec.builder().model("gpt-5", provider="openai", temperature=0.3)
            .prompt("experiment-system").languages("python3", "cpp")
            .oracles("leetcode", "leetcode").rounds(3).samples(2)
            .steer(hints=True, target="latency", compile_flags={"cpp": "-O3"}).build())
    assert RunSpec.from_yaml(spec.to_yaml()) == spec
    assert spec.digest() == RunSpec.from_yaml(spec.to_yaml()).digest()
    assert spec.touches_leetcode and spec.effective_poll_timeout_s == 300
    assert spec.compile_flags_for("cpp") == "-O3" and spec.compile_flags_for("java") == ""
    assert spec.models[0].effective_version == "openai"
    assert spec.with_changes(competition="clbg-10").competition == "clbg-10"


def test_cli_flags_cover_the_schema():
    paths = {p for p, _, _ in spec_fields()}
    assert set(ALIASES.values()) <= paths            # every alias names a real field
    p = argparse.ArgumentParser()
    add_spec_flags(p)
    dests = {a.dest for a in p._actions}
    assert {f"spec__{x}" for x in paths} <= dests


def test_cli_overrides_a_spec_file(tmp_path):
    f = tmp_path / "spec.yaml"
    f.write_text("model: {name: a, provider: ollama, params: {temperature: 0.0}}\n"
                 "oracles: {performance: h}\nlanguages: [python3]\n")
    p = argparse.ArgumentParser()
    p.add_argument("spec")
    add_spec_flags(p)
    spec = spec_from_args(p.parse_args([str(f), "--provider", "openai", "--rounds", "2",
                                        "--temperature", "0.5", "--languages", "cpp,java",
                                        "--no-hints", "--correctness-oracle", "o"]))
    m = spec.models[0]
    assert (m.name, m.provider, m.params) == ("a", "openai", {"temperature": 0.5})
    assert spec.rounds.n == 2 and spec.languages == ("cpp", "java")
    assert spec.oracles == spec.oracles.__class__(performance="h", correctness="o")


def _cli(live, *argv, capsys):
    rc = main(["--base-url", live, *argv])
    return rc, capsys.readouterr().out


def test_cli_read_commands(live_url, capsys, monkeypatch):
    monkeypatch.setenv("SDK_TEST_KEY", ADMIN_KEY)
    rc, out = _cli(live_url, "problems", "--competition", "leetcode-99-perf", capsys=capsys)
    assert rc == 0 and "two-sum" in {p["title_slug"] for p in json.loads(out)}
    rc, out = _cli(live_url, "prompts", capsys=capsys)
    assert "experiment-system" in {p["name"] for p in json.loads(out)}
    rc, out = _cli(live_url, "spec", "show", "--model", "x", "--harness", "h", capsys=capsys)
    assert "name: x" in out and "performance: h" in out


def test_cli_run_and_policy(live_url, capsys, tmp_path, monkeypatch):
    runner = FakeRunner(judge=judge_by_marker)
    llm = FakeOllama(lambda m, model: fenced(GOOD), models=("cli-model",))
    harness, oracle = make_oracles(runner)
    monkeypatch.setenv("SDK_TEST_KEY", ADMIN_KEY)
    rc = main(["--base-url", live_url, "--api-key-env", "SDK_TEST_KEY", "run",
               "--model", "cli-model", "--provider", "ollama", "--model-endpoint", llm.url,
               "--harness", harness, "--correctness-oracle", oracle,
               "--languages", "python3", "--problems", "two-sum",
               "--poll-timeout-s", "60", "--ledger", str(tmp_path / "l.json")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["completed"] == 1 and out["failed"] == 0
    rc, out = _cli(live_url, "policy", harness, capsys=capsys)
    assert out.strip() == "primary"
    runner.stop()
    llm.stop()


def test_cli_errors_exit_2(live_url, capsys):
    rc = main(["--base-url", live_url, "run", "--model", "x", "--harness", "h",
               "--languages", "cobol"])
    assert rc == 2 and "unsupported languages" in capsys.readouterr().err


def test_mcp_over_stdio(live_url):
    """`perfarena mcp` as a real subprocess, spoken to over stdio."""
    from mcp import Client, StdioServerParameters
    env = {**os.environ, "PERFARENA_API_KEY": "", "PERFARENA_BASE_URL": live_url}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "perfarena.cli", "mcp"], env=env)

    async def go():
        async with Client(params) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            res = await c.call_tool("get_problem", {"slug": "two-sum"})
            return tools, json.loads(res.content[0].text)
    tools, problem = anyio.run(go)
    assert "submit_solution" in tools and "add_replica" not in tools
    assert problem["title_slug"] == "two-sum"


def test_console_scripts_installed():
    bindir = os.path.dirname(sys.executable)
    for name in ("perfarena", "perfarena-mcp"):
        out = subprocess.run([os.path.join(bindir, name), "--help"], capture_output=True,
                             text=True, timeout=60)
        assert out.returncode == 0 and "usage" in out.stdout
