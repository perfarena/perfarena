"""Arena: the SDK's single entry point (Facade).

    from perfarena import Arena, RunSpec
    arena = Arena.from_profile("prod")
    arena.problems.get("two-sum")
    result = arena.run(RunSpec.load("spec.yaml"))
"""
from __future__ import annotations

import threading
from pathlib import Path

import httpx

from .api import (Admin, ArenaClient, Attempts, Competitions, Leaderboard,
                  MachineClasses, Oracles, Problems, Prompts, Replicas, Submissions)
from .config import Settings
from .errors import SpecError
from .generate import now_iso
from .pricing import PriceBook
from .provenance import trace_hash
from .runs.events import EventBus
from .runs.executor import Executor, RunResult
from .runs.ledger import GenerationCache, Ledger
from .runs.waiter import AttemptWaiter
from .spec import RunSpec


class Arena:
    """One arena connection, with one attribute per resource group
    (`problems`, `prompts`, `competitions`, `submissions`, `attempts`,
    `oracles`, `replicas`, `machine_classes`, `leaderboard`, `admin`) and
    the high-level operations: run a spec, submit a solution, wait for a
    verdict."""

    def __init__(self, client: ArenaClient | None = None, *, settings: Settings | None = None,
                 http: httpx.Client | None = None):
        self.client = client or ArenaClient(settings, http=http)
        c = self.client
        self.problems, self.prompts = Problems(c), Prompts(c)
        self.competitions, self.submissions = Competitions(c), Submissions(c)
        self.attempts, self.oracles = Attempts(c), Oracles(c)
        self.replicas, self.machine_classes = Replicas(c), MachineClasses(c)
        self.leaderboard, self.admin = Leaderboard(c), Admin(c)

    @classmethod
    def from_profile(cls, profile: str | None = None, **overrides) -> "Arena":
        """Connect with `Settings.resolve(profile, **overrides)`."""
        return cls(settings=Settings.resolve(profile, **overrides))

    # ---- runs -------------------------------------------------------------------

    def run(self, spec: RunSpec, *, ledger: Ledger | str | Path | None = None,
            cache: GenerationCache | str | Path | None = None,
            events: EventBus | None = None, cancel: threading.Event | None = None,
            retry_failed: bool = False, **executor_kw) -> RunResult:
        """Run every cell of the spec; resumable through the ledger file."""
        if not isinstance(ledger, Ledger):
            ledger = Ledger(ledger or "perfarena-ledger.json")
        if cache is not None and not isinstance(cache, GenerationCache):
            cache = GenerationCache(cache)
        return Executor(self, spec, ledger=ledger, cache=cache, events=events,
                        cancel=cancel, retry_failed=retry_failed, **executor_kw).run()

    def policy_mismatches(self, spec: RunSpec) -> list[str]:
        """Check-only replica policy: one message per oracle whose live
        policy differs from spec.replicas."""
        if spec.replicas is None:
            return []
        out = []
        for slug, want, correctness in (
                (spec.oracles.performance, spec.replicas.performance, False),
                (spec.oracles.correctness, spec.replicas.correctness, True)):
            if not want or not slug:
                continue
            live = self.replica_policy(slug, correctness=correctness)
            if live != want:
                kind = "correctness oracle" if correctness else "harness"
                out.append(f"{kind} {slug!r} uses replica policy {live!r}, the spec "
                           f"requires {want!r} (an admin sets it on the oracle's config)")
        return out

    def replica_policy(self, slug: str, *, correctness: bool = False) -> str:
        """The oracle's effective policy, by the server's rule: config.replicas
        .policy, else `primary` while only the primary is active, else
        least_busy (correctness) / sticky (performance)."""
        oracle = (self.oracles.correctness_oracle if correctness else self.oracles.harness)(slug)
        policy = ((oracle.get("config") or {}).get("replicas") or {}).get("policy")
        if policy:
            return policy
        active = [r for r in self.replicas.list(slug, correctness=correctness)
                  if r.get("status") == "active"]
        if all(r.get("is_primary") for r in active):
            return "primary"
        return "least_busy" if correctness else "sticky"

    # ---- one solution --------------------------------------------------------------

    def submit_solution(self, problem: str, language: str, code: str, *, model: str,
                        harness: str, version: str = "external",
                        correctness: str | None = None,
                        competition: str = "leetcode-99-perf", agent: str | None = None,
                        inference_trace: str | None = None, trace: dict | None = None,
                        prompt_template: str = "solution submitted through the perfarena SDK",
                        wait_s: float | None = None) -> dict:
        """Submit code you already have (an agent's, a human's) as one
        attempt of the (model, version, language, harness, agent) submission.
        Admin key required (POST /api/submissions is admin-only). With
        `wait_s`, waits for the verdict."""
        trace = trace or {"provider": "external", "model": model, "raw_response": code,
                          "created_at": now_iso()}
        summary = inference_trace or "submitted through the perfarena SDK"
        cell = {"model_name": model, "model_version": version, "language": language,
                "prompt_template": prompt_template, "model_params": {},
                "competition_slug": competition, "harness_slug": harness,
                "correctness_oracle_slug": correctness, "agent_slug": agent}
        sid = self.submissions.upsert(cell)["submission_id"]
        out = self.submissions.append(sid, [{"title_slug": problem, "code": code,
                                             "inference_trace": summary, "trace": trace}])
        aid = (out.get("attempt_ids") or [None])[0]
        result = {"submission_id": sid, "attempt_id": aid,
                  "leaf_hash": trace_hash(code, summary, trace)}
        if wait_s and aid is not None:
            result["state"] = self.wait(aid, wait_s, submission_id=sid)
        return result

    def wait(self, attempt_id: int, timeout_s: float, *,
             submission_id: int | None = None) -> dict | None:
        """The attempt's state once terminal, or None at the timeout."""
        return AttemptWaiter(self.attempts, self.submissions).wait(
            attempt_id, timeout_s, submission_id=submission_id)

    def pricebook(self) -> PriceBook:
        """The generation cost rules, with the server's per-model rates."""
        return PriceBook.from_rows(self.leaderboard.pricing())

    def require_admin(self) -> None:
        """Raise SpecError unless the API key is an admin key."""
        if not self.client.has_key or self.admin.me().get("role") != "admin":
            raise SpecError("this action needs an admin API key")
