"""PerfArena MCP server: an agent's interface to the arena, on top of the SDK.

    perfarena mcp                         # stdio, for a local agent
    perfarena mcp --transport streamable-http --port 8765
    perfarena --profile prod mcp --allow-admin

Read tools work with any key (or none). Write tools (submit a solution, wait
for a verdict, start a run) need an admin API key, like the REST API. Admin
tools (replicas, redispatch) are registered only with --allow-admin. The
server never returns an oracle's config (it can hold runner tokens) and never
echoes the API key.
"""
from __future__ import annotations

import argparse
import functools
import inspect
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
from mcp.server.mcpserver import Context, MCPServer, UserMessage
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .arena import Arena
from .config import Settings
from .errors import PerfArenaError, SpecError
from .languages import LANGUAGE_DISPLAY
from .prompts import PromptBuilder
from .runs.events import EventBus
from .spec import RunSpec

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                        openWorldHint=False)
ADMIN = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

INSTRUCTIONS = """\
PerfArena benchmarks the correctness and efficiency (runtime, memory, energy)
of code solutions. Typical agent workflow:
1. list_problems / get_problem to read a problem and its starter code.
2. Write the solution yourself.
3. submit_solution (admin key) with your model name and agent slug, then
   wait_for_verdict. A rejected verdict carries the failing case: fix and
   submit again.
start_run launches a whole RunSpec (provider LLM keys come from the server's
environment) as a background job; poll it with get_run.
"""

_PUBLIC_ORACLE = ("slug", "name", "kind", "status", "description",
                  "n_competitions", "n_submissions", "n_runs")


@dataclass
class Job:
    id: str
    spec: RunSpec
    status: str = "running"                     # running | done | failed | cancelled
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict | None = None
    error: str | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=200))
    cancel: threading.Event = field(default_factory=threading.Event)

    def view(self, n_events: int = 20) -> dict:
        return {"job_id": self.id, "status": self.status, "result": self.result,
                "error": self.error, "started_at": self.started_at,
                "finished_at": self.finished_at,
                "recent_events": [{"kind": e.kind, "at": e.at, **e.data}
                                  for e in list(self.events)[-n_events:]]}


class JobManager:
    """Runs RunSpecs in background threads, one ledger per job."""

    def __init__(self, arena: Arena, directory: Path):
        self.arena, self.dir = arena, directory
        self.jobs: dict[str, Job] = {}

    def start(self, spec: RunSpec) -> Job:
        job = Job(id=f"{spec.digest()}-{uuid.uuid4().hex[:6]}", spec=spec)
        self.jobs[job.id] = job
        self.dir.mkdir(parents=True, exist_ok=True)

        def run() -> None:
            try:
                res = self.arena.run(spec, ledger=self.dir / f"{job.id}.json",
                                     events=EventBus(job.events.append), cancel=job.cancel)
                job.result = {"completed": res.completed, "skipped": res.skipped,
                              "failed": res.failed, "submissions": res.submissions}
                job.status = "cancelled" if job.cancel.is_set() else "done"
            except Exception as e:  # noqa: BLE001 — reported through get_run
                job.status, job.error = "failed", f"{type(e).__name__}: {e}"
            finally:
                job.finished_at = time.time()

        threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise SpecError(f"no job {job_id!r}")
        return self.jobs[job_id]


def _sdk_errors_as_tool_errors(fn):
    """An SDK error reaches the agent as a ToolError with its message (the
    MCP SDK hides the text of any other exception)."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kw):
            try:
                return await fn(*args, **kw)
            except PerfArenaError as e:
                raise ToolError(str(e)) from e
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kw):
        try:
            return fn(*args, **kw)
        except PerfArenaError as e:
            raise ToolError(str(e)) from e
    return wrapper


def build_server(arena: Arena, *, allow_admin: bool = False,
                 jobs_dir: Path | None = None) -> MCPServer:
    server = MCPServer("perfarena", instructions=INSTRUCTIONS)

    def tool(annotations: ToolAnnotations):
        def deco(fn):
            return server.tool(annotations=annotations)(_sdk_errors_as_tool_errors(fn))
        return deco
    jobs = JobManager(arena, jobs_dir or Path.home() / ".cache" / "perfarena" / "mcp-jobs")
    role: dict[str, bool] = {}

    def need_admin() -> None:
        if "admin" not in role:
            try:
                arena.require_admin()
                role["admin"] = True
            except PerfArenaError:
                role["admin"] = False
        if not role["admin"]:
            raise SpecError("this tool needs the server to run with an admin API key")

    # ---- read ------------------------------------------------------------------

    @tool(READ)
    def list_competitions() -> dict:
        """Competitions (problem sets) on the arena."""
        return {"competitions": [{k: c.get(k) for k in ("slug", "name", "kind", "status",
                                                        "description")}
                                 for c in arena.competitions.list()]}

    @tool(READ)
    def list_problems(competition: str = "leetcode-99-perf",
                      level: str | None = None) -> dict:
        """Problems of a competition (optionally one level: Easy, Medium, Hard)."""
        return {"problems": [{k: p.get(k) for k in ("title_slug", "title", "level", "tags")}
                             for p in arena.problems.list(competition=competition,
                                                          level=level)]}

    @tool(READ)
    def get_problem(slug: str, language: str = "python3") -> dict:
        """A problem's statement, hints, and the starter code for a language."""
        p = arena.problems.get(slug)
        return {"title_slug": p["title_slug"], "title": p.get("title"),
                "level": p.get("level"), "statement": p.get("content"),
                "hints": p.get("hints"),
                "starter_code": (p.get("code_snippets") or {}).get(language),
                "languages": sorted(LANGUAGE_DISPLAY)}

    @tool(READ)
    def list_prompts() -> dict:
        """Registry prompt templates (names usable in a RunSpec)."""
        return {"prompts": [{k: p.get(k) for k in ("id", "name", "notes", "language_hint")}
                            for p in arena.prompts.list()]}

    @tool(READ)
    def list_models() -> dict:
        """Registered models with their metadata."""
        return {"models": [{k: m.get(k) for k in ("name", "version", "provider", "family",
                                                  "model_type", "status")}
                           for m in arena.leaderboard.models()]}

    @tool(READ)
    def get_rankings(competition: str | None = None, language: str | None = None,
                     limit: int = 20) -> dict:
        """Leaderboard rows (sorted by pass@1), at most `limit`."""
        params = {k: v for k, v in {"competition": competition, "language": language}.items() if v}
        rows = arena.leaderboard.rankings(**params)
        return {"rankings": rows[:limit] if isinstance(rows, list) else rows}

    @tool(READ)
    def compare_models(models: list[str], language: str | None = None,
                       competition: str | None = None) -> dict:
        """Head-to-head comparison of two or more models."""
        params: dict[str, Any] = {"models": models}
        if language:
            params["language"] = language
        if competition:
            params["competition"] = competition
        return arena.leaderboard.compare(**params)

    @tool(READ)
    def get_submission(submission_id: int) -> dict:
        """A submission's summary (pass@k, metrics, status)."""
        return arena.submissions.get(submission_id).get("submission", {})

    @tool(READ)
    def list_attempts(submission_id: int) -> dict:
        """The attempts of a submission: verdict and metrics, no traces."""
        keep = ("attempt_id", "problem_slug", "round_index", "status", "accepted",
                "n_runs", "n_accepted_runs", "avg_runtime_ms", "avg_memory_mb",
                "avg_runtime_energy_j", "prefailed_reason", "dispatch_warning")
        return {"attempts": [{k: a.get(k) for k in keep}
                             for a in arena.submissions.attempts(submission_id)]}

    @tool(READ)
    def get_attempt(attempt_id: int) -> dict:
        """One attempt: dispatch state, verdict, metrics, code, and per-run
        error logs (the failing input, expected and actual output)."""
        return arena.attempts.state(attempt_id)

    @tool(READ)
    def list_oracles() -> dict:
        """Performance harnesses and correctness oracles (no configs)."""
        def pub(rows):
            return [{k: r.get(k) for k in _PUBLIC_ORACLE} for r in rows]
        return {"harnesses": pub(arena.oracles.harnesses()),
                "correctness_oracles": pub(arena.oracles.correctness_oracles())}

    @tool(READ)
    def list_replicas(oracle: str, correctness: bool = False) -> dict:
        """An oracle's runner replicas and its effective replica policy."""
        return {"policy": arena.replica_policy(oracle, correctness=correctness),
                "replicas": arena.replicas.list(oracle, correctness=correctness)}

    # ---- write (admin key) -----------------------------------------------------------

    @tool(WRITE)
    def submit_solution(problem: str, language: str, code: str, model: str, harness: str,
                        correctness_oracle: str | None = None,
                        competition: str = "leetcode-99-perf", agent: str | None = None,
                        version: str = "external",
                        inference_trace: str | None = None) -> dict:
        """Submit your code for a problem as one attempt of the (model,
        version, language, harness, agent) submission. Returns the
        attempt_id to pass to wait_for_verdict."""
        need_admin()
        return arena.submit_solution(problem, language, code, model=model, harness=harness,
                                     version=version, correctness=correctness_oracle,
                                     competition=competition, agent=agent,
                                     inference_trace=inference_trace)

    @tool(WRITE)
    async def wait_for_verdict(attempt_id: int, ctx: Context,
                               timeout_s: float = 900.0) -> dict:
        """Wait until the attempt has a verdict (or a dispatch failure), with
        progress notifications. Returns its state; state 'timeout' if none."""
        await anyio.to_thread.run_sync(need_admin)
        t0 = time.monotonic()
        delay = 2.0
        while True:
            st = await anyio.to_thread.run_sync(arena.attempts.state, attempt_id)
            if st.get("terminal"):
                return st
            elapsed = time.monotonic() - t0
            if elapsed >= timeout_s:
                return {**st, "state": "timeout"}
            await ctx.report_progress(elapsed, timeout_s, f"state: {st.get('state')}")
            await anyio.sleep(min(delay, timeout_s - elapsed))
            delay = min(delay * 1.5, 10.0)

    @tool(WRITE)
    def start_run(spec: dict) -> dict:
        """Start a RunSpec (the YAML spec as a JSON object) as a background
        job. Returns its job_id; poll with get_run."""
        need_admin()
        job = jobs.start(RunSpec.from_dict(spec))
        return {"job_id": job.id, "status": job.status}

    @tool(READ)
    def get_run(job_id: str, events: int = 20) -> dict:
        """A run job's status, result, and its most recent events."""
        return jobs.get(job_id).view(events)

    @tool(WRITE)
    def cancel_run(job_id: str) -> dict:
        """Stop a run job after the cells in progress finish."""
        need_admin()
        job = jobs.get(job_id)
        job.cancel.set()
        return {"job_id": job_id, "status": "cancelling"}

    # ---- admin (--allow-admin) ----------------------------------------------------------

    if allow_admin:
        @tool(ADMIN)
        def add_replica(oracle: str, name: str, endpoint: str, correctness: bool = False,
                        weight: int = 1, priority: int = 100, capacity: int = 1) -> dict:
            """Register a runner replica under a harness (or correctness oracle)."""
            need_admin()
            kw = {"correctness_oracle" if correctness else "harness": oracle}
            return arena.replicas.add(name=name, endpoint=endpoint, weight=weight,
                                      priority=priority, capacity=capacity, **kw)

        @tool(ADMIN)
        def set_replica_status(replica_id: int, status: str) -> dict:
            """active | disabled | quarantined."""
            need_admin()
            return arena.replicas.edit(replica_id, status=status)

        @tool(ADMIN)
        def calibrate_replica(replica_id: int, machine_class: str | None = None,
                              set_baseline: bool = False) -> dict:
            """Start the admission-gate calibration of a performance replica."""
            need_admin()
            return arena.replicas.calibrate(replica_id, machine_class=machine_class,
                                            set_baseline=set_baseline)

        @tool(ADMIN)
        def redispatch_submission(submission_id: int) -> dict:
            """Dispatch a submission's pending attempts again."""
            need_admin()
            return arena.submissions.redispatch(submission_id)

    # ---- resources + prompts -------------------------------------------------------------

    @server.resource("perfarena://problems/{slug}", mime_type="text/markdown")
    def problem_resource(slug: str) -> str:
        p = arena.problems.get(slug)
        snippets = p.get("code_snippets") or {}
        starter = "\n\n".join(f"### {lang}\n```{LANGUAGE_DISPLAY[lang][1]}\n{code}\n```"
                              for lang, code in sorted(snippets.items())
                              if lang in LANGUAGE_DISPLAY)
        return (f"# {p.get('title')} ({p.get('level')})\n\n{p.get('content')}"
                + (f"\n\n## Starter code\n\n{starter}" if starter else ""))

    @server.resource("perfarena://submissions/{submission_id}", mime_type="application/json")
    def submission_resource(submission_id: str) -> str:
        return json.dumps(arena.submissions.get(int(submission_id)).get("submission", {}),
                          default=str)

    @server.resource("perfarena://rankings/{competition}", mime_type="application/json")
    def rankings_resource(competition: str) -> str:
        return json.dumps(arena.leaderboard.rankings(competition=competition), default=str)

    @server.prompt()
    def solve_problem(slug: str, language: str = "python3", prompt: str = "") -> list:
        """The arena's own prompt for a problem: the registry prompt named
        `prompt`, or the built-in one."""
        tpl = arena.prompts.get(prompt) if prompt else None
        sys_p, user_p = PromptBuilder().build(tpl, arena.problems.get(slug), language)
        return [UserMessage(f"{sys_p}\n\n{user_p}")]

    return server


def serve(arena: Arena, *, transport: str = "stdio", host: str = "127.0.0.1",
          port: int = 8765, allow_admin: bool = False) -> None:
    server = build_server(arena, allow_admin=allow_admin)
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="perfarena-mcp", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--base-url")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--allow-admin", action="store_true")
    a = ap.parse_args(argv)
    serve(Arena(settings=Settings.resolve(a.profile, base_url=a.base_url)),
          transport=a.transport, host=a.host, port=a.port, allow_admin=a.allow_admin)
