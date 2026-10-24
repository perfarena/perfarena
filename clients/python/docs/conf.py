"""Sphinx configuration of the perfarena docs.

Build:  .venv/bin/sphinx-build -b html docs docs/_build/html

Three reference pages come from the code at build time, so they cannot
drift from it: the spec fields (RunSpec schema), the CLI help (the argparse
parser), and the MCP tools (the server's own tool list). They are written to
docs/_generated/ and included by the reference pages.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import io
import os
import re
import sys
import types
import typing
from pathlib import Path

DOCS = Path(__file__).parent
sys.path.insert(0, str(DOCS.parent))

import perfarena  # noqa: E402

project = "perfarena"
author = "PerfArena"
release = version = perfarena.__version__

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinxcontrib.autodoc_pydantic",
]
myst_enable_extensions = ["colon_fence", "deflist"]
exclude_patterns = ["_build", "_generated"]
default_role = "literal"            # `x` in a docstring renders as code
smartquotes = False                 # keep -- and quotes as typed

html_theme = "furo"
html_title = f"perfarena {release}"
copybutton_prompt_text = r"\$ "
copybutton_prompt_is_regexp = True

autodoc_member_order = "bysource"
autodoc_default_options = {"members": True, "undoc-members": True,
                           "exclude-members": "model_config"}
autodoc_pydantic_model_show_json = False
autodoc_pydantic_model_show_config_summary = False
autodoc_pydantic_model_show_validator_summary = False
autodoc_pydantic_model_show_validator_members = False
autodoc_pydantic_model_show_field_summary = False
autodoc_pydantic_field_list_validators = False
autodoc_pydantic_model_member_order = "bysource"


def _literal_blocks(app, what, name, obj, options, lines):
    """The docstrings show examples as indented blocks without `::`. Make
    each such block a literal block, so it renders as code, not as a quote.
    A module docstring is re-read from the source: Python (3.13+) and
    autodoc dedent it, and an example that follows a one-line summary would
    lose its indentation."""
    if what == "module":
        raw = ast.get_docstring(ast.parse(inspect.getsource(obj)), clean=False)
        if raw:
            lines[:] = raw.expandtabs().strip("\n").splitlines() + [""]
    out, in_block, prev, last = [], False, "", ""
    for line in lines:
        if line.strip() and not line.startswith(" "):
            in_block = False
        elif (line.startswith("  ") and not in_block
              and (prev == "" or prev.endswith(":"))):
            in_block = True
            if not last.endswith("::"):
                out += ["", "::", ""]
        out.append(line)
        prev = line.strip()
        if prev:
            last = prev
    lines[:] = out


def setup(app):
    app.connect("autodoc-process-docstring", _literal_blocks)


# ---- generated reference fragments ---------------------------------------------

GEN = DOCS / "_generated"
GEN.mkdir(exist_ok=True)


def _cell(text: object) -> str:
    """One markdown table cell: one line, `|` escaped."""
    return re.sub(r"\s+", " ", str(text)).strip().replace("|", "\\|")


def _type_name(ann: object) -> str:
    if ann is type(None):
        return "None"
    if isinstance(ann, type):
        return ann.__name__
    if typing.get_origin(ann) is typing.Literal:
        return " | ".join(repr(a) for a in typing.get_args(ann))
    if isinstance(ann, types.UnionType) or typing.get_origin(ann) is typing.Union:
        return " | ".join(_type_name(a) for a in typing.get_args(ann))
    return re.sub(r"\btyping\.", "", str(ann))


def _spec_fields() -> str:
    from pydantic import BaseModel
    from pydantic_core import PydanticUndefined

    from perfarena.cli import ALIASES, _flag
    from perfarena.spec import ModelSpec, RunSpec

    aliases = {v: k for k, v in ALIASES.items()}
    groups: dict[str, list[str]] = {}

    def walk(cls: type[BaseModel], prefix: str) -> None:
        for name, f in cls.model_fields.items():
            path = f"{prefix}{name}"
            inner = f.annotation
            nested = [a for a in (typing.get_args(inner) or (inner,))
                      if isinstance(a, type) and issubclass(a, BaseModel)]
            if path == "models":
                walk(ModelSpec, "models.")
                continue
            if nested:
                walk(nested[0], path + ".")
                continue
            if f.default_factory is not None:
                default = f"`{f.default_factory()!r}`"
            elif f.default is PydanticUndefined:
                default = "required"
            else:
                default = f"`{f.default!r}`"
            flags = [_flag(path)] + ([aliases[path]] if path in aliases else [])
            shown = path.replace("models.", "model.", 1)
            group = shown.split(".")[0] + ".*" if "." in shown else ""
            groups.setdefault(group, []).append(
                f"`{shown}`\n: {_cell(f.description or '')}\n\n"
                f"  Type `{_type_name(inner)}` · default {default} · "
                f"CLI {', '.join(f'`{x}`' for x in flags)}\n")

    walk(RunSpec, "")
    return "\n".join(f"## {f'`{g}`' if g else 'Top-level fields'}\n\n" + "\n".join(rows)
                     for g, rows in groups.items())


CLI_COMMANDS = [[], ["run"], ["spec", "init"], ["spec", "show"], ["problems"],
                ["prompts"], ["competitions"], ["submission"], ["attempt"],
                ["oracles"], ["replicas"], ["policy"], ["queue"], ["pricing"],
                ["submit"], ["mcp"]]


def _cli_help() -> str:
    from perfarena.cli import main

    os.environ.update(NO_COLOR="1", COLUMNS="88")
    parts = []
    for cmd in CLI_COMMANDS:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
            main([*cmd, "--help"])
        title = " ".join(["perfarena", *cmd])
        parts.append(f"## `{title}`\n\n```text\n{buf.getvalue().rstrip()}\n```\n")
    return "\n".join(parts)


def _mcp_tables() -> str:
    import anyio

    from perfarena import Arena, Settings
    from perfarena.mcp_server import build_server

    arena = Arena(settings=Settings(base_url="http://docs.invalid"))
    full = build_server(arena, allow_admin=True)
    plain = build_server(arena, allow_admin=False)

    async def collect():
        return (await full.list_tools(), {t.name for t in await plain.list_tools()},
                await full.list_resource_templates(), await full.list_prompts())

    tools, non_admin, resources, prompts = anyio.run(collect)

    def params(t) -> str:
        schema = t.input_schema or {}
        req = set(schema.get("required", []))
        names = [p if p in req else f"{p}?" for p in schema.get("properties", {})]
        return ", ".join(f"`{n}`" for n in names) or "none"

    def table(rows) -> str:
        out = ["| Tool | Arguments | What it does |", "|---|---|---|"]
        out += [f"| `{t.name}` | {params(t)} | {_cell((t.description or '').split(chr(10)*2)[0])} |"
                for t in rows]
        return "\n".join(out) + "\n"

    read = [t for t in tools if t.annotations and t.annotations.read_only_hint]
    write = [t for t in tools if t.name in non_admin and t not in read]
    admin = [t for t in tools if t.name not in non_admin]
    res = ["| URI | Type |", "|---|---|"]
    res += [f"| `{r.uri_template}` | {r.mime_type} |" for r in resources]
    prm = ["| Prompt | Arguments | What it gives |", "|---|---|---|"]
    prm += [f"| `{p.name}` | "
            f"{', '.join(f'`{a.name}`' if a.required else f'`{a.name}?`' for a in p.arguments)} "
            f"| {_cell(p.description)} |" for p in prompts]
    return (f"### Read tools\n\nAny key, or no key.\n\n{table(read)}\n"
            f"### Write tools\n\nAn admin API key.\n\n{table(write)}\n"
            f"### Admin tools\n\n`--allow-admin` and an admin API key.\n\n{table(admin)}\n"
            f"### Resources\n\n" + "\n".join(res) + "\n\n"
            f"### Prompts\n\n" + "\n".join(prm) + "\n")


(GEN / "spec-fields.md").write_text(_spec_fields())
(GEN / "cli-help.md").write_text(_cli_help())
(GEN / "mcp-tools.md").write_text(_mcp_tables())
