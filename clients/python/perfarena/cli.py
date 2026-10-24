"""`perfarena` command line. Thin: every command calls the SDK.

The `run` / `spec show` flags are generated from the RunSpec schema (one flag
per field, nested fields joined by dashes: --rounds-n, --oracles-performance,
--model-provider), so the CLI cannot drift from the SDK. A few short
aliases cover the common ones (--model, --provider, --harness, ...).

    perfarena run spec.yaml
    perfarena run --model gpt-5.5 --provider openai --languages python3,cpp \\
        --harness arena-linux-local --correctness-oracle arena-local --rounds 3
"""
from __future__ import annotations

import argparse
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from . import Arena, RunSpec
from .config import Settings
from .errors import PerfArenaError
from .runs import ConsoleReporter, EventBus, JsonlReporter
from .spec import ModelSpec

# alias flag -> generated flag destination (dotted field path)
ALIASES: dict[str, str] = {
    "--model": "models.name",
    "--provider": "models.provider",
    "--harness": "oracles.performance",
    "--correctness-oracle": "oracles.correctness",
    "--prompt": "prompts",
    "--rounds": "rounds.n",
    "-k": "sampling.k",
}
_SKIP = {"models"}            # expanded field by field as `model.*`


def _unwrap(ann: Any) -> tuple[Any, bool]:
    """(inner type, optional?) of `X | None`."""
    args = typing.get_args(ann)
    if (typing.get_origin(ann) in (typing.Union, types.UnionType)) and type(None) in args:
        rest = [a for a in args if a is not type(None)]
        return (rest[0] if len(rest) == 1 else typing.Union[tuple(rest)]), True
    return ann, False


def spec_fields(cls: type[BaseModel] = RunSpec, prefix: tuple[str, ...] = ()) -> list[tuple]:
    """Leaf fields as (dotted path, annotation, description)."""
    out = []
    for name, f in cls.model_fields.items():
        if not prefix and name in _SKIP:
            out += spec_fields(ModelSpec, ("models",))
            continue
        ann, _ = _unwrap(f.annotation)
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out += spec_fields(ann, prefix + (name,))
        else:
            out.append((".".join(prefix + (name,)), ann, f.description))
    return out


def _flag(path: str) -> str:
    return "--" + path.replace("models.", "model.").replace(".", "-").replace("_", "-")


def _parser_for(ann: Any) -> dict:
    origin = typing.get_origin(ann)
    if ann is bool:
        return {"action": argparse.BooleanOptionalAction}
    if origin is Literal:
        return {"choices": list(typing.get_args(ann))}
    if origin is tuple:
        return {"type": lambda s: [x.strip() for x in s.split(",") if x.strip()],
                "metavar": "A,B"}
    if ann in (int, float):
        return {"type": ann}
    if ann is str:
        return {"type": str}
    return {"type": _json_or_str, "metavar": "JSON"}       # dicts, unions


def _json_or_str(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


def add_spec_flags(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("run spec (override the spec file)")
    reverse = {v: k for k, v in ALIASES.items()}
    for path, ann, desc in spec_fields():
        flags = [_flag(path)]
        if path in reverse:
            flags.insert(0, reverse[path])
        kw = _parser_for(ann)
        if "action" not in kw and "choices" not in kw:
            kw.setdefault("metavar", path.split(".")[-1].upper())
        g.add_argument(*flags, dest=f"spec__{path}", default=argparse.SUPPRESS,
                       help=desc, **kw)
    g.add_argument("--temperature", dest="spec__temperature", type=float,
                   default=argparse.SUPPRESS, help="shortcut for model params.temperature")


def spec_from_args(args: argparse.Namespace) -> RunSpec:
    data: dict = yaml.safe_load(Path(args.spec).read_text()) if getattr(args, "spec", None) else {}
    data = dict(data or {})
    if "model" in data:
        data["models"] = [data.pop("model")]
    if "prompt" in data:
        p = data.pop("prompt")
        data["prompts"] = [] if p is None else [p]
    for key, value in vars(args).items():
        if not key.startswith("spec__"):
            continue
        path = key[len("spec__"):]
        if path == "temperature":
            models = data.setdefault("models", [{}])
            models[0] = {**models[0], "params": {**models[0].get("params", {}),
                                                  "temperature": value}}
            continue
        if path == "prompts" and isinstance(value, str):
            value = [value]
        parts = path.split(".")
        if parts[0] == "models":
            models = data.setdefault("models", [{}])
            models[0] = {**models[0], parts[1]: value}
            continue
        node = data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
    return RunSpec.from_dict(data)


SPEC_TEMPLATE = """\
# PerfArena run spec. Every field: perfarena run --help
model:
  name: qwen2.5-coder:7b
  provider: ollama          # ollama | openai | anthropic | fake | <plugin>
  params: {temperature: 0.0}
prompt: experiment-system   # a registry prompt (perfarena prompts); omit for the inline one
competition: leetcode-99-perf
languages: [python3]
problems: []                # empty = every problem of the competition
oracles:
  performance: arena-linux-local
  correctness: arena-local
rounds: {n: 1, stop_on_accept: true}
sampling: {k: 1}
"""


def _arena(args) -> Arena:
    import os
    overrides = {"base_url": args.base_url}
    if args.api_key_env:
        overrides["api_key"] = os.environ.get(args.api_key_env)
    return Arena(settings=Settings.resolve(args.profile, **overrides))


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="perfarena", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="profile name in profiles.toml")
    ap.add_argument("--base-url", help="arena URL (overrides the profile)")
    ap.add_argument("--api-key-env", help="name of the env var holding the API key")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a spec (resumable)")
    r.add_argument("spec", nargs="?", help="YAML spec file")
    r.add_argument("--ledger", default="perfarena-ledger.json")
    r.add_argument("--cache", help="generation cache dir (matrix_sweep's sweep_cache format)")
    r.add_argument("--events", help="also write events as JSONL here")
    r.add_argument("--retry-failed", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="print the resolved spec and exit")
    add_spec_flags(r)

    s = sub.add_parser("spec", help="spec utilities")
    ssub = s.add_subparsers(dest="spec_cmd", required=True)
    si = ssub.add_parser("init", help="write a template spec")
    si.add_argument("--out", default="perfarena-spec.yaml")
    ss = ssub.add_parser("show", help="print the resolved spec")
    ss.add_argument("spec", nargs="?")
    add_spec_flags(ss)

    pr = sub.add_parser("problems")
    pr.add_argument("--competition")
    pr.add_argument("--level")
    sub.add_parser("prompts")
    sub.add_parser("competitions")
    sb = sub.add_parser("submission")
    sb.add_argument("id", type=int)
    sb.add_argument("--attempts", action="store_true")
    at = sub.add_parser("attempt")
    at.add_argument("id", type=int)
    at.add_argument("--wait", type=float, metavar="SECONDS")
    sub.add_parser("oracles")
    rp = sub.add_parser("replicas")
    rp.add_argument("oracle")
    rp.add_argument("--correctness", action="store_true")
    po = sub.add_parser("policy", help="an oracle's effective replica policy")
    po.add_argument("oracle")
    po.add_argument("--correctness", action="store_true")
    sub.add_parser("queue")
    sub.add_parser("pricing")
    su = sub.add_parser("submit", help="submit code you already have")
    su.add_argument("--problem", required=True)
    su.add_argument("--language", required=True)
    su.add_argument("--file", required=True)
    su.add_argument("--model", required=True)
    su.add_argument("--version", default="external")
    su.add_argument("--harness", required=True)
    su.add_argument("--correctness-oracle")
    su.add_argument("--competition", default="leetcode-99-perf")
    su.add_argument("--agent")
    su.add_argument("--wait", type=float, metavar="SECONDS")
    mc = sub.add_parser("mcp", help="serve the MCP interface")
    mc.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    mc.add_argument("--host", default="127.0.0.1")
    mc.add_argument("--port", type=int, default=8765)
    mc.add_argument("--allow-admin", action="store_true",
                    help="register admin tools (replicas, redispatch)")

    args = ap.parse_args(argv)
    try:
        return _dispatch(args)
    except PerfArenaError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    if args.cmd == "spec":
        if args.spec_cmd == "init":
            Path(args.out).write_text(SPEC_TEMPLATE)
            print(f"wrote {args.out}")
        else:
            print(spec_from_args(args).to_yaml(), end="")
        return 0
    if args.cmd == "run":
        spec = spec_from_args(args)
        if args.dry_run:
            print(spec.to_yaml(), end="")
            return 0
        bus = EventBus(ConsoleReporter())
        if args.events:
            bus.subscribe(JsonlReporter(args.events))
        res = _arena(args).run(spec, ledger=args.ledger, cache=args.cache, events=bus,
                               retry_failed=args.retry_failed)
        _print({"run_id": res.run_id, "completed": res.completed, "skipped": res.skipped,
                "failed": res.failed, "submissions": res.submissions})
        return 0 if res.failed == 0 else 1
    if args.cmd == "mcp":
        from .mcp_server import serve
        serve(_arena(args), transport=args.transport, host=args.host, port=args.port,
              allow_admin=args.allow_admin)
        return 0
    a = _arena(args)
    if args.cmd == "problems":
        _print(a.problems.list(competition=args.competition, level=args.level))
    elif args.cmd == "prompts":
        _print(a.prompts.list())
    elif args.cmd == "competitions":
        _print(a.competitions.list())
    elif args.cmd == "submission":
        _print(a.submissions.attempts(args.id) if args.attempts else a.submissions.get(args.id))
    elif args.cmd == "attempt":
        _print(a.wait(args.id, args.wait) if args.wait else a.attempts.state(args.id))
    elif args.cmd == "oracles":
        _print({"harnesses": a.oracles.harnesses(),
                "correctness_oracles": a.oracles.correctness_oracles()})
    elif args.cmd == "replicas":
        _print(a.replicas.list(args.oracle, correctness=args.correctness))
    elif args.cmd == "policy":
        print(a.replica_policy(args.oracle, correctness=args.correctness))
    elif args.cmd == "queue":
        _print(a.admin.queue())
    elif args.cmd == "pricing":
        _print(a.leaderboard.pricing())
    elif args.cmd == "submit":
        _print(a.submit_solution(args.problem, args.language, Path(args.file).read_text(),
                                 model=args.model, version=args.version, harness=args.harness,
                                 correctness=args.correctness_oracle,
                                 competition=args.competition, agent=args.agent,
                                 wait_s=args.wait))
    return 0


if __name__ == "__main__":
    sys.exit(main())
