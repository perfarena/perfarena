#!/usr/bin/env python3
"""Reference PerfArena harness runner — minimal HTTP shell.

The design goal here is: the HTTP layer must not distort the numbers the
benchmark itself is trying to measure. That means:

* **Zero runtime dependencies beyond the stdlib.** No FastAPI, no
  pydantic, no uvicorn, no anyio event loop competing with the child
  process for the CPU while the child is being measured. Just
  `http.server.HTTPServer` on the main thread.

* **No background timers or polling while a run is in flight.** The
  request handler blocks in `os.waitpid()` — the kernel wakes the thread
  only when the child exits (or on the timeout SIGALRM). No 100 ms tick,
  no keepalive scheduler.

* **Serial execution.** A process-global `threading.Lock` ensures at most
  one benchmark runs at a time. Concurrent /run requests queue on the
  lock (kernel-side blocking wait). Two runs never fight for the same L2
  or the same core, and there's no "background request that measures
  itself into the measurement".

* **Single-threaded server.** We use `HTTPServer` (not `ThreadingHTTPServer`)
  so only one connection can be alive at a time. GET /healthz and the
  waiting POST /run requests are handled by the same thread; there is no
  worker pool sitting idle burning cache lines.

* **No access logging.** `log_message` is silenced by default so that
  even stderr writes don't happen during a run. Enable with --verbose
  when debugging.

Deploy on the remote box:

    python harness_runner_server.py \\
        --host 0.0.0.0 --port 8787 --auth-token <shared-secret>

Then in the PerfArena SPA on /harnesses set:

    { "endpoint":   "http://<runner-host>:8787",
      "auth_token": "<shared-secret>",
      "timeout_s":  300 }

Protocol
========

    GET  /healthz  → 200 { ok, hostname, measurement, correctness, busy,
                           busy_since, endpoints: [...] }

Two orthogonal work endpoints — correctness and perf are separated so
either can be scaled / retired / re-tuned independently on the
arena side (Harness registry drives /measure; CorrectnessOracle
registry drives /validate):

    POST /validate                       (correctness oracle)
        headers: Authorization: Bearer <token>
        body:    { title_slug, language, code, meta? }
        → 200   { verdict: { status_msg, raw_response } }
        → 401 / 400 / 429 as with /measure below
        → 404 when no --validate-handler was configured on this runner

    POST /measure                        (perf oracle)
        headers: Authorization: Bearer <token>
        body:    {
            title_slug, language, code, repeats, meta?,
            # Optional metric selector — controls which physical
            # runs the handler executes internally. Defaults to all
            # three. `time` and `energy` share one run (`make
            # measure` under the profiler); `memory` costs a
            # separate `make run` under RUSAGE_CHILDREN.
            metrics?: ["time" | "energy" | "memory", ...]
        }
        → 200   { runs: [ RunResult, ... N=repeats ] }
        → 401 on missing/wrong bearer
        → 429 when another run holds the lock AND --reject-when-busy set
        → 404 when no --measure-handler / --handler was configured

    POST /run                            (back-compat: measure)
        Same shape as POST /measure; kept so older clients that hit
        /run still work. New callers should use /measure or /validate.

    RunResult = {
        status_msg,                # required — see arena_local_runner.py
        runtime_ms,                # float | None
        memory_mb,                 # float | None
        runtime_energy_j,          # float | None
        runtime_percentile,        # float | None
        memory_percentile,         # float | None
        runtime_energy_percentile, # float | None
        total_correct,             # int | None
        total_testcases,           # int | None
        submission_id_external,    # str | None
        raw_response,              # dict | None — arbitrary diagnostic
    }

Plugging in your backends
=========================

Two independent flags:

    --measure-handler  mymodule:measure_once
    --validate-handler mymodule:validate_once

`--handler` remains as an alias for `--measure-handler` for back-compat.

Both callables share the signature (title_slug: str, language: str,
code: str) -> dict. They are called with the process-global lock
held, so you can safely touch RAPL / powermetrics / NVML from
either without another run racing you.

    measure_once  → RunResult (perf fields populated)
    validate_once → { status_msg, raw_response }  # perf fields ignored
"""
from __future__ import annotations

import argparse
import http.server
import importlib
import json
import os
import resource
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable


# ---------- global state (module-scope; single-process server) --------------

# Held for the entire duration of one /run request (including all repeats).
# Concurrent /run requests park in `acquire()` — no polling, kernel-side
# blocking wake when the current holder releases.
_RUN_LOCK = threading.Lock()

# What the healthz endpoint reports and what --reject-when-busy checks.
# Set inside `_with_run_lock`.
_BUSY: dict[str, Any] = {"busy": False, "since": None, "meta": None}

# CLI-configured; set by main().
_AUTH_TOKEN: str | None = None
# Perf handler — populates runtime_ms / memory_mb / runtime_energy_j.
# Also served by the back-compat POST /run route.
_MEASURE_HANDLER: Callable[[str, str, str], dict] | None = None
_MEASUREMENT_LABEL: str = ""
# Correctness handler — returns just status_msg (+ raw_response).
# Independent from the perf handler so a runner can serve one, both,
# or neither.
_VALIDATE_HANDLER: Callable[[str, str, str], dict] | None = None
_CORRECTNESS_LABEL: str = ""
_REJECT_WHEN_BUSY: bool = False
_VERBOSE: bool = False


# ---------- reference measurement backend ----------------------------------
#
# YOU almost certainly want to swap this. It runs the code as a python3
# subprocess and captures wall-clock (perf_counter_ns) + peak RSS
# (getrusage delta). Energy is left as None — plug your RAPL /
# powermetrics / NVML entry point where the comment says so.


def _measure_python_subprocess(code: str, timeout_s: float) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code); code_path = f.name
    try:
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        t0 = time.perf_counter_ns()
        try:
            proc = subprocess.run(
                [sys.executable, code_path],
                capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return {
                "status_msg": "Time Limit Exceeded",
                "runtime_ms": None, "memory_mb": None,
                "runtime_energy_j": None,
                "runtime_percentile": None,
                "memory_percentile": None,
                "runtime_energy_percentile": None,
                "total_correct": None, "total_testcases": None,
                "submission_id_external": None,
                "raw_response": {"note": f"exceeded {timeout_s}s wall clock"},
            }
        t1 = time.perf_counter_ns()
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        rss_delta = after.ru_maxrss - before.ru_maxrss
        rss_mb = (rss_delta / 1024.0 if sys.platform == "linux"
                  else rss_delta / (1024.0 * 1024.0))
        base = {
            "runtime_ms": (t1 - t0) / 1e6,
            "memory_mb": max(rss_mb, 0.0),
            "runtime_energy_j": None,      # ← plug your measurement here
            "runtime_percentile": None,
            "memory_percentile": None,
            "runtime_energy_percentile": None,
            "total_correct": None, "total_testcases": None,
            "submission_id_external": None,
        }
        if proc.returncode != 0:
            base["status_msg"] = "Runtime Error"
            base["raw_response"] = {
                "stderr": proc.stderr[-2000:],
                "returncode": proc.returncode,
            }
        else:
            base["status_msg"] = "Accepted"
            base["raw_response"] = None
        return base
    finally:
        try: os.unlink(code_path)
        except OSError: pass


def default_run_once(title_slug: str, language: str, code: str) -> dict:
    if language not in ("python3", "python"):
        return {
            "status_msg": "Compile Error",
            "runtime_ms": None, "memory_mb": None,
            "runtime_energy_j": None,
            "runtime_percentile": None,
            "memory_percentile": None,
            "runtime_energy_percentile": None,
            "total_correct": None, "total_testcases": None,
            "submission_id_external": None,
            "raw_response": {
                "note": (
                    f"reference runner only supports python3 out of the "
                    f"box; got language={language!r}. Register a custom "
                    f"handler via --handler for other languages."
                ),
            },
        }
    return _measure_python_subprocess(code, timeout_s=30.0)


# ---------- HTTP handler ---------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    # Silence access logs — they run on stderr and would cost syscalls
    # while a benchmark is being measured on the same box. Override with
    # --verbose when debugging.
    def log_message(self, format: str, *args: Any) -> None:
        if _VERBOSE:
            super().log_message(format, *args)

    # ----- transport helpers -----

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        # Kill keepalive — connection cleanup shouldn't be running on a
        # timer while the next request is measuring itself.
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> tuple[dict | None, str | None]:
        try:
            n = int(self.headers.get("content-length", "0") or "0")
        except ValueError:
            return None, "invalid content-length"
        if n <= 0:
            return {}, None
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return None, f"invalid JSON body: {e}"
        if not isinstance(data, dict):
            return None, "body must be a JSON object"
        return data, None

    def _check_auth(self) -> bool:
        if not _AUTH_TOKEN:
            return True                                # unsecured mode
        h = self.headers.get("authorization") or ""
        if not h.startswith("Bearer "):
            return False
        return h[len("Bearer "):].strip() == _AUTH_TOKEN

    # ----- routes -----

    def do_GET(self) -> None:
        if self.path == "/healthz":
            endpoints = ["/healthz"]
            if _MEASURE_HANDLER is not None:
                endpoints += ["/measure", "/run"]
            if _VALIDATE_HANDLER is not None:
                endpoints.append("/validate")
            self._send_json(200, {
                "ok": True,
                "hostname": socket.gethostname(),
                "measurement": _MEASUREMENT_LABEL or None,
                "correctness": _CORRECTNESS_LABEL or None,
                "busy": _BUSY["busy"],
                "busy_since": _BUSY["since"],
                "endpoints": endpoints,
            })
            return
        self._send_json(404, {"error": "not found"})

    # ----- request helpers shared by both POST routes -----

    def _validate_common(self) -> tuple[dict | None, str | None]:
        """Auth + body-shape check shared by /run, /measure, /validate.
        Returns (body, None) on success or (None, error_json_body) after
        having already written the error response (caller returns
        immediately)."""
        if not self._check_auth():
            self._send_json(401, {"error": "missing or invalid Bearer token"})
            return None, "auth"
        body, err = self._read_body()
        if err is not None:
            self._send_json(400, {"error": err})
            return None, "body"
        assert body is not None
        for k in ("title_slug", "language", "code"):
            if not isinstance(body.get(k), str) or not body[k]:
                self._send_json(400, {"error": f"missing/empty field: {k}"})
                return None, "field"
        return body, None

    def _acquire_or_reject(self, meta: dict) -> bool:
        """Serialize work through _RUN_LOCK. Returns True if the caller
        holds the lock (and must release it later); False if we bailed
        out with a 429."""
        if _REJECT_WHEN_BUSY and not _RUN_LOCK.acquire(blocking=False):
            self._send_json(429, {
                "error": "runner busy — another job in progress",
                "busy_since": _BUSY["since"], "busy_meta": _BUSY["meta"],
            })
            return False
        if not _REJECT_WHEN_BUSY:
            # Blocking acquire — kernel-side wait, no polling.
            _RUN_LOCK.acquire()
        _BUSY["busy"] = True
        _BUSY["since"] = time.time()
        _BUSY["meta"] = meta or None
        return True

    def _release(self) -> None:
        _BUSY["busy"] = False
        _BUSY["since"] = None
        _BUSY["meta"] = None
        _RUN_LOCK.release()

    def _call_handler(
        self, handler: Callable[..., dict], body: dict,
        *, extra_kwargs: dict[str, Any] | None = None,
    ) -> dict:
        """Invoke the handler, tolerating both old (3-arg) and new
        (kwarg-aware) shapes. `extra_kwargs` is the endpoint-specific
        selector — e.g. `{"metrics": [...]}` for /measure. Handlers
        that don't accept an extra kwarg fall back to the 3-arg call
        so back-compat handlers keep working."""
        extra_kwargs = extra_kwargs or {}
        try:
            try:
                r = handler(
                    body["title_slug"], body["language"], body["code"],
                    **extra_kwargs,
                )
            except TypeError:
                # Handler predates the extra-kwarg protocol — fall back
                # to the 3-arg shape. Any selector the caller passed is
                # silently ignored on those handlers.
                r = handler(body["title_slug"], body["language"], body["code"])
            if not isinstance(r, dict) or "status_msg" not in r:
                return {"status_msg": "Runtime Error",
                        "raw_response":
                            {"note": f"handler returned unexpected shape: {r!r}"}}
            return r
        except Exception as e:                                       # noqa: BLE001
            return {"status_msg": "Runtime Error",
                    "raw_response":
                        {"note": f"handler raised: {type(e).__name__}: {e}"}}

    # ----- routes -----

    def do_POST(self) -> None:
        if self.path in ("/measure", "/run"):
            self._do_measure()
        elif self.path == "/validate":
            self._do_validate()
        else:
            self._send_json(404, {"error": "not found"})

    def _do_measure(self) -> None:
        if _MEASURE_HANDLER is None:
            self._send_json(404, {
                "error": "no --measure-handler / --handler configured on this runner",
            })
            return
        body, ferr = self._validate_common()
        if ferr is not None:
            return
        assert body is not None
        try:
            repeats = int(body.get("repeats") or 1)
        except (TypeError, ValueError):
            self._send_json(400, {"error": "repeats must be an integer"})
            return
        repeats = max(1, min(repeats, 25))
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}

        # `metrics` — optional list of {"time","energy","memory"}.
        # Reject at the API boundary rather than passing through
        # untrusted shapes; the handler also validates but a 400 here
        # gives the client a clearer error.
        metrics_kw: dict[str, Any] = {}
        if "metrics" in body and body["metrics"] is not None:
            m = body["metrics"]
            if not isinstance(m, list) or not all(isinstance(x, str) for x in m):
                self._send_json(400, {
                    "error": "metrics must be a list of strings"})
                return
            allowed = {"time", "energy", "memory", "correctness"}
            unknown = set(m) - allowed
            if unknown:
                self._send_json(400, {
                    "error": f"unknown metrics: {sorted(unknown)}; "
                             f"known: {sorted(allowed)}"})
                return
            metrics_kw["metrics"] = m

        # `compile` — build the artifact ONCE for this batch (default off).
        # A batch-aware handler compiles once then measures `repeats` times;
        # the compile is never repeated per run.
        compile_flag = bool(body.get("compile", False))
        if not self._acquire_or_reject(meta):
            return
        try:
            import inspect
            try:
                batch_ok = "repeats" in inspect.signature(_MEASURE_HANDLER).parameters
            except (TypeError, ValueError):
                batch_ok = False
            if batch_ok:
                # Batch handler: ONE call, returns a list of `repeats` results.
                try:
                    batch = _MEASURE_HANDLER(
                        body["title_slug"], body["language"], body["code"],
                        **{**metrics_kw, "repeats": repeats, "do_compile": compile_flag},
                    )
                except Exception as e:                               # noqa: BLE001
                    batch = [{"status_msg": "Runtime Error",
                              "raw_response": {"note": f"handler raised: "
                                                       f"{type(e).__name__}: {e}"}}]
            else:
                # Back-compat handler (no batch support): loop the per-call shape.
                batch = [self._call_handler(_MEASURE_HANDLER, body,
                                            extra_kwargs=metrics_kw)
                         for _ in range(repeats)]
            if isinstance(batch, dict):
                batch = [batch]
            batch = [b for b in batch if isinstance(b, dict) and "status_msg" in b]
            if not batch:
                batch = [{"status_msg": "Runtime Error",
                          "raw_response": {"note": "handler returned no runs"}}]
            # Pad/truncate to `repeats` — a compile/validate fail returns one
            # row; replicate it so the arena's aggregate math stays valid.
            if len(batch) < repeats:
                batch = batch + [batch[-1]] * (repeats - len(batch))
            runs = [_normalise(b) for b in batch[:repeats]]
        finally:
            self._release()
        self._send_json(200, {"runs": runs})

    def _do_validate(self) -> None:
        if _VALIDATE_HANDLER is None:
            self._send_json(404, {
                "error": "no --validate-handler configured on this runner",
            })
            return
        body, ferr = self._validate_common()
        if ferr is not None:
            return
        assert body is not None
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}

        if not self._acquire_or_reject(meta):
            return
        try:
            r = self._call_handler(_VALIDATE_HANDLER, body)
        finally:
            self._release()
        # Correctness responses carry status_msg + raw_response + a
        # top-level `error_log` (populated on any non-Accepted verdict
        # by the handler). Perf fields are meaningless on /validate.
        self._send_json(200, {
            "verdict": {
                "status_msg":      r.get("status_msg") or "Runtime Error",
                "error_log":       r.get("error_log"),
                # Full-suite validators report how many reference cases passed
                # before the verdict (== LeetCode total_correct) and the total.
                "total_correct":   r.get("total_correct"),
                "total_testcases": r.get("total_testcases"),
                "raw_response":    r.get("raw_response"),
            },
        })


def _normalise(row: dict) -> dict:
    """Fill missing fields with None so the response shape is stable."""
    keys = ("status_msg", "runtime_ms", "memory_mb", "runtime_energy_j",
            "runtime_percentile", "memory_percentile",
            "runtime_energy_percentile", "total_correct", "total_testcases",
            "submission_id_external",
            # Per-op metrics from the looped curated methodology + the
            # measurement provenance (energy tool + machine descriptor).
            # The arena backend persists the per-op fields on the Run row
            # and stamps energy_tool / machine on the Submission.
            "iterations", "energy_j_per_op", "time_us_per_op",
            "energy_tool", "machine", "config",
            # `error_log` — human-readable one-shot summary of a compile /
            # validate / runtime failure. Handlers populate this on any
            # non-Accepted result; the arena backend surfaces it to the
            # SPA so an operator sees WHY a run failed without paging
            # through raw_response.
            "error_log",
            "raw_response")
    return {k: row.get(k) for k in keys}


# ---------- CLI ------------------------------------------------------------

def _resolve_handler(
    spec: str | None, *, default: Callable[[str, str, str], dict] | None,
    default_label: str, flag_name: str,
) -> tuple[Callable[[str, str, str], dict] | None, str]:
    """Load one callable from `module:function`. `spec=None` and no
    `default` → returns (None, "") — that endpoint is disabled. With a
    `default`, returns the default when spec is None."""
    if not spec:
        if default is None:
            return None, ""
        return default, default_label
    module_path, _, fn_name = spec.partition(":")
    if not module_path or not fn_name:
        raise SystemExit(f"{flag_name} must be 'module:function', got {spec!r}")
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    return fn, f"custom: {spec}"


def main() -> None:
    global _AUTH_TOKEN
    global _MEASURE_HANDLER, _MEASUREMENT_LABEL
    global _VALIDATE_HANDLER, _CORRECTNESS_LABEL
    global _REJECT_WHEN_BUSY, _VERBOSE

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--auth-token", default=os.environ.get("HARNESS_RUNNER_TOKEN"),
                    help="shared secret expected on Authorization: Bearer. "
                         "Leave unset for unauthenticated (dev only).")
    # Perf handler. --handler is the historical name and remains an
    # alias for --measure-handler; new configs should prefer the
    # explicit split.
    ap.add_argument("--measure-handler", default=None,
                    help="dotted import path to measure_once(slug, lang, code) → dict "
                         "(the perf-oracle backend). Defaults to a python3 subprocess "
                         "reference. Pass an empty string to disable POST /measure "
                         "on this runner.")
    ap.add_argument("--handler", default=None,
                    help="DEPRECATED alias for --measure-handler.")
    # Correctness handler is off unless explicitly set — most existing
    # deployments only care about perf, and a runner without a
    # correctness backend simply 404s POST /validate.
    ap.add_argument("--validate-handler", default=None,
                    help="dotted import path to validate_once(slug, lang, code) → "
                         "{status_msg, raw_response} (the correctness-oracle "
                         "backend). Off by default; POST /validate 404s when "
                         "unset.")
    ap.add_argument("--reject-when-busy", action="store_true",
                    help="when another run holds the lock, return 429 instead of "
                         "queueing. Default: queue on the lock (kernel-side wait).")
    ap.add_argument("--verbose", action="store_true",
                    help="log request lines to stderr. Off by default so stderr "
                         "isn't written during a measurement.")
    args = ap.parse_args()

    _AUTH_TOKEN = args.auth_token
    # --handler takes precedence only if --measure-handler wasn't given;
    # that way an operator who has been passing --handler for years
    # keeps working, and someone who explicitly sets --measure-handler
    # picks up the new naming without ambiguity.
    measure_spec = args.measure_handler if args.measure_handler is not None else args.handler
    _MEASURE_HANDLER, _MEASUREMENT_LABEL = _resolve_handler(
        measure_spec,
        default=default_run_once,
        default_label="reference: python3 subprocess + ru_maxrss",
        flag_name="--measure-handler",
    )
    _VALIDATE_HANDLER, _CORRECTNESS_LABEL = _resolve_handler(
        args.validate_handler,
        default=None,
        default_label="",
        flag_name="--validate-handler",
    )
    _REJECT_WHEN_BUSY = args.reject_when_busy
    _VERBOSE = args.verbose

    # SIGCHLD default-ignore so zombie subprocesses from the reference
    # measurement backend don't accumulate. We use subprocess.run which
    # waits synchronously anyway — this is belt+suspenders.
    if hasattr(signal, "SIGCHLD"):
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)

    # Single-threaded server. NOT ThreadingHTTPServer — concurrent
    # requests would still queue on _RUN_LOCK, but a per-request worker
    # thread pool would sit idle burning cache lines while a
    # measurement is running. Serving from a single thread keeps the
    # footprint one Python thread on the box.
    server = http.server.HTTPServer((args.host, args.port), _Handler)

    print(f"[harness-runner] listening on {args.host}:{args.port} "
          f"(single-thread, stdlib-only)", file=sys.stderr)
    print(f"[harness-runner] measure  (/measure, /run): "
          f"{_MEASUREMENT_LABEL or 'OFF'}", file=sys.stderr)
    print(f"[harness-runner] validate (/validate):     "
          f"{_CORRECTNESS_LABEL or 'OFF'}", file=sys.stderr)
    print(f"[harness-runner] auth: "
          f"{'bearer-required' if _AUTH_TOKEN else 'OPEN (dev only)'}",
          file=sys.stderr)
    print(f"[harness-runner] concurrency: "
          f"{'reject-when-busy (429)' if _REJECT_WHEN_BUSY else 'queue on lock'}",
          file=sys.stderr)
    print(f"[harness-runner] access log: "
          f"{'on' if _VERBOSE else 'off'}", file=sys.stderr)
    try:
        server.serve_forever(poll_interval=60.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
