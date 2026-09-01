"""HTTP client to a remote arena-local harness runner.

This is the counterpart to leetbot for `Harness.kind == 'arena-local'` (and
`custom` when `config.endpoint` is set). Where LeetCode is a fire-and-poll
GraphQL flow that requires a shared credential pool, an arena-local runner
is a plain HTTP server on a machine the operator controls — see
`examples/harness_runner_server.py` for a reference implementation.

Protocol
========

The runner MUST expose two endpoints. `Harness.config.endpoint` is the base
URL; `Harness.config.auth_token`, when present, is sent as
`Authorization: Bearer <token>` on every request.

    GET /healthz
        → 200 { "ok": true, "hostname": "gpu-01", "measurement": "rapl+ru_maxrss" }

        Used by the SPA's "Test connection" button and by the dispatcher's
        pre-flight so a dead runner fails fast (dispatch_warning stamp; no
        Run rows), same shape as LeetCode auth failure. Response body is
        opaque — anything with `ok: true` counts as healthy.

    POST /run
        headers: Authorization: Bearer <token>   (when auth_token is set)
        body: {
            "title_slug":    "two-sum",
            "language":      "python3",
            "code":          "class Solution: ...",
            "repeats":       3,                # runner MUST honour this
            "meta": {                          # opaque; passed through
                "submission_id": 42,
                "attempt_id":    5793
            }
        }
        → 200 { "runs": [ RunResult, ...N=repeats ] }

Where `RunResult` has the shape the existing dispatcher already knows how
to persist (matches `Run` columns 1:1 so nothing else in the pipeline needs
to change):

    {
        "status_msg":                 "Accepted",       # required
                                                        # "Wrong Answer" |
                                                        # "Runtime Error" |
                                                        # "Time Limit Exceeded" |
                                                        # "Compile Error"
        "runtime_ms":                 12.34,            # execution time in ms
        "memory_mb":                  18.2,             # peak RSS in MB
        "runtime_energy_j":           0.000486,         # optional — arena-local's
                                                        # main value-add
        "runtime_energy_percentile":  73.1,             # optional
        "runtime_percentile":         84.0,             # optional
        "memory_percentile":          65.0,             # optional
        "total_correct":              63,               # optional — for the
                                                        # per-run breakdown UI
        "total_testcases":            63,
        "submission_id_external":     null,             # runners can echo an
                                                        # opaque handle here
        "raw_response":               { ... },          # optional; stored on
                                                        # Run.raw_response
        "leetcode_full_response":     null              # never populated by
                                                        # arena-local runners
    }

Failure semantics
=================

* Runner reachable but the code failed the correctness check → return a
  RunResult with the appropriate `status_msg` (Wrong Answer / Runtime
  Error / etc.). Runtime numbers may still be present (they usually are
  for Runtime Error since the code did run for a while); the dispatcher
  filters them out of the accepted-only aggregates.

* Runner unreachable / 5xx / HTTP timeout → we synthesize N synthetic
  "Dispatch error" runs and let the outer dispatcher's SYSTEM-failure
  detection do the rest (stamps `dispatch_warning`, writes ZERO Run
  rows, next dispatch call retries). Matches leetbot's behavior for
  transient LC outages.

* Runner returns 200 but the payload is malformed → treated the same
  as an unreachable runner: dispatch_warning + retry, so we never
  persist partial garbage.

Notes for future us
===================

* One HTTP call carries all N repeats. Some measurement harnesses need
  to warm caches across repeats; a per-run streaming variant (chunked
  Server-Sent Events, one JSON object per repeat) is a straightforward
  extension — swap the httpx.post for a `stream=True` iteration and
  keep calling `on_run_completed` per line.

* No credential pool. The endpoint is a single fixed URL per Harness;
  scale-out is the runner's problem (put a load balancer in front of
  N boxes and stamp them all with the same auth_token).

* No inter-submission delay. The remote machine is ours, not LC's, so
  there's no anti-bot heuristic to appease. The dispatcher's outer
  pacing (`INTER_DELAY`, `wait_s`) only kicks in for `harness.kind ==
  'leetcode'`; arena-local dispatches back-to-back.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx

from .models import CorrectnessOracle, Harness, Problem


_DEFAULT_TIMEOUT_S = 300.0


# Both Harness and CorrectnessOracle share the same runner-config shape
# (`config.endpoint`, `config.auth_token`, `config.timeout_s`, plus a `slug`
# for diagnostics). The client helpers below duck-type on those two
# attributes so a single implementation drives both /measure and /validate.
_Runner = Harness | CorrectnessOracle


def _client(runner: _Runner) -> tuple[httpx.Client, str]:
    """Build a client for the runner's endpoint. Raises ValueError when
    the config is missing or malformed — callers should stamp
    dispatch_warning and abort the attempt in that case."""
    cfg = runner.config or {}
    endpoint = (cfg.get("endpoint") or "").rstrip("/")
    if not endpoint:
        kind_label = (
            "harness" if isinstance(runner, Harness) else "correctness oracle"
        )
        page = "/harnesses" if isinstance(runner, Harness) else "/oracles"
        raise ValueError(
            f"{kind_label} '{runner.slug}' has no endpoint configured — "
            f"set config.endpoint on {page} to the runner's base URL"
        )
    token = cfg.get("auth_token") or ""
    timeout = float(cfg.get("timeout_s") or _DEFAULT_TIMEOUT_S)
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    client = httpx.Client(
        base_url=endpoint, headers=headers, timeout=timeout,
        # Retries live in the outer dispatcher — we surface transport
        # errors as SYSTEM failures so its backoff curve owns them.
        follow_redirects=False,
    )
    return client, endpoint


def probe_healthz(runner: _Runner) -> tuple[bool, str]:
    """Ping the runner's /healthz. Returns (ok, info-line). Used by the
    SPA's admin "Test connection" button and the dispatcher pre-flight."""
    try:
        client, endpoint = _client(runner)
    except ValueError as e:
        return False, str(e)
    try:
        with client:
            r = client.get("/healthz")
    except httpx.HTTPError as e:
        return False, f"transport error hitting {endpoint}/healthz: {e}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code} from {endpoint}/healthz"
    try:
        body = r.json()
    except json.JSONDecodeError:
        return False, f"{endpoint}/healthz returned non-JSON"
    if not body.get("ok"):
        return False, f"{endpoint}/healthz replied {body!r} (no ok flag)"
    tail = ""
    if body.get("hostname"):
        tail += f" · host={body['hostname']}"
    if body.get("measurement"):
        tail += f" · measurement={body['measurement']}"
    if body.get("correctness"):
        tail += f" · correctness={body['correctness']}"
    if body.get("endpoints"):
        tail += f" · endpoints={','.join(body['endpoints'])}"
    return True, f"runner healthy at {endpoint}{tail}"


def _synthetic_failure(repeats: int, status_msg: str, note: str) -> list[dict]:
    """Same shape leetbot's SYSTEM-failure branch produces. `note` is
    surfaced on the attempt's dispatch_warning by the outer dispatcher."""
    return [
        {
            "status_msg": status_msg,
            "runtime_ms": None,
            "memory_mb": None,
            "runtime_percentile": None,
            "memory_percentile": None,
            "runtime_energy_j": None,
            "runtime_energy_percentile": None,
            "total_correct": None,
            "total_testcases": None,
            "submission_id_external": None,
            "raw_response": {"note": note},
            "leetcode_full_response": None,
        }
        for _ in range(repeats)
    ]


_VALID_STATUS_MSGS = frozenset({
    "Accepted", "Wrong Answer", "Runtime Error", "Time Limit Exceeded",
    "Memory Limit Exceeded", "Output Limit Exceeded", "Compile Error",
})


def _normalise_run(row: dict) -> dict:
    """Force a runner-reported run into the shape `_persist_run` /
    `_aggregate_attempt` expect. Fields the runner didn't set land as
    None; unknown status strings pass through so the operator can debug.

    `error_log`, when the runner provided it, is folded INSIDE
    raw_response.error_log so it lands on the Run row (which has a
    JSON raw_response column but no dedicated error_log column). The
    SPA can then surface `run.raw_response.error_log` on any
    non-Accepted verdict without a schema change.
    """
    status = row.get("status_msg") or "Runtime Error"
    raw_response = row.get("raw_response") or {}
    if row.get("error_log") and isinstance(raw_response, dict):
        # Prefer whatever the runner sent under raw_response.error_log
        # if it's already present, but backfill from the top-level
        # `error_log` when the runner used the newer top-level slot.
        raw_response = {**raw_response,
                        "error_log": raw_response.get("error_log")
                                     or row["error_log"]}
    return {
        "status_msg":                status,
        "runtime_ms":                _f(row.get("runtime_ms")),
        "memory_mb":                 _f(row.get("memory_mb")),
        "runtime_percentile":        _f(row.get("runtime_percentile")),
        "memory_percentile":         _f(row.get("memory_percentile")),
        "runtime_energy_j":          _f(row.get("runtime_energy_j")),
        "runtime_energy_percentile": _f(row.get("runtime_energy_percentile")),
        "total_correct":             _i(row.get("total_correct")),
        "total_testcases":           _i(row.get("total_testcases")),
        "submission_id_external":    row.get("submission_id_external"),
        # Per function call metrics from the looped curated methodology (None on
        # runners that don't emit them).
        "iterations":                _i(row.get("iterations")),
        "energy_j_per_op":           _f(row.get("energy_j_per_op")),
        "time_us_per_op":            _f(row.get("time_us_per_op")),
        # Measurement provenance — the energy tool used and a descriptor of
        # the machine it ran on. Passed straight through so the dispatcher
        # can stamp them on the submission (one tool / one machine per sub).
        "energy_tool":               row.get("energy_tool"),
        "machine":                   row.get("machine"),
        "config":                    row.get("config"),
        "raw_response":              raw_response,
        "leetcode_full_response":    None,     # arena-local ⇒ never LC
    }


def _f(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _i(x: Any) -> int | None:
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def dispatch_via_arena_local(
    problem: Problem, language: str, code: str, repeats: int,
    harness: Harness,
    *,
    on_run_completed: Callable[[dict, int], None] | None = None,
    meta: dict | None = None,
    metrics: list[str] | None = None,
) -> list[dict]:
    """POST the code to the harness runner and stream the N results back.

    Signature mirrors `_real_dispatch_repeated` so the outer dispatcher
    can call either interchangeably. `on_run_completed(run, idx_1based)`
    fires per repeat exactly like the leetbot path so the SPA's live
    runs-landed counter advances immediately.

    Returns a list of N run dicts. Transport / auth / malformed-payload
    failures all collapse to `Dispatch error` synthetic runs so the
    outer dispatcher's SYSTEM_FAILURE_STATUSES filter treats them as
    retry-later without writing garbage Runs to the DB.
    """
    try:
        client, endpoint = _client(harness)
    except ValueError as e:
        return _synthetic_failure(repeats, "Dispatch error", str(e))

    body = {
        "title_slug": problem.title_slug,
        "language":   language,
        "code":       code,
        "repeats":    repeats,
        # Build the model's code ONCE for this batch; the runner then measures
        # `repeats` times against that single artifact (no per-repeat compile).
        "compile":    True,
        "meta":       meta or {},
    }
    # Metric selector — controls which physical runs the runner's
    # handler executes internally (make measure for time/energy, make
    # run for memory). None → runner default (all three). Callers who
    # only care about energy pass ["energy"] and pay only for that
    # physical pass.
    if metrics is not None:
        body["metrics"] = list(metrics)
    started = time.time()
    try:
        with client:
            # Prefer the split-endpoint route when the runner exposes
            # it. Falls back to /run for older runners that don't yet
            # know about /measure.
            r = client.post("/measure", json=body)
            if r.status_code == 404:
                r = client.post("/run", json=body)
    except httpx.TimeoutException as e:
        return _synthetic_failure(
            repeats, "Dispatch timeout",
            f"runner {endpoint}/run exceeded timeout after "
            f"{time.time() - started:.1f}s: {e}",
        )
    except httpx.HTTPError as e:
        return _synthetic_failure(
            repeats, "Dispatch error",
            f"runner {endpoint}/run transport error: {e}",
        )

    if r.status_code == 401:
        return _synthetic_failure(
            repeats, "Auth failed",
            f"runner {endpoint}/run returned 401 — set "
            f"Harness.config.auth_token to the runner's expected token",
        )
    if r.status_code == 429:
        return _synthetic_failure(
            repeats, "Rate limited",
            f"runner {endpoint}/run returned 429; back off and retry",
        )
    if r.status_code >= 500:
        return _synthetic_failure(
            repeats, "Dispatch error",
            f"runner {endpoint}/run returned HTTP {r.status_code}: "
            f"{r.text[:200]}",
        )
    if r.status_code != 200:
        return _synthetic_failure(
            repeats, "Dispatch error",
            f"runner {endpoint}/run returned HTTP {r.status_code}: "
            f"{r.text[:200]}",
        )

    try:
        payload = r.json()
    except json.JSONDecodeError as e:
        return _synthetic_failure(
            repeats, "Dispatch parse error",
            f"runner {endpoint}/run returned non-JSON: {e}",
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
        return _synthetic_failure(
            repeats, "Dispatch parse error",
            f"runner {endpoint}/run payload missing 'runs' array",
        )

    raw_runs = payload["runs"]
    if len(raw_runs) != repeats:
        # Fill or truncate so aggregate math stays valid, and stamp the
        # count mismatch as a note on every synthesized slot the runner
        # didn't fill. Runs the runner DID return pass through unchanged.
        normalised: list[dict] = []
        for i in range(repeats):
            if i < len(raw_runs):
                normalised.append(_normalise_run(raw_runs[i]))
            else:
                normalised.append({
                    "status_msg": "Dispatch parse error",
                    "runtime_ms": None, "memory_mb": None,
                    "runtime_percentile": None, "memory_percentile": None,
                    "runtime_energy_j": None,
                    "runtime_energy_percentile": None,
                    "total_correct": None, "total_testcases": None,
                    "submission_id_external": None,
                    "raw_response": {
                        "note": (
                            f"runner returned {len(raw_runs)} runs but "
                            f"{repeats} were requested"
                        ),
                    },
                    "leetcode_full_response": None,
                })
    else:
        normalised = [_normalise_run(row) for row in raw_runs]

    # Fire the live-progress callback for each returned run. The runner
    # returned everything in one shot, so the timestamps here are
    # essentially the same instant — but the callback shape matches the
    # streaming leetbot path so the SPA counter advances 0/N → N/N
    # cleanly regardless of which harness produced the runs.
    if on_run_completed is not None:
        for i, run in enumerate(normalised, start=1):
            on_run_completed(run, i)

    return normalised


# ---------- correctness path ----------------------------------------------


def validate_via_arena_local(
    problem: Problem, language: str, code: str,
    oracle: CorrectnessOracle,
    *,
    meta: dict | None = None,
) -> dict:
    """POST the code to the oracle runner's /validate and return a single
    verdict dict.

    Return shape (a subset of Run column shape — the outer dispatcher
    will fill perf fields with None when persisting):

        {
            "status_msg":    "Accepted" | "Wrong Answer" | ...,
            "raw_response":  { ... } | None,
            # Only set on transport/auth/parse failures:
            "system_failure": True | False,
        }

    On system failure the outer dispatcher treats the attempt like a
    leetbot-side transient outage: stamp `dispatch_warning`, write NO
    Run rows, next dispatch call retries. That's why `system_failure`
    is a top-level flag rather than a `Dispatch error` status_msg — a
    Wrong Answer verdict from the oracle IS a real outcome we want to
    persist, so we need to distinguish a real "not accepted" from an
    "oracle unreachable" without conflating them.
    """
    try:
        client, endpoint = _client(oracle)
    except ValueError as e:
        return {"status_msg": "Dispatch error",
                "raw_response": {"note": str(e)},
                "system_failure": True}

    body = {
        "title_slug": problem.title_slug,
        "language":   language,
        "code":       code,
        "meta":       meta or {},
    }
    started = time.time()
    try:
        with client:
            r = client.post("/validate", json=body)
    except httpx.TimeoutException as e:
        return {"status_msg": "Dispatch timeout",
                "raw_response": {
                    "note": f"oracle {endpoint}/validate exceeded timeout after "
                            f"{time.time() - started:.1f}s: {e}",
                },
                "system_failure": True}
    except httpx.HTTPError as e:
        return {"status_msg": "Dispatch error",
                "raw_response": {
                    "note": f"oracle {endpoint}/validate transport error: {e}",
                },
                "system_failure": True}

    if r.status_code == 401:
        return {"status_msg": "Auth failed",
                "raw_response": {
                    "note": f"oracle {endpoint}/validate returned 401 — set "
                            f"CorrectnessOracle.config.auth_token to the "
                            f"runner's expected token",
                },
                "system_failure": True}
    if r.status_code == 429:
        return {"status_msg": "Rate limited",
                "raw_response": {
                    "note": f"oracle {endpoint}/validate returned 429",
                },
                "system_failure": True}
    if r.status_code == 404:
        # The runner explicitly said "no validate handler configured".
        # That's an operator error, not a transient — surface it as
        # Dispatch error so the dispatch_warning message is clear.
        return {"status_msg": "Dispatch error",
                "raw_response": {
                    "note": f"oracle {endpoint}/validate returned 404 — the "
                            f"runner has no --validate-handler configured. "
                            f"Add one, or unbind the correctness oracle from "
                            f"this competition/submission.",
                },
                "system_failure": True}
    if r.status_code >= 500 or r.status_code != 200:
        return {"status_msg": "Dispatch error",
                "raw_response": {
                    "note": f"oracle {endpoint}/validate returned HTTP "
                            f"{r.status_code}: {r.text[:200]}",
                },
                "system_failure": True}

    try:
        payload = r.json()
    except json.JSONDecodeError as e:
        return {"status_msg": "Dispatch parse error",
                "raw_response": {
                    "note": f"oracle {endpoint}/validate returned non-JSON: {e}",
                },
                "system_failure": True}
    verdict = payload.get("verdict") if isinstance(payload, dict) else None
    if not isinstance(verdict, dict):
        return {"status_msg": "Dispatch parse error",
                "raw_response": {
                    "note": f"oracle {endpoint}/validate payload missing "
                            f"'verdict' object",
                },
                "system_failure": True}
    status = verdict.get("status_msg") or "Runtime Error"
    # Fold error_log into raw_response.error_log so the persist path
    # (which stores raw_response on the Run row) surfaces it without
    # a schema change. Runners that emit raw_response.error_log
    # directly are honored too.
    raw = verdict.get("raw_response") or {}
    if verdict.get("error_log") and isinstance(raw, dict):
        raw = {**raw, "error_log": raw.get("error_log") or verdict["error_log"]}
    return {
        "status_msg":      status,
        # Full-suite validators report cases passed before the verdict (==
        # LeetCode total_correct) and the total; the dispatcher stamps these
        # onto the Run row so the Tests column shows K/N instead of ?/?.
        "total_correct":   verdict.get("total_correct"),
        "total_testcases": verdict.get("total_testcases"),
        "raw_response":    raw,
        "system_failure":  False,
    }

