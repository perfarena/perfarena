"""Executor: runs the cells of a RunSpec. Per cell and sample chain:
generate → submit → wait for the verdict → (next round with feedback).

The rules are the sweep's (matrix_sweep.py): one submission per (model,
version, prompt, language); a problem already covered by that submission is
skipped; failed generations become prefailed attempts, or a retryable ledger
entry when the failure is infrastructure; an in-flight attempt left by an
earlier run is polled again, never resubmitted; LeetCode runs are paced and
run one cell at a time."""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from ..attempt import build_attempt
from ..codegen import looks_like, sanitize_compile_flags
from ..errors import (ArenaHTTPError, PerfArenaError, PolicyMismatch, ProviderError,
                      SpecError, generation_failure_verdict)
from ..generate import Generator, failed_trace
from ..llm import create_provider, standard_chain
from ..llm.base import Provider
from ..pricing import PriceBook
from ..prompts import PromptBuilder, RoundState, distill_judge, is_clbg_statement, system_prompt
from ..spec import ModelSpec, RunSpec
from .cells import Cell, order_problems, plan
from .events import EventBus
from .ledger import GenerationCache, Ledger, cache_salt
from .strategies import ExperimentStrategy, strategy_for
from .waiter import AttemptWaiter

if TYPE_CHECKING:
    from ..arena import Arena

ProviderFactory = Callable[[ModelSpec], Provider]

_RECOVERABLE = ("auth failed", "exhaust", "all pool accounts", "leetcode session expired",
                "runner unreachable", "harness unreachable", "oracle unreachable",
                "/healthz", "connection refused", "transport error")


@dataclass
class RunResult:
    run_id: str
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    submissions: dict[str, int] = field(default_factory=dict)
    cells: list[dict] = field(default_factory=list)


@dataclass
class _Group:
    lock: threading.Lock = field(default_factory=threading.Lock)
    looked_up: bool = False
    sid: int | None = None
    covered: set[str] = field(default_factory=set)


class Executor:
    def __init__(self, arena: "Arena", spec: RunSpec, *,
                 ledger: Ledger | None = None, cache: GenerationCache | None = None,
                 events: EventBus | None = None,
                 provider_factory: ProviderFactory | None = None,
                 strategy: ExperimentStrategy | None = None,
                 cancel: threading.Event | None = None, retry_failed: bool = False,
                 sleep: Callable[[float], None] = time.sleep,
                 waiter: AttemptWaiter | None = None):
        self.arena, self.spec = arena, spec
        self.ledger = ledger or Ledger("perfarena-ledger.json")
        self.cache = cache
        self.events = events or EventBus()
        self.provider_factory = provider_factory or (
            lambda m: standard_chain(create_provider(m), timeout_s=spec.inference_timeout_s))
        self.strategy = strategy or strategy_for(spec)
        self.cancel = cancel or threading.Event()
        self.retry_failed, self.sleep = retry_failed, sleep
        self.waiter = waiter or AttemptWaiter(arena.attempts, arena.submissions, sleep=sleep)
        self.run_id = f"{spec.digest()}-{uuid.uuid4().hex[:6]}"
        self._groups: dict[tuple, _Group] = {}
        self._groups_lock = threading.Lock()
        self._problems: dict[str, dict] = {}
        self._prev_pair: tuple[str, str] | None = None
        self._pace_lock = threading.Lock()
        self._is_admin: bool | None = None

    # ---- setup ----------------------------------------------------------------

    def check(self) -> None:
        """Fail fast on a spec the server cannot run: unknown oracles,
        prompts, or a replica policy that differs from the spec."""
        for slug, correctness in ((self.spec.oracles.performance, False),
                                  (self.spec.oracles.correctness, True)):
            if not slug:
                continue
            try:
                (self.arena.oracles.correctness_oracle if correctness
                 else self.arena.oracles.harness)(slug)
            except ArenaHTTPError as e:
                if e.status == 404:
                    raise SpecError(f"{'correctness oracle' if correctness else 'harness'} "
                                    f"{slug!r} does not exist") from e
                raise
        bad = self.arena.policy_mismatches(self.spec)
        if bad:
            raise PolicyMismatch("; ".join(bad))

    def _prompts(self) -> dict[str | None, dict | None]:
        if not self.spec.prompts:
            return {None: None}
        try:
            return {n: self.arena.prompts.get(n) for n in self.spec.prompts}
        except KeyError as e:
            raise SpecError(str(e)) from e

    def _slugs(self) -> list[str]:
        if self.spec.problems:
            return list(self.spec.problems)
        rows = self.arena.problems.list(competition=self.spec.competition)
        return order_problems(rows, self.spec.problem_order)

    # ---- run ------------------------------------------------------------------

    def run(self) -> RunResult:
        self.check()
        self.prompts = self._prompts()
        cells = plan(self.spec, self._slugs())
        todo = [c for c in cells if not self.ledger.should_skip(
            c.ledger_key(self.spec), retry_failed=self.retry_failed)]
        if self.spec.max_cells is not None:
            todo = todo[:self.spec.max_cells]
        result = RunResult(run_id=self.run_id)
        self.events.emit("run_started", run_id=self.run_id, cells=len(todo),
                         skipped=len(cells) - len(todo), strategy=self.strategy.name)
        self.providers = {m.name: self.provider_factory(m) for m in self.spec.models}
        self.digests = {m.name: m.digest or self.providers[m.name].model_digest(m.name)
                        for m in self.spec.models}
        self.pricebook = PriceBook.from_rows(self.arena.leaderboard.pricing())
        workers = 1 if self.spec.touches_leetcode else self.spec.concurrency
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for outcome in pool.map(self._run_cell_safe, todo):
                if outcome is None:
                    continue
                result.cells.append(outcome)
                status = outcome["status"]
                if status == "complete":
                    result.completed += 1
                elif status == "skipped":
                    result.skipped += 1
                else:
                    result.failed += 1
        for g, st in self._groups.items():
            if st.sid is not None:
                result.submissions["|".join(g)] = st.sid
        self.events.emit("run_done", run_id=self.run_id, completed=result.completed,
                         skipped=result.skipped, failed=result.failed,
                         submissions=result.submissions)
        return result

    def _run_cell_safe(self, cell: Cell) -> dict | None:
        if self.cancel.is_set():
            return None
        try:
            return self._run_cell(cell)
        except PerfArenaError as e:
            return self._record(cell, "failed", reason=f"{type(e).__name__}: {e}",
                                transient=bool(getattr(e, "transient", False)))

    # ---- one cell ---------------------------------------------------------------

    def _record(self, cell: Cell, status: str, **fields) -> dict:
        self.ledger.record(cell.ledger_key(self.spec), status, **fields)
        out = {"cell": cell.tuple_key, "status": status, **fields}
        if status != "complete":
            kind = {"dispatch_failed": "dispatch_failed", "poll_timeout": "poll_timeout",
                    "skipped": "cell_skipped"}.get(status)
            if kind:
                self.events.emit(kind, cell=cell.tuple_key, **fields)
        self.events.emit("cell_done", **out)
        return out

    def _group(self, cell: Cell) -> _Group:
        with self._groups_lock:
            return self._groups.setdefault(cell.group(), _Group())

    def cell_body(self, cell: Cell) -> dict:
        """The submission fields of the cell's group (also the upsert key)."""
        prompt = self.prompts[cell.prompt_name]
        m, spec = cell.model, self.spec
        flags = sanitize_compile_flags(spec.compile_flags_for(cell.language))
        return {
            "model_name": m.name, "model_version": m.effective_version,
            "language": cell.language,
            "prompt_template": prompt["system_prompt"] if prompt else system_prompt(cell.language),
            "prompt_id": prompt["id"] if prompt else None,
            "model_params": {**m.params, **({} if m.think else {"think": False}),
                             **({"compile_flags": flags} if flags else {})},
            "n_samples_per_problem": spec.sampling.k, "n_rounds": spec.rounds.n,
            "include_hints": spec.hints, "optimization_target": spec.target,
            "stop_on_accept": spec.rounds.n > 1 and spec.rounds.stop_on_accept,
            "competition_slug": spec.competition,
            "harness_slug": spec.oracles.performance,
            "correctness_oracle_slug": spec.oracles.correctness,
            "agent_slug": spec.agent,
            "include_fully_accepted": spec.append_fully_accepted,
        }

    def _lookup(self, cell: Cell, group: _Group) -> None:
        """Find the group's existing submission (without creating one) and
        the problems it already covers: an attempt with runs and no dispatch
        warning (the sweep's rule)."""
        if group.looked_up or self.spec.new_submission:
            group.looked_up = True
            return
        group.looked_up = True
        found = self.arena.submissions.upsert({**self.cell_body(cell), "create": False})
        group.sid = found.get("submission_id")
        if group.sid is not None:
            for a in self.arena.submissions.attempts(group.sid):
                if (a.get("n_runs") or 0) > 0 and not a.get("dispatch_warning"):
                    group.covered.add(a["problem_slug"])

    def _problem(self, slug: str) -> dict:
        if slug not in self._problems:
            self._problems[slug] = self.arena.problems.get(slug)
        return self._problems[slug]

    def _run_cell(self, cell: Cell) -> dict:
        key = cell.ledger_key(self.spec)
        prior = self.ledger.get(key)
        group = self._group(cell)
        with group.lock:
            self._lookup(cell, group)
            covered = cell.slug in group.covered
        self.events.emit("cell_started", cell=cell.tuple_key)
        if covered:
            return self._record(cell, "complete", reason="already_in_existing_submission",
                                submission_id=group.sid)
        problem = self._problem(cell.slug)
        prompt = self.prompts[cell.prompt_name]
        if (PromptBuilder.needs_code_template(prompt)
                and not is_clbg_statement(problem.get("content") or "")
                and not (problem.get("code_snippets") or {}).get(cell.language)):
            return self._record(cell, "skipped", reason="no_code_snippet_for_language")

        chains = self.strategy.chains(cell.model)
        finals = []
        for chain in chains:
            if self.cancel.is_set():
                return self._record(cell, "failed", reason="cancelled", transient=True)
            out = self._run_chain(cell, group, problem, prompt, chain,
                                  single=len(chains) == 1, resume=prior is not None)
            if out["status"] != "complete":
                return self._record(cell, out.pop("status"), **out)
            finals.append(out)
        last = finals[-1]
        return self._record(cell, "complete", submission_id=group.sid,
                            attempt_id=last["attempt_id"], verdict=last["verdict"],
                            accepted=any(f["accepted"] for f in finals),
                            rounds_used=last["rounds_used"],
                            solved_at_round=last["solved_at_round"],
                            samples=[{k: f[k] for k in ("attempt_id", "verdict", "accepted")}
                                     for f in finals] if len(finals) > 1 else None,
                            n_runs=last.get("n_runs"), leaf_hash=last["leaf_hash"])

    def _run_chain(self, cell, group, problem, prompt, chain, *, single: bool,
                   resume: bool) -> dict:
        spec, model = self.spec, cell.model
        generator = Generator(self.providers[model.name],
                              prompts=PromptBuilder(target=spec.target, hints=spec.hints),
                              timeout_s=spec.inference_timeout_s)
        salt = cache_salt(target=spec.target, hints=spec.hints,
                          temperature=chain.params.get("temperature"),
                          rounds=self.strategy.rounds, think=model.think)
        history: list[dict] = []
        prev_id: int | None = None
        state: dict = {}
        leaf = None
        for r in range(self.strategy.rounds):
            code, trace, prefailed, failed = self._generate(
                cell, generator, prompt, problem, chain, r, history,
                use_cache=single and r == 0, salt=salt)
            if failed is not None:
                return failed
            built = build_attempt(
                code, trace, slug=cell.slug, language=cell.language, model=model.name,
                model_version=model.effective_version, pricebook=self.pricebook,
                prefailed_reason=prefailed, round_index=r, previous_attempt_id=prev_id,
                model_digest=self.digests.get(model.name),
                operator_flags=spec.compile_flags_for(cell.language),
                sign_key=spec.sign_key)
            leaf = built.leaf_hash
            resumed = self._inflight(group, cell, r) if resume else None
            if resumed is not None:
                aid = resumed
            else:
                try:
                    aid = self._submit(cell, group, built.body)
                except ArenaHTTPError as e:
                    return {"status": "failed", "reason": f"submit (round {r}): {e}",
                            "transient": e.transient, "leaf_hash": leaf,
                            "submission_id": group.sid}
            self.events.emit("submitted", cell=cell.tuple_key, round=r, attempt_id=aid,
                             submission_id=group.sid, resumed=resumed is not None)
            if prefailed and resumed is None:
                # Recorded without dispatch: nothing to measure or poll.
                return {"status": "failed", "reason": prefailed, "transient": False,
                        "attempt_id": aid, "submission_id": group.sid, "leaf_hash": leaf,
                        "rounds_used": r + 1}
            state = self._await(cell, group, aid)
            if state.get("status") in ("poll_timeout", "dispatch_failed"):
                return {**state, "leaf_hash": leaf, "rounds_used": r + 1}
            self.events.emit("verdict", cell=cell.tuple_key, round=r, attempt_id=aid,
                             verdict=state.get("verdict"), accepted=state.get("accepted"))
            history.append({"round_index": r, "code": code, "judge": distill_judge(state)})
            prev_id = aid
            if single and r == 0 and self.cache is not None and not spec.keep_cache:
                self.cache.drop(cell.tuple_key, salt)
            if self.strategy.stop_after(r, bool(state.get("accepted"))):
                break
        solved = next((h["round_index"] for h in history if h["judge"].get("accepted")), None)
        return {"status": "complete", "attempt_id": prev_id, "verdict": state.get("verdict"),
                "accepted": bool(state.get("accepted")), "n_runs": state.get("n_runs"),
                "rounds_used": len(history), "solved_at_round": solved, "leaf_hash": leaf}

    def _generate(self, cell, generator, prompt, problem, chain, r, history, *,
                  use_cache: bool, salt: str):
        """(code, trace, prefailed_reason, failure-record-or-None)."""
        model = cell.model
        if use_cache and self.cache is not None:
            hit = self.cache.load(cell.tuple_key, salt)
            if hit is not None and looks_like(hit[0], cell.language):
                self.events.emit("generated", cell=cell.tuple_key, round=r,
                                 chars=len(hit[0]), cached=True)
                return hit[0], hit[1], None, None
        t0 = time.time()
        try:
            gen = generator.generate(model.name, prompt, problem, cell.language,
                                     dict(chain.params),
                                     RoundState(r, self.strategy.rounds, history))
        except (ProviderError, TimeoutError) as e:
            verdict = (generation_failure_verdict(e)
                       if self.spec.record_generation_failures else None)
            self.events.emit("generation_failed", cell=cell.tuple_key, round=r,
                             error=str(e), verdict=verdict)
            if verdict is None:
                return None, None, None, {
                    "status": "failed", "reason": f"generation (round {r}): {e}",
                    "transient": bool(getattr(e, "transient", False)),
                    "submission_id": None}
            trace = failed_trace(model=model.name, provider=self.providers[model.name].name,
                                 params=dict(chain.params), verdict=verdict, error=e,
                                 wall_clock_ms=int((time.time() - t0) * 1000))
            return "", trace, verdict, None
        prefailed = None if looks_like(gen.code, cell.language) else "wrong_language_extracted"
        self.events.emit("generated", cell=cell.tuple_key, round=r, chars=len(gen.code),
                         cached=False, prefailed=prefailed)
        if prefailed is None and use_cache and self.cache is not None:
            self.cache.save(cell.tuple_key, salt, gen.code, gen.trace)
        return gen.code, gen.trace, prefailed, None

    def _pace(self, cell: Cell) -> None:
        """LeetCode pacing before a submit: a short wait on the same
        problem+language, a longer one when it changes."""
        if not self.spec.touches_leetcode:
            return
        with self._pace_lock:
            pair = (cell.slug, cell.language)
            if self._prev_pair is not None:
                self.sleep(self.spec.inter_problem_delay_s if pair != self._prev_pair
                           else self.spec.inter_cell_delay_s)
            self._prev_pair = pair

    def _submit(self, cell: Cell, group: _Group, body: dict) -> int:
        self._pace(cell)
        with group.lock:
            if group.sid is None:
                if self.spec.new_submission:
                    create = {k: v for k, v in self.cell_body(cell).items()
                              if k != "include_fully_accepted"}
                    out = self.arena.submissions.create({**create, "attempts": [body]})
                    group.sid = out["submission_id"]
                    group.covered.add(cell.slug)
                    return self._attempt_id(out, group.sid, cell, body)
                group.sid = self.arena.submissions.upsert(self.cell_body(cell))["submission_id"]
            sid = group.sid
        out = self.arena.submissions.append(sid, [body])
        with group.lock:
            group.covered.add(cell.slug)
        return self._attempt_id(out, sid, cell, body)

    def _attempt_id(self, out: dict, sid: int, cell: Cell, body: dict) -> int:
        ids = out.get("attempt_ids")
        if ids:
            return ids[0]
        # Older server: the newest attempt of this problem and round.
        rows = [a for a in self.arena.submissions.attempts(sid)
                if a.get("problem_slug") == cell.slug
                and (a.get("round_index") or 0) == body["round_index"]]
        return max(a["attempt_id"] for a in rows)

    def _inflight(self, group: _Group, cell: Cell, r: int) -> int | None:
        """An attempt for this problem and round that an earlier run left
        in flight (queued/Pending, not prefailed, no dispatch warning)."""
        if group.sid is None:
            return None
        rows = [a for a in self.arena.submissions.attempts(group.sid)
                if a.get("problem_slug") == cell.slug and (a.get("round_index") or 0) == r
                and not a.get("dispatch_warning") and not a.get("prefailed_reason")
                and a.get("status") in ("queued", "Pending")]
        return max((a["attempt_id"] for a in rows), default=None)

    def _admin(self) -> bool:
        if self._is_admin is None:
            try:
                self._is_admin = self.arena.admin.me().get("role") == "admin"
            except PerfArenaError:
                self._is_admin = False
        return self._is_admin

    def _await(self, cell: Cell, group: _Group, aid: int) -> dict:
        timeout = self.spec.effective_poll_timeout_s
        st = self.waiter.wait(aid, timeout, submission_id=group.sid)
        if st is None:
            return {"status": "poll_timeout", "attempt_id": aid, "timeout_s": timeout,
                    "submission_id": group.sid}
        if st.get("state") == "failed":
            warn = st.get("dispatch_warning") or "dispatch failed without a warning"
            if any(s in warn.lower() for s in _RECOVERABLE) and self._admin():
                self.sleep(self.spec.recovery_wait_s)
                self.arena.submissions.redispatch(group.sid)
                st = self.waiter.wait(aid, timeout, submission_id=group.sid)
            if st is None or st.get("state") == "failed":
                return {"status": "dispatch_failed", "attempt_id": aid,
                        "warning": (st or {}).get("dispatch_warning") or warn,
                        "submission_id": group.sid}
        return st
