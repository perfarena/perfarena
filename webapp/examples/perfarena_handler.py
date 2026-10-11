"""Perfarena Energy-Languages harness bridge for harness_runner_server.py.

Adapts the arena runner protocol (title_slug + language + code ->
{status_msg, runtime_ms, memory_mb, runtime_energy_j, raw_response})
to the perfarena Energy-Languages fork's Makefile contract
(compile / validate / measure / mem, driven by perfarena.mk).

Repo of record:

    /Users/rar9993/repos/research/leetcode_crawler/Energy-Languages/

This bundles two problem sets under one tree:
    * CLBG (10 canonical benchmarks) — laid out `<Language>/<problem>/`
      at the repo root (legacy layout).
    * LeetCode-Energy (curated LC93 workloads) — laid out
      `leetcode-energy/<Language>/<problem>/`.

A restructure to `<Language>/<competition>/<problem>` (clbg + leetcode
sitting alongside each other under each language) is planned but not
yet applied — this handler discovers cells at BOTH legacy paths so the
current on-disk layout keeps working until the restructure lands.

Run the arena runner with:

    export PERFARENA_ROOT=/Users/rar9993/repos/research/leetcode_crawler/Energy-Languages
    python3 webapp/examples/harness_runner_server.py \\
        --host 0.0.0.0 --port 8787 --auth-token dev-secret \\
        --measure-handler  perfarena_handler:measure_once \\
        --validate-handler perfarena_handler:validate_once \\
        --verbose

Requirements on the host:
    * PERFARENA_ROOT set to the Energy-Languages fork root.
    * `make` on PATH, per-language toolchains for the cells you want.
    * On Linux for RAPL: `RAPL/perfarena_runner` built, and either
      sudo NOPASSWD for perfarena_runner or run the arena runner
      itself with elevated privileges.
    * On macOS: `pip install codecarbon` inside the runner's venv.

What it does per POST /measure:
    1. Resolves (language, title_slug) -> `Energy-Languages/<Lang>/<comp>/<slug>/`.
    2. Reads the cell's Makefile to discover SOURCE (the canonical
       file the Makefile expects to compile from).
    3. Backs up SOURCE, writes the model's `code` in its place.
    4. Runs `make clean compile validate measure` — energy + wall
       clock, N iterations under the active profiler (rapl /
       powermetrics / codecarbon, controlled by PERFARENA_PROFILER).
    5. Parses the JSONL emitted by the runner, averages
       `phase=measure` iterations, converts energy to joules.
    6. **In a SEPARATE run**, runs `make run` under RUSAGE_CHILDREN
       to capture peak RSS. This mirrors the original CLBG paper's
       methodology: memory is measured in a different invocation
       from energy+time so the memory tracker's overhead doesn't
       contaminate the perf number. Set PERFARENA_SKIP_MEM=1 to
       skip if you're measuring memory elsewhere.
    7. Restores the original SOURCE (so a failed run doesn't leave
       the tree in a modified state).

status_msg mapping:
    * compile fails                  -> "Compile Error"
    * validate fails / diff mismatch -> "Wrong Answer"
    * measure exits non-zero         -> "Runtime Error"
    * everything green               -> "Accepted"
"""
from __future__ import annotations

import json
import os
import platform
import re
import resource
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


# --- lightweight phase logging (opt-in via PERFARENA_LOG) --------------------
# A bench run is otherwise silent until a timeout fires, so a hang is invisible.
# Set PERFARENA_LOG=1 for timestamped, per-phase lines on the runner's stderr.
# This is the HANDLER's stderr, separate from the child harness's captured
# stderr, so it never contaminates the parsed CASE=/ITERS=/BEACON= line.
_LOG_ON = os.environ.get("PERFARENA_LOG", "").strip().lower() not in (
    "", "0", "false", "no", "off")


def _log(msg: str) -> None:
    if _LOG_ON:
        print(f"[clbg {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _log_always(msg: str) -> None:
    """Progress logging that ignores PERFARENA_LOG. Reserved for low-volume,
    user-facing milestones (e.g. per test-suite-run timing) that should show
    even when verbose handler logging is off."""
    print(f"[clbg {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# Arena language slug (matches the "language" column on Attempt rows)
# -> (folder name in Energy-Languages fork, file extension). Only the
# languages CLBG-Energy-Languages actually ships with — attempts in
# other languages are rejected up front so the model never wastes an
# LLM call on something the runner can't measure.
_LANG_MAP: dict[str, tuple[str, str]] = {
    "python3":    ("Python",     ".py"),
    "cpp":        ("C++",        ".cpp"),
    "c":          ("C",          ".c"),
    "swift":      ("Swift",      ".swift"),
    "scala":      ("Scala",      ".scala"),
    "java":       ("Java",       ".java"),
    "csharp":     ("CSharp",     ".cs"),
    "javascript": ("JavaScript", ".js"),
    "typescript": ("TypeScript", ".ts"),
    "go":         ("Go",         ".go"),
    # The arena stores Go submissions under language="golang" (LeetCode's
    # own slug); accept both so dispatched golang attempts resolve.
    "golang":     ("Go",         ".go"),
    "rust":       ("Rust",       ".rs"),
    "ruby":       ("Ruby",       ".rb"),
    "php":        ("PHP",        ".php"),
    "kotlin":     ("Kotlin",     ".kt"),
    "dart":       ("Dart",       ".dart"),
    "elixir":     ("Elixir",     ".ex"),
    "erlang":     ("Erlang",     ".erl"),
    "racket":     ("Racket",     ".rkt"),
}


# Arena language slug -> the argument run_harness.sh expects. Mostly the
# identity, but the arena stores Go as "golang" (LeetCode's slug) while the
# shared inputs / harness key it under "golang" too — kept explicit so a new
# arena language that diverges from the harness name is a one-line add here.
_HARNESS_LANG: dict[str, str] = {
    "python3": "python3", "cpp": "cpp", "c": "c", "java": "java", "csharp": "csharp",
    "swift": "swift", "scala": "scala",
    "javascript": "javascript", "typescript": "typescript",
    "go": "golang", "golang": "golang", "rust": "rust", "ruby": "ruby",
    "php": "php", "kotlin": "kotlin", "dart": "dart",
    "elixir": "elixir", "erlang": "erlang", "racket": "racket",
}


# ─────────────────────────── looped-curated measurement ───────────────────────
# The scientifically-fair per-language methodology: for each CURATED test case
# of a problem (chosen by the SimPoint-style selection in
# selection/curated_selection.json), run the language's shared-input loop
# harness (selection/run_harness.sh) to a wall-time budget so every case
# executes for >=1s, then normalise to PER-OP metrics that ARE comparable
# across languages (energy / time for one single solve of that case).
#
# Energy is metered by wrapping the whole harness process in the machine's
# energy tool (codecarbon on this Mac; RAPL on a bare-metal Linux box). The
# harness itself reports MEAS_S = the wall-time of its MEASURED window (the
# counted loop, excluding warmup). We apportion the process-total energy to
# that window by time-fraction — under a roughly-constant-power model this
# excludes compilation, interpreter/JIT startup and warmup from the per-op
# energy, without needing to cache build artifacts. When we move to real RAPL
# on the box this apportioning should be replaced by a build/exec split.


def _selection_dir() -> Path:
    return _root() / "selection"


def _energy_tool_name() -> str:
    """Which energy tool this machine measures with. Honors an explicit
    override, else the perfarena profiler knob, else codecarbon (this Mac's
    default). This string is stored as the submission's `energy_tool` so a
    codecarbon-on-Mac run is never compared against a RAPL-on-box run."""
    return (os.environ.get("PERFARENA_ENERGY_TOOL")
            or os.environ.get("PERFARENA_PROFILER")
            or "codecarbon")


_TRACKER_CLS: Any = None
_TRACKER_TRIED = False


def _get_tracker_cls() -> Any:
    """Lazily resolve codecarbon.EmissionsTracker, caching the (possibly
    None) result. None ⇒ no energy tool available; measurement still runs
    and returns time-only per-op metrics."""
    global _TRACKER_CLS, _TRACKER_TRIED
    if not _TRACKER_TRIED:
        _TRACKER_TRIED = True
        try:
            # OFFLINE tracker: skips codecarbon's online geolocation / carbon-
            # intensity network lookup, which hangs for ~20 min on a host with
            # restricted or flaky egress (a silent stall before measurement). We
            # report measured energy, not CO2, so the country is irrelevant.
            from codecarbon import OfflineEmissionsTracker  # type: ignore
            _TRACKER_CLS = OfflineEmissionsTracker
        except Exception as e:  # noqa: BLE001
            print(f"[perfarena_handler] codecarbon unavailable ({e}); "
                  f"energy will be None", file=sys.stderr)
            _TRACKER_CLS = None
    return _TRACKER_CLS


_MACHINE_CACHE: dict[str, Any] | None = None


def _machine_descriptor() -> dict[str, Any]:
    """Describe the machine this runner measures on, so each submission can
    record where it ran. Cached — machine identity doesn't change over a
    runner's lifetime."""
    global _MACHINE_CACHE
    if _MACHINE_CACHE is not None:
        return _MACHINE_CACHE
    cores: int | None = None
    try:
        import psutil  # type: ignore
        cores = psutil.cpu_count(logical=False) or os.cpu_count()
    except Exception:  # noqa: BLE001
        cores = os.cpu_count()
    cpu_model: str | None = None
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                               capture_output=True, text=True, timeout=5)
            cpu_model = r.stdout.strip() or platform.processor() or None
        else:
            for ln in Path("/proc/cpuinfo").read_text().splitlines():
                if ln.lower().startswith("model name"):
                    cpu_model = ln.split(":", 1)[1].strip()
                    break
    except Exception:  # noqa: BLE001
        cpu_model = platform.processor() or None
    name = (os.environ.get("PERFARENA_MACHINE_NAME")
            or socket.gethostname().split(".")[0])
    _MACHINE_CACHE = {
        "name": name,
        "hostname": socket.gethostname(),
        "cpu_model": cpu_model,
        "cores": cores,
        "energy_source": _energy_tool_name(),
        "os": f"{platform.system()} {platform.release()}",
    }
    return _MACHINE_CACHE


def _apply_compile_flags_env(meta: "dict[str, Any] | None") -> None:
    """Export this request's compilation flags so every compile subprocess (make
    compile / codegen-harness compilers) picks them up via os.environ.copy().
    The runner is serial (one job under _RUN_LOCK), so a process-global env var
    is safe. An empty string CLEARS any prior request's flags, so a run without
    flags behaves exactly as before — and `_measure_config` omits the key when
    empty, giving a config dict byte-identical to legacy runs (same config_hash
    → same existing MeasurementProfile, no repopulation)."""
    flags = ""
    if isinstance(meta, dict):
        flags = str(meta.get("compile_flags") or "").strip()
    os.environ["PERFARENA_COMPILE_FLAGS"] = flags


def _measure_config() -> dict[str, Any]:
    """The measurement parameters that affect the per-op numbers — the config
    half of a MeasurementProfile (machine x tool x config). The energy tool is
    tracked separately; this captures the loop/budget knobs so the arena can
    dedup a submission's runs to one profile."""
    cfg = {
        "budget_s": float(os.environ.get("PERFARENA_BUDGET_S", "1.5")),
        "mem_budget_s": float(os.environ.get("PERFARENA_MEM_BUDGET_S", "0.5")),
        "warmup_frac": 0.3,
        "skip_mem": bool(os.environ.get("PERFARENA_SKIP_MEM")),
    }
    # Compile flags are part of the profile identity — different flags must not
    # be pooled into one energy comparison. Included ONLY when non-empty so a
    # default (no-flags) run hashes identically to all pre-existing runs.
    flags = os.environ.get("PERFARENA_COMPILE_FLAGS", "").strip()
    if flags:
        cfg["compile_flags"] = flags
    return cfg


_ITERS_RE = re.compile(r"ITERS=(\d+)")
_MEAS_RE = re.compile(r"MEAS_S=([0-9][0-9.eE+\-]*)")
_CASE_RE = re.compile(r"CASE=(\S+)")


def _parse_harness_line(stderr: str) -> dict[str, Any]:
    """Extract ITERS / MEAS_S / CASE from the harness's last output line
    (the one carrying ITERS=). Missing MEAS_S ⇒ meas_s None (older harness);
    the caller then can't compute a trustworthy per-op and drops the case."""
    line = ""
    for ln in stderr.splitlines():
        if "ITERS=" in ln:
            line = ln
    mi = _ITERS_RE.search(line)
    mm = _MEAS_RE.search(line)
    mc = _CASE_RE.search(line)
    return {
        "iters": int(mi.group(1)) if mi else None,
        "meas_s": float(mm.group(1)) if mm else None,
        "case_name": mc.group(1) if mc else None,
        "harness_line": line,
    }


def _curated_cases(slug: str) -> list[tuple[int, float, str | None]]:
    """(case_index, normalized_weight, case_name) for a problem's curated
    cases. Weights are the Voronoi weights from curated_selection.json,
    renormalized to sum to 1. Falls back to a single case 0 (weight 1) for
    a problem not present in the selection."""
    path = _selection_dir() / "curated_selection.json"
    try:
        data = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return [(0, 1.0, None)]
    entry = data.get(slug)
    kept = (entry or {}).get("kept") or []
    if not kept:
        return [(0, 1.0, None)]
    cases = [(int(c["idx"]), float(c.get("weight") or 0.0), c.get("case"))
             for c in kept]
    tot = sum(w for _, w, _ in cases)
    if tot <= 0:
        n = len(cases)
        return [(i, 1.0 / n, nm) for i, _, nm in cases]
    return [(i, w / tot, nm) for i, w, nm in cases]


def _killpg(proc: subprocess.Popen, pgid: int | None = None) -> None:
    """SIGKILL the child's WHOLE process group. The child is launched
    start_new_session=True (it's the group leader), so a plain proc.kill()
    would only reap the `bash run_harness.sh` wrapper and orphan the real
    workers (javac/g++/rustc/php/the compiled binary) — which keep burning CPU
    and skew the next measurement. SIGKILL the group instead.

    The group id to kill is `proc.pid` itself: with start_new_session the child
    is a session+group leader whose pgid EQUALS its pid. We deliberately do NOT
    use os.getpgid(proc.pid) — right after Popen the child may not have finished
    setsid() (so getpgid transiently returns the RUNNER's own group), and after
    the pid is reaped it may be recycled; killpg'ing either takes the server
    down with it. Targeting proc.pid can only ever hit the child's own group
    (or nothing). As a hard backstop we still refuse to signal our own group."""
    if pgid is None:
        pgid = proc.pid
    try:
        own_pgid = os.getpgid(0)          # the runner's OWN process group
    except Exception:  # noqa: BLE001
        own_pgid = -1
    if pgid is None or pgid <= 0 or pgid == own_pgid:
        # Never SIGKILL the server's own group — fall back to the direct child.
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # group already gone / not yet created — nothing to reap
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# ---- containment: no solution may take the runner down ----------------------
# A solution can write without bound (e.g. a PHP warning in a hot loop on an
# input it does not expect: 65 MB/s measured on bicho). Reading such a pipe
# into memory grew the runner to 14.6 GB until the kernel OOM-killed it. Every
# child pipe is therefore drained by _BoundedDrain, which keeps only the head
# and the tail of the output, and a child that writes more than
# PERFARENA_MAX_OUTPUT_BYTES is killed and fails. Every child also gets the
# highest OOM score (_contain), so under memory pressure the kernel kills the
# solution, never the runner.
_OUTPUT_LIMIT = int(os.environ.get("PERFARENA_MAX_OUTPUT_BYTES", str(64 * 1024 * 1024)))
_KEEP_HEAD = 64 * 1024
_KEEP_TAIL = 256 * 1024


class _BoundedDrain:
    """Drain one child pipe on a thread, keeping its first _KEEP_HEAD and last
    _KEEP_TAIL bytes and counting the rest. Past _OUTPUT_LIMIT bytes it calls
    `on_limit` once (the caller kills the child's group) and keeps discarding
    until EOF. `on_line`, when given, receives each complete line (decoded)
    until the limit is reached, for live progress logging."""

    def __init__(self, pipe: Any, on_limit: Any = None, on_line: Any = None) -> None:
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self.over_limit = False
        self._pipe, self._on_limit, self._on_line = pipe, on_limit, on_line
        self._partial = b""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            fd = self._pipe.fileno()
            while True:
                b = os.read(fd, 65536)
                if not b:
                    break
                self.total += len(b)
                room = _KEEP_HEAD - len(self.head)
                if room > 0:
                    self.head += b[:room]
                    b_rest = b[room:]
                else:
                    b_rest = b
                if b_rest:
                    self.tail += b_rest
                    if len(self.tail) > _KEEP_TAIL:
                        del self.tail[:len(self.tail) - _KEEP_TAIL]
                if self._on_line is not None and not self.over_limit:
                    self._partial += b
                    *lines, self._partial = self._partial.split(b"\n")
                    for line in lines:
                        try:
                            self._on_line(line.decode("utf-8", "replace"))
                        except Exception:  # noqa: BLE001 — logging must not kill the run
                            pass
                if not self.over_limit and self.total > _OUTPUT_LIMIT:
                    self.over_limit = True
                    self._partial = b""
                    if self._on_limit is not None:
                        try:
                            self._on_limit()
                        except Exception:  # noqa: BLE001
                            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                self._pipe.close()
            except Exception:  # noqa: BLE001
                pass

    def join(self, timeout: "float | None" = None) -> None:
        self._thread.join(timeout)

    def text(self) -> str:
        kept = len(self.head) + len(self.tail)
        if self.total <= kept:
            return (bytes(self.head) + bytes(self.tail)).decode("utf-8", "replace")
        note = f"\n[... {self.total - kept} bytes of output omitted ...]\n"
        if self.over_limit:
            note += (f"[output limit exceeded: more than {_OUTPUT_LIMIT} bytes; "
                     "the process was killed]\n")
        return (self.head.decode("utf-8", "replace") + note
                + self.tail.decode("utf-8", "replace"))


def _contain(cmd: list[str]) -> list[str]:
    """Prefix `cmd` so the process (and everything it spawns) has the highest
    OOM score: under memory pressure the kernel then kills the solution, not
    the runner. An unprivileged process may raise its own score. The shell
    execs the command, so the pid stays the same (wait4 still sees the
    program). A no-op where /proc/self/oom_score_adj does not exist (macOS)."""
    return ["sh", "-c", '{ echo 1000 > /proc/self/oom_score_adj; } 2>/dev/null; exec "$@"',
            "sh", *cmd]


def _raise_oom_score(pid: int) -> None:
    """Give an already started child the highest OOM score, from the parent.
    Used where a wrapper shell would distort the measurement (_peak_rss_run):
    Linux keeps a process's peak RSS across exec, so `sh -c ... exec` would add
    the shell's footprint. A no-op where /proc does not exist (macOS)."""
    try:
        with open(f"/proc/{pid}/oom_score_adj", "w") as f:
            f.write("1000")
    except OSError:
        pass


def _run_grouped(
    cmd: list[str], cwd: Path, timeout: float,
    env: "dict[str, str] | None" = None,
    on_stderr_line: "Any" = None,
) -> subprocess.CompletedProcess:
    """Group-safe drop-in for `subprocess.run(capture_output=True, timeout=…)`.

    Every heavy step on the runner (make -> compiler, `bash run_harness.sh` ->
    the looping measurement binary/JVM) spawns grandchildren. Plain
    subprocess.run kills only the DIRECT child on timeout and reaps nothing on
    normal exit, so a compiled benchmark that outlives `make`/`bash` keeps
    burning CPU after the request returns — the box fans stay maxed until the
    whole server is killed. Launch the child as a session leader and SIGKILL
    the WHOLE process group both on timeout and in a finally, so no descendant
    survives the call. Contract matches subprocess.run: returns a
    CompletedProcess and re-raises subprocess.TimeoutExpired on timeout.

    When `on_stderr_line` is given, the child's stderr is streamed line by line
    to that callback as each line arrives, so a long step (a 20-min CLBG
    measure) can report live progress instead of looking hung. The returned
    stdout and stderr keep only their head and tail (_BoundedDrain), and a
    child whose output passes the output limit is killed."""
    proc = subprocess.Popen(
        _contain(cmd), cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, env=env,
    )
    # The child's group id IS its pid (start_new_session). Use proc.pid rather
    # than os.getpgid — see _killpg for why (never risk killing our own group).
    pgid = proc.pid
    # Both pipes are drained by bounded readers (head + tail only), so neither
    # a chatty compiler nor a flooding solution can grow the runner's memory;
    # past the output limit the whole group is killed.
    kill = lambda: _killpg(proc, pgid)  # noqa: E731
    d_out = _BoundedDrain(proc.stdout, on_limit=kill)
    d_err = _BoundedDrain(proc.stderr, on_limit=kill, on_line=on_stderr_line)
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _killpg(proc, pgid)
            proc.wait(timeout=5)
            raise
        # Let the readers flush the tail. Daemon + bounded join so a straggler
        # holding the pipe open never wedges the single-threaded runner.
        d_out.join(5)
        d_err.join(5)
        return subprocess.CompletedProcess(cmd, proc.returncode, d_out.text(), d_err.text())
    finally:
        _killpg(proc, pgid)


class _EnergyBackend:
    """Per-submission energy sampler. `begin()`/`end()` bracket ONE case's
    subprocess; `end()` returns joules over that window (or None). Built ONCE
    per `_measure_curated_looped` so we never pay a per-case codecarbon
    constructor (that was 15-25 constructions per submission and the dominant
    measurement overhead — see the RAPL vs codecarbon note in the README)."""

    label = "none"

    def begin(self) -> None:
        pass

    def end(self) -> "float | None":
        return None

    def close(self) -> None:
        pass


class _RaplEnergy(_EnergyBackend):
    """Direct Intel-RAPL reader over the powercap sysfs. Sums the top-level
    package domains' `energy_uj` before and after the case — a couple of file
    reads, microsecond overhead, instead of a fresh codecarbon tracker each
    time. Needs read access to /sys/class/powercap (setup-harness-host.sh
    grants it)."""

    def __init__(self) -> None:
        import glob
        self.label = "rapl"
        # Top-level package domains only. Subdomains (intel-rapl:N:M — core /
        # uncore / dram) are subsets of the package and would double-count;
        # intel-rapl-mmio:* is a separate psys-style view we also skip.
        self._paths = [
            p for p in sorted(glob.glob("/sys/class/powercap/intel-rapl:*/energy_uj"))
            if p.rsplit("/", 2)[1].startswith("intel-rapl:")
            and p.rsplit("/", 2)[1].count(":") == 1
        ]
        self._e0: "int | None" = None

    def available(self) -> bool:
        return bool(self._paths) and self._read_uj() is not None

    def _read_uj(self) -> "int | None":
        total = 0
        for p in self._paths:
            try:
                with open(p) as f:
                    total += int(f.read())
            except Exception:  # noqa: BLE001
                return None
        return total

    def begin(self) -> None:
        self._e0 = self._read_uj()

    def end(self) -> "float | None":
        e1 = self._read_uj()
        if self._e0 is None or e1 is None:
            return None
        delta_uj = e1 - self._e0
        # A negative delta means a counter wrapped mid-case (rare: the range is
        # ~10^11 uJ, a ~2s case spends ~10^7). Drop it rather than guess.
        return delta_uj / 1_000_000.0 if delta_uj >= 0 else None


class _PowermetricsEnergy(_EnergyBackend):
    """macOS powermetrics reader for the per-op path. Starts ONE continuous
    powermetrics sampler (scoped passwordless sudo, 100 ms) at construction;
    begin()/end() mark a per-case window and integrate CPU+GPU+ANE power over it
    into joules — the same integration as
    perfarena.runners.powermetrics_runner, so a per-op number is comparable to
    the CLBG one. Needs macOS + `sudo -n /usr/bin/powermetrics`; available()
    is False anywhere else and the caller falls back."""

    def __init__(self) -> None:
        self.label = "powermetrics"
        self._ok = False
        self._sampler: Any = None
        self._t0: "int | None" = None
        try:
            import tempfile
            from perfarena.casewise_energy import PowermetricsSampler
            PowermetricsSampler.preflight()  # raises if not macOS / no scoped sudo
            fd, raw = tempfile.mkstemp(prefix="perfarena_pm_op_", suffix=".plist")
            os.close(fd)
            interval = int(os.environ.get("PERFARENA_PM_INTERVAL_MS", "100"))
            self._sampler = PowermetricsSampler(Path(raw), interval_ms=interval)
            self._sampler.start()
            self._ok = True
        except Exception as e:  # noqa: BLE001
            print(f"[perfarena_handler] powermetrics unavailable ({e}); falling back",
                  file=sys.stderr)

    def available(self) -> bool:
        return self._ok

    def begin(self) -> None:
        self._t0 = time.monotonic_ns()

    def end(self) -> "float | None":
        if not self._ok or self._sampler is None or self._t0 is None:
            return None
        t1 = time.monotonic_ns()
        try:
            self._sampler.wait_for_window(t1)
            samples = self._sampler.samples_for_window(self._t0, t1)
        except Exception:  # noqa: BLE001
            return None
        if not samples:
            return None
        joules = 0.0
        for s in samples:
            dt = s.elapsed_ns / 1e9
            joules += (s.cpu_w or 0.0) * dt
            if s.gpu_w:
                joules += s.gpu_w * dt
            if s.ane_w:
                joules += s.ane_w * dt
        return joules

    def close(self) -> None:
        if self._sampler is not None:
            try:
                self._sampler.stop()
            except Exception:  # noqa: BLE001
                pass


def _new_tracker(cls: Any) -> Any:
    """Construct a codecarbon EmissionsTracker with a SHORT power-sampling
    interval. codecarbon's default `measure_power_secs` is 15s — far longer
    than our ~1s measured window (PERFARENA_BUDGET_S*0.7), so with the default
    a measurement can span zero scheduled power samples and under-read energy.
    We set it low (default 0.5s ⇒ ~2 samples/window) so the window is actually
    sampled; a value that is too small would add codecarbon-thread overhead
    during the measured loop, so 0.5s is a balance (tune via
    PERFARENA_MEASURE_POWER_SECS). Falls back to the default interval if the
    installed codecarbon predates the kwarg."""
    kw = dict(project_name="perfarena-curated",
              country_iso_code=os.environ.get("PERFARENA_COUNTRY_ISO", "USA"),
              log_level="error", save_to_file=False, save_to_logger=False)
    mps = float(os.environ.get("PERFARENA_MEASURE_POWER_SECS", "0.5"))
    try:
        return cls(measure_power_secs=mps, **kw)
    except TypeError:
        return cls(**kw)


class _CodecarbonEnergy(_EnergyBackend):
    """One codecarbon EmissionsTracker reused across cases via the task API
    (`start_task`/`stop_task`), so the expensive constructor runs ONCE per
    submission instead of once per case. Falls back to a fresh tracker per case
    only if the task API is missing (older codecarbon)."""

    def __init__(self, tracker_cls: Any) -> None:
        self.label = _energy_tool_name()
        self._cls = tracker_cls
        self._tracker = None
        self._has_tasks = False
        self._fresh = None
        try:
            self._tracker = _new_tracker(tracker_cls)
            self._has_tasks = (hasattr(self._tracker, "start_task")
                               and hasattr(self._tracker, "stop_task"))
            if self._has_tasks:
                self._tracker.start()
        except Exception as e:  # noqa: BLE001
            print(f"[perfarena_handler] codecarbon init failed: {e}", file=sys.stderr)
            self._tracker = None

    def begin(self) -> None:
        if self._tracker is not None and self._has_tasks:
            try:
                self._tracker.start_task()
                return
            except Exception:  # noqa: BLE001
                self._has_tasks = False
        # Fallback: a fresh per-case tracker (old, slow, but correct).
        try:
            self._fresh = _new_tracker(self._cls)
            self._fresh.start()
        except Exception:  # noqa: BLE001
            self._fresh = None

    def end(self) -> "float | None":
        if self._tracker is not None and self._has_tasks:
            try:
                data = self._tracker.stop_task()
                e = getattr(data, "energy_consumed", None)
                return float(e) * 3_600_000.0 if e is not None else None
            except Exception:  # noqa: BLE001
                return None
        if self._fresh is not None:
            try:
                self._fresh.stop()
                d = self._fresh.final_emissions_data
                e = getattr(d, "energy_consumed", None) if d is not None else None
                self._fresh = None
                return float(e) * 3_600_000.0 if e is not None else None
            except Exception:  # noqa: BLE001
                self._fresh = None
                return None
        return None

    def close(self) -> None:
        if self._tracker is not None and self._has_tasks:
            try:
                self._tracker.stop()
            except Exception:  # noqa: BLE001
                pass


def _make_energy_backend() -> _EnergyBackend:
    """Pick the energy backend ONCE per submission. PERFARENA_ENERGY_TOOL=rapl
    uses the direct powercap reader (near-zero per-case cost); otherwise a
    reused codecarbon tracker. Falls back to time-only if neither is usable."""
    tool = _energy_tool_name()
    if tool == "powermetrics":
        p = _PowermetricsEnergy()
        if p.available():
            return p
        print("[perfarena_handler] PERFARENA_PROFILER=powermetrics but powermetrics "
              "is not usable; falling back to codecarbon", file=sys.stderr)
    if tool == "rapl":
        r = _RaplEnergy()
        if r.available():
            return r
        print("[perfarena_handler] PERFARENA_ENERGY_TOOL=rapl but powercap is not "
              "readable; falling back to codecarbon", file=sys.stderr)
    cls = _get_tracker_cls()
    if cls is not None:
        return _CodecarbonEnergy(cls)
    return _EnergyBackend()  # time-only; energy stays None


def _run_harness_measured(
    cell: Path, harness_lang: str, budget: float, idx: int, timeout: float,
    *, energy: "_EnergyBackend | None" = None,
) -> dict[str, Any]:
    """Run `run_harness.sh <lang> <budget> <idx>` FROM `cell`. Returns raw
    per-invocation numbers; per-op normalization is the caller's job.

    `energy` is a shared per-submission backend (RAPL or reused codecarbon)
    whose begin()/end() bracket the run. Peak memory is measured in a SEPARATE
    pass (_run_harness_peak_rss), so it never perturbs the energy/time numbers —
    the CLBG methodology's separate-run rule (see `_measure_curated_looped`)."""
    run_harness = _selection_dir() / "run_harness.sh"
    cmd = ["bash", str(run_harness), harness_lang, str(budget), str(idx)]

    env = os.environ.copy()
    root = _root()
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}:{prev_pp}" if prev_pp else str(root)
    env["PATH"] = f"{str(Path(sys.executable).parent)}:{env.get('PATH', '')}"

    if energy is not None:
        _ts = time.monotonic()
        energy.begin()
        _log(f"case idx={idx} energy.begin ({energy.label}) "
             f"in {time.monotonic() - _ts:.2f}s")

    _log(f"case idx={idx} launch: {' '.join(cmd)} timeout={timeout:.0f}s "
         f"energy={energy is not None}")
    t0 = time.monotonic()
    proc = subprocess.Popen(
        _contain(cmd), cwd=str(cell), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True, env=env,
    )
    # The child's group id IS its pid (start_new_session); use proc.pid, never
    # os.getpgid (which can transiently return the RUNNER's own group). Lets us
    # reap stragglers after the leader exits without risking a self-kill.
    pgid = proc.pid

    # Drain stderr on a background thread so the child never blocks on a full
    # pipe. The harness emits one small line, but a solution can flood stderr
    # (e.g. a warning per call): the bounded reader keeps only head + tail and
    # kills the group past the output limit, so the runner's memory stays flat.
    reader = _BoundedDrain(proc.stderr, on_limit=lambda: _killpg(proc, pgid))

    timed_out = False
    while proc.poll() is None:
        if time.monotonic() - t0 > timeout:
            _log(f"case idx={idx} TIMED OUT after {timeout:.0f}s — killing process group")
            _killpg(proc)
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            timed_out = True
            break
        time.sleep(0.01)   # we only need to notice exit / timeout
    wall_s = time.monotonic() - t0
    reader.join(timeout=5)
    output_limit_exceeded = reader.over_limit
    # Reap any straggler still in the harness group — a build daemon or a
    # detached measurement binary that outlived `bash run_harness.sh`. On the
    # normal path the leader has already exited so this is a no-op; on timeout
    # the group was already killed above. Either way nothing survives the call.
    _killpg(proc, pgid)
    stderr = reader.text()
    if output_limit_exceeded:
        _log(f"case idx={idx} killed: output passed {_OUTPUT_LIMIT} bytes")
    _log(f"case idx={idx} exit={proc.returncode if not timed_out else 'KILLED'} "
         f"wall={wall_s:.2f}s")

    energy_j: float | None = None
    energy_source = "none"
    if energy is not None:
        try:
            _ts = time.monotonic()
            e_win = energy.end()
            _log(f"case idx={idx} energy.end ({energy.label}) "
                 f"in {time.monotonic() - _ts:.2f}s -> {e_win}")
            if not timed_out and e_win is not None:
                energy_j = e_win
                energy_source = energy.label
        except Exception as e:  # noqa: BLE001
            energy_source = f"{energy.label}-error"
            print(f"[perfarena_handler] energy.end failed: {e}", file=sys.stderr)

    if timed_out:
        return {"exit_code": 124, "wall_s": wall_s, "energy_j": None,
                "energy_source": "none",
                "iters": None, "meas_s": None, "case_name": None,
                "harness_line": "", "stderr_tail": stderr[-2000:],
                "timed_out": True}

    parsed = _parse_harness_line(stderr)
    return {
        "exit_code": int(proc.returncode), "wall_s": wall_s, "energy_j": energy_j,
        "energy_source": energy_source,
        "stderr_tail": stderr[-2000:], "timed_out": False,
        "output_limit_exceeded": output_limit_exceeded, **parsed,
    }


def _peak_rss_helper() -> Path:
    """Path of the compiled selection/peak_rss.c for this platform, built on
    first use. The name carries the platform because the Energy-Languages tree
    may be shared between a macOS host and a Linux container."""
    src = _selection_dir() / "peak_rss.c"
    exe = (_selection_dir() / ".perf_build"
           / f"peak_rss-{platform.system()}-{platform.machine()}")
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        exe.parent.mkdir(exist_ok=True)
        subprocess.run(["cc", "-O2", "-o", str(exe), str(src)], check=True,
                       capture_output=True, timeout=60)
    return exe


def _peak_rss_run(cmd: list[str], cwd: Path, env: "dict[str, str]",
                  timeout: float) -> dict[str, Any]:
    """Run `cmd` once and return the peak resident set size of the process it
    starts, from the kernel's ru_maxrss as `wait4` reports it when the process
    exits: the largest single process among the child and the descendants it
    waited for. This is the number GNU `time -v` prints as "Maximum resident
    set size", the memory method of the cross-language energy studies, and
    both competitions use it. Like GNU time, the small peak_rss helper starts
    the program: Linux counts the runner's own memory in the ru_maxrss of a
    process that the runner starts directly. stdout is discarded (mandelbrot
    writes a binary image there); stderr is drained on a thread and kept as a
    tail."""
    t0 = time.monotonic()
    out = cwd / ".peak_rss"
    out.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [str(_peak_rss_helper()), str(out), *cmd], cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True, env=env,
    )
    _raise_oom_score(proc.pid)
    reader = _BoundedDrain(proc.stderr, on_limit=lambda: _killpg(proc))
    timed_out = False
    while True:
        pid, status, ru = os.wait4(proc.pid, os.WNOHANG)
        if pid == proc.pid:
            break
        if time.monotonic() - t0 > timeout:
            timed_out = True
            _killpg(proc)
            pid, status, ru = os.wait4(proc.pid, 0)
            break
        time.sleep(0.01)
    proc.returncode = os.waitstatus_to_exitcode(status)   # reaped here, not by Popen
    wall_s = time.monotonic() - t0
    reader.join(timeout=5)
    _killpg(proc, proc.pid)   # reap stragglers left in the group
    try:
        maxrss = int(out.read_text())
        out.unlink()
    except (OSError, ValueError):   # killed before the helper wrote it
        maxrss = 0
    peak_kb = maxrss // 1024 if sys.platform == "darwin" else maxrss   # bytes on macOS
    return {"exit_code": 124 if timed_out else proc.returncode, "wall_s": wall_s,
            "peak_rss_kb": peak_kb, "timed_out": timed_out,
            "output_limit_exceeded": reader.over_limit,
            "stderr_tail": reader.text()[-2000:]}


def _harness_env(mode: str) -> "dict[str, str]":
    """Environment for run_harness.sh in PERFARENA_HARNESS_MODE `mode`
    (all | build | run), with the same PYTHONPATH/PATH as the energy pass."""
    env = os.environ.copy()
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{_root()}:{prev_pp}" if prev_pp else str(_root())
    env["PATH"] = f"{str(Path(sys.executable).parent)}:{env.get('PATH', '')}"
    env["PERFARENA_HARNESS_MODE"] = mode
    return env


def _run_harness_build(cell: Path, harness_lang: str, timeout: float) -> tuple[bool, str]:
    """Compile the solution and its driver ONCE before the memory pass
    (run_harness.sh build mode), so the measured runs contain no compiler."""
    cmd = ["bash", str(_selection_dir() / "run_harness.sh"), harness_lang, "0", "0"]
    try:
        r = _run_grouped(cmd, cwd=cell, timeout=timeout, env=_harness_env("build"))
    except subprocess.TimeoutExpired:
        return False, f"build timed out after {timeout:.0f}s"
    return r.returncode == 0, (r.stderr or "")[-1000:]


def _run_harness_peak_rss(cell: Path, harness_lang: str, budget: float, idx: int,
                          timeout: float, *, prebuilt: bool) -> dict[str, Any]:
    """One memory-pass run of one case. When the build succeeded, ask
    run_harness.sh for the prebuilt program's command line (argv mode) and
    start that program DIRECTLY: Linux keeps a process's peak RSS across exec,
    so starting it through bash would report bash's ~3 MB for a solution that
    uses less. Otherwise (or for a language without a build/run split) fall
    back to the combined path. Peak RSS comes from _peak_rss_run, as on CLBG."""
    script = str(_selection_dir() / "run_harness.sh")
    if prebuilt:
        try:
            r = subprocess.run(["bash", script, harness_lang, str(budget), str(idx)],
                               cwd=str(cell), env=_harness_env("argv"),
                               capture_output=True, timeout=60)
            argv = [a.decode() for a in r.stdout.split(b"\0") if a] if r.returncode == 0 else []
        except subprocess.TimeoutExpired:
            argv = []
        if argv:
            env = _harness_env("run")
            env["PERFARENA_TS_PREBUILT"] = "1"   # read by harness_ts.js only
            return _peak_rss_run(argv, cell, env, timeout)
    cmd = ["bash", script, harness_lang, str(budget), str(idx)]
    return _peak_rss_run(cmd, cell, _harness_env("run" if prebuilt else "all"), timeout)


# Sentinel: when passed as `energy_backend`, _measure_curated_looped builds and
# owns its own backend (legacy per-call behaviour). Any other value (including
# None) is a caller-owned backend reused across calls and NOT closed here.
_MAKE_OWN_BACKEND: Any = object()


def _measure_curated_looped(
    cell: Path, arena_lang: str, slug: str, budget: float, per_case_timeout: float,
    *, measure_energy: bool = True, measure_memory: bool = False,
    energy_backend: Any = _MAKE_OWN_BACKEND, deadline: float | None = None,
) -> dict[str, Any]:
    """Run every curated case and aggregate to weighted PER-OP metrics.

    Energy+time and memory are measured in SEPARATE passes per the CLBG
    methodology (memory's RSS polling must not perturb the energy/time run):
      * energy pass  — looped to `budget` (>=1s), codecarbon on, no RSS poll;
      * memory pass  — a short separate run, RSS polled, no energy tool.

    Per-op derivation (per case i, from the energy pass):
        time_us_per_op_i   = MEAS_S_i * 1e6 / iters_i
        energy_j_per_op_i  = (energy_window_i * MEAS_S_i / wall_i) / iters_i
    then the workload-representative per-op is the Voronoi-weighted mean over
    cases. `iterations` is the TOTAL solves executed (confidence / provenance).
    """
    harness_lang = _HARNESS_LANG[arena_lang]
    cases = _curated_cases(slug)
    _log(f"curated slug={slug} lang={arena_lang} cases={len(cases)} "
         f"budget={budget:.1f}s per_case_timeout={per_case_timeout:.0f}s "
         f"energy={measure_energy} memory={measure_memory}")
    per_case: list[dict[str, Any]] = []
    energy_source: str | None = None
    peak_rss_kb = 0
    any_nonzero_exit = False
    # Peak RSS is reached during solving and doesn't need the full >=1s window,
    # so the memory pass uses a short budget to keep the extra run cheap.
    mem_budget = float(os.environ.get("PERFARENA_MEM_BUDGET_S", "0.5"))

    # Energy backend (RAPL powercap or a reused codecarbon tracker) is
    # expensive to build. By default this call owns it (builds + closes), but
    # measure_once passes a shared backend so it is built ONCE per submission
    # instead of once per repeat — the codecarbon tracker start/stop is the
    # dominant fixed cost otherwise.
    own_backend = energy_backend is _MAKE_OWN_BACKEND
    if own_backend:
        energy_backend = _make_energy_backend() if measure_energy else None

    # Optional wall-clock deadline for this whole pass. The caller (measure_once)
    # sets it on the FIRST repeat so a solution too slow to finish all repeats
    # within the budget is caught on run 1. Stop launching cases once the
    # deadline passes, and clamp each case's timeout to the time left so one slow
    # case cannot overshoot the cap by a whole per_case_timeout.
    deadline_exceeded = False

    def _case_timeout() -> float:
        if deadline is None:
            return per_case_timeout
        return max(1.0, min(per_case_timeout, deadline - time.perf_counter()))

    # Memory pass: build once, then measure the prebuilt program for each case,
    # so its peak RSS never includes a compiler (run_harness.sh build/run).
    mem_prebuilt = False
    if measure_memory:
        mem_prebuilt, build_note = _run_harness_build(cell, harness_lang, per_case_timeout)
        _log(f"memory build {'ok' if mem_prebuilt else 'failed, using the combined path'}"
             + ("" if mem_prebuilt else f": {build_note[-200:]}"))

    for (idx, w, name) in cases:
        if deadline is not None and time.perf_counter() >= deadline:
            deadline_exceeded = True
            break
        row: dict[str, Any] = {
            "idx": idx, "weight": w, "name": name,
            "iters": 0, "meas_s": None, "wall_s": 0.0,
            "energy_j_window": None, "time_us_per_op": None,
            "energy_j_per_op": None, "exit_code": None,
            "harness_line": None, "stderr_tail": None,
        }

        if measure_energy:
            r = _run_harness_measured(cell, harness_lang, budget, idx,
                                      _case_timeout(), energy=energy_backend)
            iters = r.get("iters") or 0
            meas_s = r.get("meas_s")
            wall_s = r.get("wall_s") or 0.0
            e_win = r.get("energy_j")
            if r.get("exit_code") != 0 or not iters:
                any_nonzero_exit = True
            src = r.get("energy_source")
            if src and src != "none" and not src.endswith("-error"):
                energy_source = src
            t_op_us: float | None = None
            e_op_j: float | None = None
            if iters and meas_s and meas_s > 0:
                t_op_us = (meas_s * 1e6) / iters
                if e_win is not None and wall_s > 0:
                    e_meas = e_win * (meas_s / wall_s)     # measured-window energy
                    e_op_j = e_meas / iters
            row.update({
                "name": name or r.get("case_name"), "iters": iters,
                "meas_s": meas_s, "wall_s": wall_s, "energy_j_window": e_win,
                "time_us_per_op": t_op_us, "energy_j_per_op": e_op_j,
                "exit_code": r.get("exit_code"), "harness_line": r.get("harness_line"),
                "stderr_tail": r.get("stderr_tail") if r.get("exit_code") else None,
            })

        if measure_memory:
            m = _run_harness_peak_rss(cell, harness_lang, mem_budget, idx,
                                      _case_timeout(), prebuilt=mem_prebuilt)
            peak_rss_kb = max(peak_rss_kb, m.get("peak_rss_kb") or 0)
            row["peak_rss_kb_case"] = m.get("peak_rss_kb")
            if m.get("exit_code") not in (0, None):
                any_nonzero_exit = True

        _log(f"case idx={idx} DONE iters={row.get('iters')} "
             f"meas_s={row.get('meas_s')} e_win={row.get('energy_j_window')} "
             f"exit={row.get('exit_code')}")
        per_case.append(row)

    if own_backend and energy_backend is not None:
        energy_backend.close()

    valid_t = [c for c in per_case if c["time_us_per_op"] is not None]
    tw = sum(c["weight"] for c in valid_t) or 1.0
    time_us_per_op = (sum(c["weight"] * c["time_us_per_op"] for c in valid_t) / tw
                      if valid_t else None)
    valid_e = [c for c in valid_t if c["energy_j_per_op"] is not None]
    twe = sum(c["weight"] for c in valid_e) or 1.0
    energy_j_per_op = (sum(c["weight"] * c["energy_j_per_op"] for c in valid_e) / twe
                       if valid_e else None)
    total_iters = sum(c["iters"] for c in per_case)
    return {
        "time_us_per_op": time_us_per_op,
        "energy_j_per_op": energy_j_per_op,
        "iterations": total_iters,
        "energy_source": energy_source or "none",
        "peak_rss_kb": peak_rss_kb,
        "memory_method": ("peak-rss-helper-prebuilt" if mem_prebuilt else "peak-rss-helper")
                         if measure_memory else None,
        "per_case": per_case,
        "n_cases": len(cases),
        "any_nonzero_exit": any_nonzero_exit,
        "deadline_exceeded": deadline_exceeded,
    }


def _root() -> Path:
    """Locate the Energy-Languages fork root (the directory holding
    perfarena.mk). PERFARENA_ROOT wins when set and valid. When it is unset
    or wrong, fall back to auto-detection relative to this file so a runner
    started without the env var still works instead of failing every attempt."""
    env = os.environ.get("PERFARENA_ROOT")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    # Repo layout: this file is <repo>/webapp/examples/, the fork <repo>/Energy-Languages.
    candidates.append(here.parent.parent.parent / "Energy-Languages")
    # Walk up: an ancestor that IS the fork, or one that contains it.
    for anc in here.parents:
        candidates.append(anc)
        candidates.append(anc / "Energy-Languages")
    # Common container layout.
    candidates.append(Path("/work/Energy-Languages"))
    seen: set[str] = set()
    for c in candidates:
        try:
            p = c.resolve()
        except OSError:
            continue
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if (p / "perfarena.mk").exists():
            return p
    if env:
        raise RuntimeError(
            f"PERFARENA_ROOT={env} does not look like an Energy-Languages "
            "fork (perfarena.mk not found there), and auto-detection from "
            f"{here} also failed.")
    raise RuntimeError(
        "PERFARENA_ROOT env var not set and auto-detection failed — point it "
        "at the Energy-Languages fork root (the directory containing "
        "perfarena.mk, Python/, C++/, ...).")


def _cell_dir(title_slug: str, language: str) -> Path:
    """Resolve a (language, title_slug) to the cell directory. Searches
    both problem trees so callers don't need to know which competition
    the slug belongs to:

      1. `<root>/<Language>/<competition>/<slug>` — target layout
         (post-restructure) where each language folder groups cells
         by competition.
      2. `<root>/<Language>/<slug>` — legacy CLBG layout (still
         canonical for the 10 benchmarks at the time of writing).
      3. `<root>/leetcode-energy/<Language>/<slug>` — legacy LC layout
         (the 93-problem curated LC set).

    Whichever exists first wins. Once the tree is restructured to (1),
    the legacy fall-throughs become no-ops but stay in the resolver so
    a half-migrated tree keeps working."""
    if language not in _LANG_MAP:
        raise ValueError(
            f"language {language!r} not measurable by this harness. "
            f"Known: {sorted(_LANG_MAP)}")
    # SECURITY: title_slug is interpolated into a filesystem path below. Reject
    # anything that is not a plain slug so a crafted value cannot traverse out
    # of the fork (e.g. "../../etc") or write to an arbitrary location.
    if title_slug in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", title_slug or ""):
        raise ValueError(f"invalid title_slug {title_slug!r}")
    folder, _ext = _LANG_MAP[language]
    root = _root()
    candidates = [
        # Target layout: <root>/<Lang>/<competition>/<slug>. We probe
        # every immediate subfolder of <Lang>/ to find one that contains
        # <slug>/Makefile — a cheap directory scan (<10 entries).
        *[
            root / folder / comp / title_slug
            for comp in _list_subdirs(root / folder)
        ],
        # Legacy CLBG layout — root/<Lang>/<slug>/
        root / folder / title_slug,
        # Legacy LC layout — root/leetcode-energy/<Lang>/<slug>/
        root / "leetcode-energy" / folder / title_slug,
    ]
    for cell in candidates:
        if (cell / "Makefile").exists():
            return cell
    raise ValueError(
        f"no cell for language={language!r} title_slug={title_slug!r} "
        f"under {root} — checked: "
        + ", ".join(str(c.relative_to(root)) for c in candidates)
    )


def _list_subdirs(p: Path) -> list[str]:
    """Names of immediate subdirectories of `p`, or empty list if `p`
    doesn't exist. Used by _cell_dir to enumerate competitions under
    `<root>/<Language>/`."""
    if not p.is_dir():
        return []
    return [c.name for c in p.iterdir() if c.is_dir()]


_SOURCE_RE = re.compile(r"^\s*SOURCE\s*=\s*(\S+)", re.MULTILINE)


def _discover_source(cell: Path) -> Path:
    """Read the cell Makefile to find the canonical SOURCE filename."""
    mk = (cell / "Makefile").read_text()
    m = _SOURCE_RE.search(mk)
    if not m:
        raise RuntimeError(
            f"cannot find SOURCE = ... in {cell}/Makefile")
    return cell / m.group(1)


# Java top-level `public class Foo` must live in Foo.java; the cell source is
# solution.java, so a model that writes `public class Solution` fails to compile
# ("class Solution is public, should be declared in a file named Solution.java").
# Strip `public` from top-level type declarations (start-of-line) as a safety net
# — package-private is fine within the cell's single compilation unit. Method /
# field modifiers (`public int foo()`) are unaffected: they don't start a line
# with `public class|interface|enum|record`.
_JAVA_PUBLIC_TYPE_RE = re.compile(
    r"(?m)^(\s*)public\s+((?:final\s+|abstract\s+|sealed\s+)*(?:class|interface|enum|record)\b)")

# The Rust harness injects `pub struct Solution;` when the solution lacks one.
# LeetCode's Rust editor hides that boilerplate, so models often add their own
# `struct Solution;` (sometimes twice) -> E0428 "Solution defined multiple
# times". Strip every unit-struct Solution the model declares; the harness then
# supplies exactly one. A `struct Solution { ... }` WITH a body is left alone
# (the harness sees it and won't add a second).
_RUST_STRUCT_SOLUTION_RE = re.compile(
    r"(?m)^[ \t]*(?:pub\s+)?struct\s+Solution\s*;[ \t]*\r?\n?")


def _strip_kotlin_class(code: str, name: str) -> str:
    """Remove a top-level `class <name>( ... ) { ... }` from Kotlin source
    (paren + brace matched). The JVM harness provides public TreeNode/ListNode;
    a model that also declares its own would clash (two classes, same name)."""
    out = code
    pat = re.compile(r"(?m)^[ \t]*(?:open\s+|data\s+|sealed\s+|final\s+)*class\s+"
                     + re.escape(name) + r"\b")
    while True:
        m = pat.search(out)
        if not m:
            return out
        i, k, n = m.start(), m.end(), len(out)
        while k < n and out[k] in " \t":
            k += 1
        if k < n and out[k] == "(":            # skip primary-constructor params
            d = 0
            while k < n:
                if out[k] == "(":
                    d += 1
                elif out[k] == ")":
                    d -= 1
                    if d == 0:
                        k += 1
                        break
                k += 1
        while k < n and out[k] in " \t":
            k += 1
        if k < n and out[k] == "{":             # skip the class body
            d = 0
            while k < n:
                if out[k] == "{":
                    d += 1
                elif out[k] == "}":
                    d -= 1
                    if d == 0:
                        k += 1
                        break
                k += 1
            end = k
        else:                                   # bodyless class -> to end of line
            nl = out.find("\n", k)
            end = nl if nl != -1 else n
        if end < n and out[end] == "\n":
            end += 1
        out = out[:i] + out[end:]


def _normalize_solution_code(language: str, code: str, is_clbg: bool = False) -> str:
    """Best-effort fixups so common model-output quirks still compile."""
    if language == "php" and is_clbg:
        # CLBG runs the .php file standalone, and PHP only executes code inside a
        # `<?php ... ?>` block. A program without the opening tag is printed as
        # text, so it fails every case. Clients strip `<?php` for the LeetCode
        # PHP harness, which wraps user code in an already-open tag. The CLBG
        # standalone file needs it back, so add it when it is missing. Leetcode
        # PHP (is_clbg=False) keeps the stripped shape its harness expects.
        if not re.match(r"\s*<\?(php\b|=)", code, re.IGNORECASE):
            code = "<?php\n" + code
        return code
    if language == "java":
        code = _JAVA_PUBLIC_TYPE_RE.sub(r"\1\2", code)
        # LeetCode's judge implicitly imports the common java.util / stream /
        # math classes, so models write bare `List`, `Map`, `IntStream`,
        # `BigInteger` with NO import lines. javac compiles solution.java as its
        # own unit (Harness.java's imports do NOT apply to it), so those symbols
        # fail with "cannot find symbol: class List". Add the ones the model
        # didn't already bring in — wildcard/unused imports are harmless.
        imports = ["import java.util.*;", "import java.util.stream.*;",
                   "import java.math.*;"]
        missing = [imp for imp in imports if imp not in code]
        if missing:
            block = "\n".join(missing) + "\n"
            m = re.match(r"\s*package\s+[\w.]+\s*;", code)  # imports go AFTER a package stmt
            code = (code[:m.end()] + "\n" + block + code[m.end():]) if m else block + code
        return code
    if language == "rust":
        return _RUST_STRUCT_SOLUTION_RE.sub("", code)
    if language == "kotlin":
        return _strip_kotlin_class(_strip_kotlin_class(code, "TreeNode"), "ListNode")
    return code


def _measure_mem(cell: Path, timeout: float) -> tuple[float | None, dict[str, Any]]:
    """Peak RSS in MB of one `make run`, from ru_maxrss as `wait4` reports it
    for the `make` process (see _peak_rss_run), the same method as the
    LeetCode memory pass. `make run` only runs the already compiled program.

    Follows the original Energy-Languages methodology: memory is measured in
    a SEPARATE run from energy+time, so measuring it never perturbs the
    energy/time numbers.

    Returns (memory_mb, diagnostic_dict). memory_mb is None when the
    measurement failed for any reason (child crashed, `make run` target
    missing, timeout); the caller still records the run but leaves
    memory_mb null.
    """
    env = os.environ.copy()
    root = _root()
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}:{prev_pp}" if prev_pp else str(root)
    r = _peak_rss_run(["make", "-s", "run"], cell, env, timeout)
    if r["timed_out"]:
        return None, {"note": f"mem run timed out after {timeout:.0f}s"}
    if r["exit_code"] != 0:
        return None, {"note": f"mem run failed rc={r['exit_code']}",
                      "stderr": r["stderr_tail"][-1000:]}
    return max(r["peak_rss_kb"] / 1024.0, 0.0), {
        "stage": "mem",
        "returncode": r["exit_code"],
        "peak_rss_kb": r["peak_rss_kb"],
        "memory_method": "peak-rss-helper",
    }


def _first_meaningful_line(text: str, cap: int = 240) -> str:
    """First non-blank line of `text`, truncated. Used to build a
    human-readable one-line summary of a compile/validate/runtime
    failure — the full stderr/stdout is still kept in raw_response
    for deep debugging."""
    if not text:
        return ""
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s[:cap] + ("…" if len(s) > cap else "")
    return ""


def _first_error_line(*texts: str, cap: int = 240) -> str:
    """One-line failure summary that SKIPS make's own `make: *** […] Error N`
    wrapper (that line is just the exit code, not the cause) and prefers a real
    compiler/validator error line. Compilers disagree on the stream — gcc/g++/
    rustc write errors to stderr, but `dotnet` and `tsc` write them to STDOUT —
    so search every provided text. Falls back to the first non-make line."""
    def _lines(t: str):
        for ln in (t or "").splitlines():
            s = ln.strip()
            if s and not s.startswith("make:"):
                yield s
    for t in texts:                         # 1) an explicit error line
        for s in _lines(t):
            if "error" in s.lower():
                return s[:cap] + ("…" if len(s) > cap else "")
    for t in texts:                         # 2) first meaningful non-make line
        for s in _lines(t):
            return s[:cap] + ("…" if len(s) > cap else "")
    return ""


def _fail_dict(
    stage: str, status_msg: str, rc: subprocess.CompletedProcess,
) -> dict[str, Any]:
    """Build a failure RunResult that the arena backend's
    `_build_error_log` can plug into `RunPublic.error_log` (a
    `RunErrorLog` object the SPA already renders on the attempt and
    run detail views).

    Layout:
      * `error_log` at the TOP LEVEL — a distilled human-readable
        string, useful for external clients (matrix_sweep logs, etc.).
      * `raw_response` uses the LC-compatible key names
        (`compile_error`, `full_compile_error`, `runtime_error`,
        `full_runtime_error`, `raw_stdout`, `raw_stderr`, `note`) so
        the SPA's existing renderer picks them up without any
        backend / frontend change. This is why we shape the payload
        by stage rather than dumping a generic blob.
    """
    stderr_tail = rc.stderr[-4000:] if rc.stderr else ""
    stdout_tail = rc.stdout[-4000:] if rc.stdout else ""
    # The USEFUL error can be on either stream (g++ -> stderr; dotnet/tsc ->
    # stdout) and make's stderr always carries a useless `make: *** Error N`
    # wrapper — search both and skip that wrapper.
    summary = (
        _first_error_line(rc.stderr, rc.stdout)
        or f"(no output; exit code {rc.returncode})"
    )
    # Keep BOTH streams for the full log — `stderr or stdout` dropped the
    # compiler's real message (in stdout) whenever the make wrapper wrote to
    # stderr, which is always.
    both_tail = "\n".join(t for t in (stdout_tail, stderr_tail) if t)
    error_log_str = (
        f"{stage} failed (exit={rc.returncode}): {summary}"
        + (f"\n\n--- stderr tail ---\n{stderr_tail}" if stderr_tail else "")
        + (f"\n\n--- stdout tail ---\n{stdout_tail}" if stdout_tail else "")
    )
    # Shape raw_response so _build_error_log's ERROR_FIELDS grep hits.
    raw: dict[str, Any] = {
        "stage":       stage,
        "returncode":  rc.returncode,
        "note":        summary,
        "raw_stdout":  stdout_tail,
        "raw_stderr":  stderr_tail,
    }
    if stage == "compile":
        raw["compile_error"]      = summary
        raw["full_compile_error"] = both_tail
    else:
        # validate + measure — both are runtime failures from the SPA's
        # POV. The stdout may carry the actual model output for wrong-
        # answer cases; surface it as std_output so the SPA renders it
        # in the "your output was" panel.
        raw["runtime_error"]      = summary
        raw["full_runtime_error"] = both_tail
        if status_msg == "Wrong Answer" and stdout_tail:
            raw["std_output"] = stdout_tail
    return dict(
        status_msg=status_msg,
        runtime_ms=None, memory_mb=None, runtime_energy_j=None,
        error_log=error_log_str,
        raw_response=raw,
    )


def _run(cmd: list[str], cwd: Path, timeout: float,
         on_stderr_line: "Any" = None) -> subprocess.CompletedProcess:
    # Inject PYTHONPATH so the Makefile's `python3 -m perfarena.runners…`
    # step finds the perfarena package without requiring a pip-install
    # into the arena-runner's Python env. Also forward the tuning knobs
    # (WARMUP / MEASURE / IDLE_S / RUNNER override) if the operator set
    # them on this handler process.
    env = os.environ.copy()
    root = _root()
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}:{prev_pp}" if prev_pp else str(root)
    # LC-energy cell Makefiles invoke bare `perfarena leetcode-*` — a
    # console script installed alongside `sys.executable`. When the
    # runner is launched from a venv (`.venv/bin/python …`), the shell
    # subprocess we spawn here inherits the PARENT shell's PATH, not
    # the venv's `bin/`. That's why `make compile` used to die with
    # "perfarena: No such file or directory" even though the CLI was
    # installed in the runner's venv. Prepending the runner-Python's
    # bin dir makes every console script from the same Python env
    # reachable in the subprocess, and it's a no-op when the runner
    # is already launched from a shell that has the venv activated.
    py_bin_dir = str(Path(sys.executable).parent)
    env["PATH"] = f"{py_bin_dir}:{env.get('PATH', '')}"
    # Group-safe: `make`/`bash run_harness.sh` spawn compilers and the looping
    # measurement binary as grandchildren; _run_grouped SIGKILLs the whole
    # group on timeout and on return so none of them outlive this call.
    return _run_grouped(cmd, cwd, timeout, env=env, on_stderr_line=on_stderr_line)


def _compile_with_flag_fallback(
    cmd: list[str], cell: Path, timeout: float,
) -> "tuple[subprocess.CompletedProcess, dict[str, Any] | None]":
    """Run a compile `cmd`, tolerating INVALID custom compile flags.

    If `PERFARENA_COMPILE_FLAGS` is set and the compile fails, retry the SAME
    compile with the flags cleared (language default):
      * retry succeeds  -> the FLAGS were the problem. Fall back to defaults for
        the rest of the run and return fallback info so the caller records it.
        The env is left cleared, so `_measure_config` records the DEFAULT profile
        (defaults are what actually ran) and the invalidity is metadata only.
      * retry also fails -> it is the model's CODE. Return the default-flags
        failure as the canonical Compile Error (no flag noise), no fallback.
    Returns (CompletedProcess, fallback_info|None) where fallback_info =
    {"compile_flags_invalid": <attempted>, "compile_flags_used": ""}."""
    flags = os.environ.get("PERFARENA_COMPILE_FLAGS", "").strip()
    rc = _run(cmd, cwd=cell, timeout=timeout)
    if rc.returncode == 0 or not flags:
        return rc, None
    # Compile failed WITH flags — distinguish invalid-flags from broken-code by
    # retrying on defaults.
    os.environ["PERFARENA_COMPILE_FLAGS"] = ""
    rc2 = _run(cmd, cwd=cell, timeout=timeout)
    if rc2.returncode == 0:
        _log(f"compile: custom flags {flags!r} invalid — fell back to default")
        return rc2, {"compile_flags_invalid": flags, "compile_flags_used": ""}
    return rc2, None   # real code error (fails even on defaults)


def _count_measure_rows(cell: Path, folder: str, title_slug: str) -> int:
    """Count `phase=measure` JSONL rows already present for THIS cell, captured
    BEFORE `make measure` so _parse_metrics can isolate the rows THIS run
    appends (the runner appends to the per-language JSONL, never truncates)."""
    jsonl_path = cell.parent / f"{folder}.jsonl"
    if not jsonl_path.exists():
        return 0
    n = 0
    with jsonl_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (row.get("test") == title_slug
                    and row.get("language") in (folder, folder.lower())
                    and row.get("phase") == "measure"):
                n += 1
    return n


def _parse_metrics(cell: Path, folder: str, title_slug: str,
                   baseline: int = 0) -> dict[str, Any]:
    """Parse the RAPL/codecarbon runner's JSONL output for THIS cell.

    Both runners write to `../<Folder>.jsonl` (relative to the cell),
    appending. We take THIS run's rows (those appended past `baseline`) that
    match (test=title_slug, language=folder, phase=measure) and average."""
    jsonl_path = cell.parent / f"{folder}.jsonl"
    if not jsonl_path.exists():
        raise RuntimeError(
            f"measure produced no output at {jsonl_path}")
    rows: list[dict] = []
    with jsonl_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("test") != title_slug:
                continue
            if row.get("language") not in (folder, folder.lower()):
                continue
            if row.get("phase") != "measure":
                continue
            rows.append(row)
    if not rows:
        raise RuntimeError(
            f"no `phase=measure` rows for ({folder}, {title_slug}) "
            f"in {jsonl_path}")
    # The runner appends every invocation — take only the trailing
    # PERFARENA_CLBG_MEASURE_RUNS rows (default 7; old name PERFARENA_MEASURE
    # still honoured) for this cell so a re-run isn't skewed by earlier ones.
    n_measure = int(os.environ.get("PERFARENA_CLBG_MEASURE_RUNS")
                    or os.environ.get("PERFARENA_MEASURE") or "7")
    # Only THIS run's rows: everything appended after the pre-measure baseline.
    # The runner never truncates the JSONL, so without this we would average
    # stale rows from a prior invocation of the same cell — exactly what let a
    # watchdog-KILLED measure still parse a full sample set and get Accepted.
    fresh = rows[baseline:]
    if len(fresh) < n_measure:
        raise RuntimeError(
            f"incomplete measure for ({folder}, {title_slug}): "
            f"{len(fresh)}/{n_measure} fresh sample(s) — the run was killed or "
            f"timed out before finishing")
    tail = fresh[-n_measure:]

    walls = [float(r["wall_ms"]) for r in tail if "wall_ms" in r]
    # Energy shape produced by the runner (both codecarbon and RAPL
    # backends share the same JSONL schema):
    #   * `rapl_pkg_delta_raw` carries the counter value in the
    #     backend's native unit (microjoules for codecarbon, raw
    #     MSR ticks for RAPL).
    #   * `energy_source` tells which backend produced the row:
    #     "codecarbon" | "rapl" | "none".
    # Post-processed rows (produced by `perfarena ingest-measurements`)
    # additionally carry `inference_metrics.energy_uj`; we honor that
    # if present but do not depend on it — the runner writes raw rows
    # to `<Folder>.jsonl`, so that's the primary source.
    energies_j: list[float] = []
    energy_source: str | None = None
    mems_kb: list[float] = []
    exits: list[int] = []
    for r in tail:
        src = r.get("energy_source")
        if src and src != "none":
            energy_source = src
        raw = r.get("rapl_pkg_delta_raw")
        if src == "codecarbon" and raw:
            # microjoules → joules
            energies_j.append(float(raw) / 1_000_000.0)
        elif src == "rapl" and raw:
            # RAPL raw counter — the RAPL runner emits already-scaled
            # joules in this field when compiled with the CPU-family
            # correction; if unscaled it's ticks. We trust the source
            # of truth (the runner) and emit as-is. Users who see
            # unrealistic joule numbers should verify their RAPL
            # runner build has the pkg unit set for their CPU.
            energies_j.append(float(raw))
        im = r.get("inference_metrics") or {}
        # Post-processed row: honor peak_rss_kb from the enriched shape.
        if im.get("peak_rss_kb") is not None:
            mems_kb.append(float(im["peak_rss_kb"]))
        # Also accept `peak_rss_kb` at the top level if the runner is
        # ever taught to emit it raw.
        if r.get("peak_rss_kb") is not None:
            mems_kb.append(float(r["peak_rss_kb"]))
        exits.append(int(r.get("exit_code", 0)))

    runtime_ms = sum(walls) / len(walls) if walls else None
    memory_mb = (sum(mems_kb) / len(mems_kb) / 1024.0) if mems_kb else None
    energy_j = (sum(energies_j) / len(energies_j)) if energies_j else None

    any_nonzero_exit = any(x != 0 for x in exits)

    return dict(
        runtime_ms=runtime_ms,
        memory_mb=memory_mb,
        runtime_energy_j=energy_j,
        energy_source=energy_source,
        n_iterations=len(tail),
        # Per-sample series (one entry per measured run) so a caller can emit
        # each MEASURE sample as its own result instead of only the mean — CLBG
        # uses this to expose the run-to-run spread without an extra outer loop.
        walls_ms=walls,
        energies_j=energies_j,
        exit_codes=exits,
        any_nonzero_exit=any_nonzero_exit,
        jsonl_path=str(jsonl_path),
    )


_VALIDATE_NCASES_RE = re.compile(r"\bncases=(\d+)")
_VALIDATE_PASSED_RE = re.compile(r"\bpassed=(\d+)")


def _parse_validate_counts(stderr: str) -> "tuple[int | None, int | None]":
    """Extract (total_correct, total_testcases) from a full-suite VALIDATE line.
    Validators emit `... passed=<K> ncases=<N>`; K = cases that passed before the
    verdict (LeetCode's total_correct semantics), N = total reference cases.
    Returns (None, None) for the legacy `make validate` fallback (no counts)."""
    if not stderr:
        return None, None
    n = _VALIDATE_NCASES_RE.search(stderr)
    k = _VALIDATE_PASSED_RE.search(stderr)
    total = int(n.group(1)) if n else None
    passed = int(k.group(1)) if k else None
    return passed, total


def _make_validate_status(returncode: int, stderr: str) -> str:
    """Map a `make validate` result to a verdict. A non-zero exit is not always a
    wrong answer: the model's program can CRASH (Runtime Error) or HANG past the
    per-case cap (Time Limit Exceeded). The validators name the cause in stderr —
    `clbg_validate.sh` prints `crashed rc=` / `timed out after`, and
    `perfarena.mk`'s single-file path (used by CLBG mandelbrot and the LeetCode
    legacy fallback) prints `program exited` — so read it instead of collapsing
    every failure to Wrong Answer. Falls back to Wrong Answer for a genuine output
    mismatch. Shared by the CLBG paths and the LeetCode rc-2 fallback."""
    if returncode == 0:
        return "Accepted"
    s = stderr or ""
    if "timed out after" in s:
        return "Time Limit Exceeded"
    if "crashed rc=" in s or "program exited" in s:
        return "Runtime Error"
    return "Wrong Answer"


def _parse_clbg_diff(text: str) -> "tuple[dict[str, str], str]":
    """Extract the PERFARENA-DIFF-* markers that clbg_validate.sh / perfarena.mk
    emit on a text-output mismatch, and return (fields, text-without-markers).

    `fields` has `last_testcase`, `expected_output`, and `code_output` (decoded
    from base64) for the SPA's side-by-side panel. Binary problems emit no
    markers, so `fields` is empty and the hex diff in stderr stands alone."""
    import base64
    fields: dict[str, str] = {}
    m = re.search(r"(?m)^PERFARENA-DIFF-CASE:(.*)$", text)
    if m:
        fields["last_testcase"] = m.group(1).strip()
    for marker, key in (("EXPECTED", "expected_output"), ("PRODUCED", "code_output")):
        mm = re.search(rf"(?m)^PERFARENA-DIFF-{marker}-B64:(\S+)\s*$", text)
        if mm:
            try:
                fields[key] = base64.b64decode(mm.group(1)).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
    stripped = re.sub(r"(?m)^PERFARENA-DIFF-[A-Z0-9-]+:.*\n?", "", text)
    return fields, stripped


def _full_suite_validate(cell: Path, arena_lang: str,
                         timeout: float = 300.0) -> subprocess.CompletedProcess:
    """Run the full-suite correctness validator (selection/run_validate.sh) FROM
    the cell — the solution against EVERY reference case. Exit codes: 0 Accepted,
    1 Wrong Answer, 3 Runtime Error, 2 not-yet-built for this language (the
    caller falls back to the legacy single-case `make validate`)."""
    script = _selection_dir() / "run_validate.sh"
    # run_validate.sh keys on the same labels as run_harness.sh (e.g. "golang",
    # not "go"), so normalise the arena slug the same way the measure path does.
    harness_lang = _HARNESS_LANG.get(arena_lang, arena_lang)
    return _run(["bash", str(script), harness_lang], cwd=cell, timeout=timeout)


def _is_clbg_cell(cell: Path, title_slug: str) -> bool:
    """True when (language, title_slug) is a CLBG benchmark rather than a
    LeetCode problem. CLBG cells live under `<Lang>/clbg/<slug>/` and are
    validated by the golden-output `make validate` path — never the LeetCode
    full-suite validator, which needs a `reference/leetcode/workloads/<slug>.json`
    that CLBG problems don't have (harness_cpp.py raises FileNotFoundError on
    them, exiting 1 instead of the rc-2 the fallback expects). Detected by the
    cell path or the presence of a CLBG reference so it works for both the
    `<Lang>/clbg/<slug>` and legacy `<Lang>/<slug>` layouts."""
    if "clbg" in cell.parts:
        return True
    out = _root() / "reference" / "clbg" / "outputs"
    return (out / title_slug / "cases.txt").exists() or (out / f"{title_slug}.txt").exists()


def validate_once(
    title_slug: str, language: str, code: str,
    meta: "dict[str, Any] | None" = None,
) -> dict[str, Any]:
    """Correctness-only adapter — matches the runner's /validate route.

    Runs `make clean compile validate` on the (language, problem) cell
    and returns only {status_msg, raw_response}. Skips `make measure`
    entirely — this is O(seconds), not O(minutes), so a correctness
    oracle can call it eagerly on every submission without paying the
    energy-measurement bill for wrong answers.

    Restores the canonical source in a `finally` so the fork stays
    git-clean between attempts.
    """
    _apply_compile_flags_env(meta)   # export PERFARENA_COMPILE_FLAGS for compiles
    cell = _cell_dir(title_slug, language)
    source = _discover_source(cell)
    backup = source.with_suffix(source.suffix + ".perfarena_backup")
    try:
        # Back up the source only if it exists — a fresh cell (no committed
        # solution.*, e.g. a just-generated or cleared cell) has none, and we
        # overwrite it with the model's code here anyway. Without this guard
        # copy2 raised FileNotFoundError and such a cell could NEVER bootstrap
        # (write never ran). The finally leaves the written file when there was
        # no backup, so the next attempt has a source and restores normally.
        if source.exists():
            shutil.copy2(source, backup)
        source.write_text(_normalize_solution_code(
            language, code, _is_clbg_cell(cell, title_slug)))

        rc, flag_fb = _compile_with_flag_fallback(
            ["make", "-s", "clean", "compile"], cell, 60.0)
        if rc.returncode != 0:
            fd = _fail_dict("compile", "Compile Error", rc)
            # validate_once doesn't have perf fields — drop them so the
            # response shape stays lean for correctness callers.
            return {"status_msg": fd["status_msg"],
                    "error_log":  fd["error_log"],
                    "raw_response": fd["raw_response"]}
        # Correctness. CLBG benchmarks are validated by the golden-output
        # `make validate` path (multi-case diff via clbg_validate.sh) — they have
        # no LeetCode workload, so the full-suite LeetCode validator crashes on
        # them instead of cleanly signalling rc 2. Route CLBG straight to
        # `make validate`; LeetCode problems use the full-suite validator with
        # its rc-2 fallback to the legacy single-case `make validate`.
        total_correct = total_testcases = None
        if _is_clbg_cell(cell, title_slug):
            vr = _run(["make", "-s", "validate"], cwd=cell, timeout=120.0)
            status = _make_validate_status(vr.returncode, vr.stderr or "")
            # clbg_validate.sh emits `passed=<K> ncases=<N>` — same shape the
            # full-suite parser reads, so the per-run breakdown is populated too.
            total_correct, total_testcases = _parse_validate_counts(vr.stderr or "")
        else:
            vr = _full_suite_validate(cell, language)
            if vr.returncode == 2:
                # rc 2 = full-suite validator can't run; fall back to the legacy
                # single-case `make validate` and read its cause the same way.
                vr = _run(["make", "-s", "validate"], cwd=cell, timeout=120.0)
                status = _make_validate_status(vr.returncode, vr.stderr or "")
            else:
                status = {0: "Accepted", 3: "Runtime Error"}.get(vr.returncode, "Wrong Answer")
                # Full-suite validators emit `passed=<K> ncases=<N>`: K cases passed
                # before the verdict (== LeetCode total_correct), N total.
                total_correct, total_testcases = _parse_validate_counts(vr.stderr or "")
        if status != "Accepted":
            # Pull the expected/produced diff markers (if any) out of the
            # validator output so the SPA can render a side-by-side panel, and
            # hide the raw base64 markers from the stderr box.
            diff_fields, clean_stderr = _parse_clbg_diff(vr.stderr or "")
            if diff_fields:
                vr = subprocess.CompletedProcess(
                    vr.args, vr.returncode, vr.stdout, clean_stderr)
            fd = _fail_dict("validate", status, vr)
            fd["raw_response"].update(diff_fields)
            if flag_fb:
                fd["raw_response"].update(flag_fb)
            return {"status_msg": fd["status_msg"],
                    "error_log":  fd["error_log"],
                    "total_correct": total_correct,
                    "total_testcases": total_testcases,
                    "raw_response": fd["raw_response"]}
        raw = dict(stage="validate", validate=(vr.stderr or "")[-2000:])
        if flag_fb:   # invalid custom flags → recorded, compiled on defaults
            raw.update(flag_fb)
        return dict(
            status_msg="Accepted",
            total_correct=total_correct,
            total_testcases=total_testcases,
            raw_response=raw,
        )
    finally:
        try:
            if backup.exists():
                shutil.copy2(backup, source)
                backup.unlink()
        except Exception as e:  # noqa: BLE001
            print(f"[perfarena_handler] WARN failed to restore {source}: {e}")


# Vocabulary for the `metrics` selector on the /measure body. Callers
# ask for any subset; the handler runs only the physical steps needed to
# satisfy the requested metrics — cost scales with what you asked for,
# not with the maximum possible. `time` and `energy` share a run (both
# come out of `make measure`), so requesting one runs measure; memory
# always costs a separate `make run` (paper methodology).
_KNOWN_METRICS = frozenset({"time", "energy", "memory", "correctness"})
# Default is perf-only — correctness is opt-in (it is NOT in the default set),
# so /measure never runs `make validate` unless a caller asks for it.
_DEFAULT_METRICS = frozenset({"time", "energy", "memory"})


def _resolve_metrics(metrics: Any) -> frozenset[str]:
    """Return the requested metric set, defaulting to all three when the
    caller passed None. Unknown labels raise ValueError so a typo
    surfaces at the API boundary rather than as silently-skipped work."""
    if metrics is None:
        return _DEFAULT_METRICS
    requested = frozenset(str(m).lower() for m in metrics)
    if not requested:
        return _DEFAULT_METRICS
    unknown = requested - _KNOWN_METRICS
    if unknown:
        raise ValueError(
            f"unknown metrics: {sorted(unknown)}; "
            f"known: {sorted(_KNOWN_METRICS)}")
    return requested


def _run_one_measurement(cell, language: str, title_slug: str,
                         requested, do_measure: bool, do_mem: bool,
                         energy_backend: Any = _MAKE_OWN_BACKEND,
                         deadline: float | None = None) -> dict[str, Any]:
    """ONE measurement pass (the curated per-op loop + result dict).
    measure_once calls this N times AFTER a single compile — it never
    recompiles, so a compiled language pays the build cost once per batch.
    `energy_backend` is the shared per-submission backend (built once by
    measure_once and reused across repeats); the default sentinel makes this
    call build + own its own, preserving standalone behaviour."""
    looped: dict[str, Any] = {}
    looped_wall_s: float | None = None
    if do_measure or do_mem:
        budget = float(os.environ.get("PERFARENA_BUDGET_S", "1.5"))
        per_case_timeout = float(os.environ.get("PERFARENA_CASE_TIMEOUT_S", "180"))
        t0 = time.perf_counter()
        looped = _measure_curated_looped(
            cell, language, title_slug, budget, per_case_timeout,
            measure_energy=do_measure, measure_memory=do_mem,
            energy_backend=energy_backend, deadline=deadline)
        looped_wall_s = time.perf_counter() - t0
        _log(f"curated measure total {looped_wall_s:.2f}s")

    if do_measure:
        produced = looped.get("time_us_per_op") is not None
        status = "Accepted" if produced else "Runtime Error"
    elif do_mem:
        status = "Accepted" if looped.get("peak_rss_kb") else "Runtime Error"
    else:
        status = "Accepted"

    t_us = looped.get("time_us_per_op")
    e_op = looped.get("energy_j_per_op")
    runtime_ms = (t_us / 1000.0) if (t_us is not None and "time" in requested) else None
    energy_j = e_op if (e_op is not None and "energy" in requested) else None
    memory_mb = ((looped.get("peak_rss_kb") or 0) / 1024.0) if do_mem and looped.get("peak_rss_kb") else None

    machine = _machine_descriptor()
    _src = looped.get("energy_source")
    energy_tool_out: str | None = (
        _src if (energy_j is not None and _src
                 and _src not in ("none", "codecarbon-error"))
        else None
    )

    raw_response = dict(
        stage="curated-looped",
        requested_metrics=sorted(requested),
        energy_source=looped.get("energy_source"),
        budget_s=float(os.environ.get("PERFARENA_BUDGET_S", "1.5")),
        n_cases=looped.get("n_cases"),
        iterations=looped.get("iterations"),
        energy_j_per_op=e_op,
        time_us_per_op=t_us,
        peak_rss_kb=looped.get("peak_rss_kb"),
        memory_method=looped.get("memory_method"),
        any_nonzero_exit=looped.get("any_nonzero_exit"),
        measure_wall_s=looped_wall_s,
        per_case=looped.get("per_case"),
        energy_tool=energy_tool_out,
        machine=machine,
        cell=str(cell),
    )
    # On a measure-time Runtime Error the real harness error lives in each
    # per-case `stderr_tail`, under keys _build_error_log doesn't grep. Lift it
    # to a top-level `error_log` (an _ERROR_FIELDS key) so the UI shows WHY the
    # perf run crashed instead of a blank panel.
    if status == "Runtime Error":
        tails = [f"[case {c.get('name') or c.get('idx')}] {c.get('stderr_tail')}"
                 for c in (looped.get("per_case") or [])
                 if c.get("exit_code") not in (0, None) and c.get("stderr_tail")]
        raw_response["note"] = "perf harness runtime error during measurement"
        raw_response["error_log"] = ("\n".join(tails) if tails else
            "measurement produced no per-op metric (harness exited non-zero or "
            "emitted no CASE line); no stderr captured.")

    return dict(
        status_msg=status,
        runtime_ms=runtime_ms,
        memory_mb=memory_mb,
        runtime_energy_j=energy_j,
        iterations=looped.get("iterations"),
        energy_j_per_op=(e_op if "energy" in requested else None),
        time_us_per_op=(t_us if "time" in requested else None),
        energy_tool=energy_tool_out,
        machine=machine,
        config=_measure_config(),
        deadline_exceeded=looped.get("deadline_exceeded", False),
        raw_response=raw_response,
    )


def _measure_clbg_samples(
    cell: Path, folder: str, title_slug: str,
    requested, do_measure: bool, do_mem: bool, timeout: float,
) -> "list[dict[str, Any]]":
    """Measure a CLBG whole-program benchmark and return ONE result PER measured
    sample.

    CLBG cells are entire programs, not LeetCode `Solution` methods: they carry
    NO `test_suite.py` / curated per-op harness. `make measure` runs the program
    under the energy runner PERFARENA_CLBG_MEASURE_RUNS times (after
    PERFARENA_CLBG_WARMUP_RUNS warmups), appending one JSONL row per measured
    run. Those measured runs ARE the run-to-run samples — so we run `make
    measure` ONCE and emit each sample as its own result. This is why CLBG must
    NOT also be looped by the dispatch-level `repeats`: that would re-run the
    whole MEASURE loop `repeats` times (repeats × MEASURE executions) for no
    extra information. Memory (deterministic) is measured once and attached to
    the first sample. CLBG numbers are whole-program, so `*_per_op` stay None."""
    machine = _machine_descriptor()

    def _one(runtime_ms, energy_j, energy_source, memory_mb, any_nonzero):
        # A nonzero benchmark exit means the program crashed on at least one
        # measured run — never report that as a clean Accepted measurement.
        if any_nonzero:
            status = "Runtime Error"
        else:
            status = "Accepted" if (runtime_ms is not None or energy_j is not None
                                    or memory_mb is not None) else "Runtime Error"
        energy_tool_out = (energy_source if (energy_j is not None and energy_source
                           and energy_source not in ("none", "codecarbon-error"))
                           else None)
        return dict(
            status_msg=status, runtime_ms=runtime_ms, memory_mb=memory_mb,
            runtime_energy_j=energy_j, iterations=None, energy_j_per_op=None,
            time_us_per_op=None, energy_tool=energy_tool_out, machine=machine,
            config=_measure_config(),
            raw_response=dict(stage="clbg-make-measure",
                              requested_metrics=sorted(requested),
                              energy_source=energy_source, runtime_ms=runtime_ms,
                              peak_rss_mb=memory_mb, any_nonzero_exit=any_nonzero,
                              machine=machine, cell=str(cell)))

    memory_mb = None
    if do_mem:
        # The memory pass runs `make run` once (peak RSS via getrusage). Log it
        # as its own timed phase: it precedes `make measure`, so a stall here
        # (e.g. a stdin-input program blocking on an unredirected stdin) would
        # otherwise show as a silent gap before any codecarbon output.
        _log_always(f"{title_slug} ({folder}) measuring peak memory (make run)...")
        _t_mem = time.monotonic()
        memory_mb, _diag = _measure_mem(cell, timeout)
        _dt_mem = time.monotonic() - _t_mem
        if memory_mb is not None:
            _log_always(f"{title_slug} ({folder}) peak memory = "
                        f"{memory_mb:.1f} MB in {_dt_mem:.1f}s")
        else:
            _log_always(f"{title_slug} ({folder}) peak memory unavailable in "
                        f"{_dt_mem:.1f}s ({_diag.get('note', '?')})")

    if not do_measure:
        # Memory-only request: one result carrying the single memory reading.
        return [_one(None, None, None, memory_mb, False)]

    # Snapshot how many measure rows already exist so _parse_metrics can count
    # ONLY the samples this run appends (the JSONL is never truncated).
    baseline = _count_measure_rows(cell, folder, title_slug)

    def _progress(line: str) -> None:
        # codecarbon_runner prints a "[codecarbon-runner] <phase>..." line for
        # every phase: energy-tracker init, idle baseline, and each warmup /
        # measure k/total iteration. Re-emit ALL of them as timestamped
        # heartbeats (not just the k/total ones) so a long CLBG measure shows
        # live progress instead of a silent gap, and any stall is attributable
        # to the phase whose line printed last.
        s = line.strip()
        if s.startswith("[codecarbon-runner]"):
            _log_always(f"{title_slug} ({folder}) "
                        f"{s[len('[codecarbon-runner]'):].strip()}")

    try:
        mr = _run(["make", "-s", "measure"], cwd=cell, timeout=timeout,
                  on_stderr_line=_progress)
    except subprocess.TimeoutExpired:
        return [{
            "status_msg": "Time Limit Exceeded", "runtime_ms": None,
            "memory_mb": None, "runtime_energy_j": None,
            "machine": machine, "config": _measure_config(),
            "raw_response": {"stage": "clbg-make-measure",
                             "note": f"`make measure` exceeded {timeout:.0f}s "
                                     f"wall clock and was killed",
                             "cell": str(cell)}}]
    if mr.returncode != 0:
        return [_fail_dict("measure", "Runtime Error", mr)]
    try:
        m = _parse_metrics(cell, folder, title_slug, baseline=baseline)
    except RuntimeError as e:
        # Reached only when `make` exited 0 but did NOT leave a complete, fresh
        # sample set — the run was killed/timed out mid-measure. Report a
        # timeout, never a (stale-data) Accepted.
        return [{
            "status_msg": "Time Limit Exceeded", "runtime_ms": None,
            "memory_mb": None, "runtime_energy_j": None,
            "machine": machine, "config": _measure_config(),
            "raw_response": {"stage": "clbg-make-measure",
                             "note": "CLBG `make measure` did not leave a "
                                     "complete fresh sample set (killed or "
                                     "timed out)",
                             "error_log": str(e), "cell": str(cell)}}]

    want_t = "time" in requested
    want_e = "energy" in requested
    src = m.get("energy_source")
    any_nonzero = bool(m.get("any_nonzero_exit"))
    walls = m.get("walls_ms") or []
    energies = m.get("energies_j") or []
    n = max(len(walls), len(energies), 1)   # one result per measured sample
    results: list[dict[str, Any]] = []
    for i in range(n):
        rt = (walls[i] if i < len(walls) else None) if want_t else None
        en = (energies[i] if i < len(energies) else None) if want_e else None
        # Attach the single memory reading to the first sample only.
        results.append(_one(rt, en, src, memory_mb if i == 0 else None, any_nonzero))
        # Per-sample breakdown so each measured run's metrics are visible.
        _rt = f"{rt:.1f}ms" if rt is not None else "n/a"
        _en = f"{en:.2f}J" if en is not None else "n/a"
        _mem = f" mem={memory_mb:.1f}MB" if (i == 0 and memory_mb is not None) else ""
        _log_always(f"sample {i + 1}/{n} for {title_slug} ({folder}): "
                    f"rt={_rt} energy={_en}{_mem}")
    return results


def measure_once(
    title_slug: str, language: str, code: str,
    metrics: Any = None, repeats: int = 1, do_compile: bool = False,
    meta: "dict[str, Any] | None" = None,
) -> "list[dict[str, Any]]":
    """Adapter matching harness_runner_server.py's --measure-handler protocol.

    Returns a dict shaped for arena_local_runner._normalise_run:
        status_msg, runtime_ms, memory_mb, runtime_energy_j, raw_response

    `metrics` selects which measurements to run:
        None or {"time","energy","memory"}  → run both make-measure and
                                              make-run (default)
        {"time","energy"} or {"energy"}     → only make-measure
                                              (memory_mb stays None)
        {"time"}                            → only make-measure
                                              (energy_j stays None
                                              via profiler agnosticism)
        {"memory"}                          → only make-run
                                              (runtime_ms + energy_j
                                              stay None)

    time+energy share a physical run because separating them would
    require a second measure pass; memory always costs its own run
    (paper methodology).
    """
    _apply_compile_flags_env(meta)   # export PERFARENA_COMPILE_FLAGS for compiles
    requested = _resolve_metrics(metrics)
    do_measure = bool(requested & {"time", "energy"})
    do_mem = "memory" in requested
    # Correctness is opt-in: /measure only runs `make validate` when a caller
    # explicitly asks ("correctness" in metrics). Otherwise it is perf-only and
    # never emits a Wrong Answer verdict — correctness comes from the
    # submission's correctness oracle (its /validate), dispatched separately.
    do_validate = "correctness" in requested
    cell = _cell_dir(title_slug, language)
    folder, _ext = _LANG_MAP[language]
    source = _discover_source(cell)
    backup = source.with_suffix(source.suffix + ".perfarena_backup")
    _log(f"measure_once START slug={title_slug} lang={language} "
         f"do_measure={do_measure} do_mem={do_mem}")

    # Per-invocation timeout — measure is by far the slowest step
    # (PERFARENA_WARMUP + PERFARENA_MEASURE iterations at the
    # default N each). Bump PERFARENA_HANDLER_TIMEOUT_S if a specific
    # cell/language routinely exceeds this.
    timeout_s = float(os.environ.get("PERFARENA_HANDLER_TIMEOUT_S", "1200"))

    # Snapshot the JSONL length BEFORE our run so we can attribute only
    # newly-appended rows to this attempt — the runner appends without
    # truncating.
    jsonl_path = cell.parent / f"{folder}.jsonl"
    pre_bytes = jsonl_path.stat().st_size if jsonl_path.exists() else 0

    try:
        # Back up the source only if it exists — a fresh cell (no committed
        # solution.*, e.g. a just-generated or cleared cell) has none, and we
        # overwrite it with the model's code here anyway. Without this guard
        # copy2 raised FileNotFoundError and such a cell could NEVER bootstrap
        # (write never ran). The finally leaves the written file when there was
        # no backup, so the next attempt has a source and restores normally.
        if source.exists():
            shutil.copy2(source, backup)
        source.write_text(_normalize_solution_code(
            language, code, _is_clbg_cell(cell, title_slug)))

        # 1. compile — ONCE, only when explicitly requested. Default /measure
        # assumes the artifact is already built (compile once, then measure
        # many); it never recompiles per repeat.
        measure_flag_fb: "dict[str, Any] | None" = None
        if do_compile:
            _tc = time.perf_counter()
            rc, measure_flag_fb = _compile_with_flag_fallback(
                ["make", "-s", "clean", "compile"], cell, 60.0)
            _log(f"compile rc={rc.returncode} in {time.perf_counter() - _tc:.2f}s")
            if rc.returncode != 0:
                return [_fail_dict("compile", "Compile Error", rc)]

        # 2. validate (correctness — diff against reference output) — ONLY when
        # explicitly requested ("correctness" metric). Perf-only by default;
        # correctness is the correctness oracle's job, not the perf harness's.
        if do_validate:
            _tv = time.perf_counter()
            # CLBG benchmarks validate via the golden-output `make validate`
            # path; only LeetCode problems go through the full-suite validator
            # (which has no workload for CLBG and would crash). See _is_clbg_cell.
            if _is_clbg_cell(cell, title_slug):
                vr = _run(["make", "-s", "validate"], cwd=cell, timeout=120.0)
                _vstatus = _make_validate_status(vr.returncode, vr.stderr or "")
            else:
                vr = _full_suite_validate(cell, language)
                if vr.returncode == 2:
                    # rc 2 = full-suite validator can't run; fall back to the legacy
                    # single-case `make validate` and read its cause the same way.
                    vr = _run(["make", "-s", "validate"], cwd=cell, timeout=120.0)
                    _vstatus = _make_validate_status(vr.returncode, vr.stderr or "")
                else:
                    _vstatus = {0: "Accepted", 3: "Runtime Error"}.get(vr.returncode, "Wrong Answer")
            _log(f"validate {_vstatus} in {time.perf_counter() - _tv:.2f}s")
            if _vstatus != "Accepted":
                return [_fail_dict("validate", _vstatus, vr)]

        # 3. Measure N times against the single compiled artifact — the compile
        # above ran at most ONCE (never per repeat). Each pass is an independent
        # per-op measurement; together they give the run-to-run spread. Each
        # "test suite run" logs its iteration number and elapsed time on
        # completion so the local perf server shows measurement progress.
        n_runs = max(1, int(repeats))
        # Peak RSS is deterministic run-to-run, so memory needs far fewer
        # samples than energy/time. When energy/time is the primary metric,
        # only the first PERFARENA_MEM_REPEATS repeats also run the separate
        # memory pass; the rest measure energy/time only. A memory-only
        # request (do_measure False) keeps memory on every pass so the
        # requested sample count is honoured.
        mem_cap = max(1, int(os.environ.get("PERFARENA_MEM_REPEATS", "3")))
        # CLBG benchmarks are whole programs measured via `make measure` (no
        # per-op test_suite.py); LeetCode uses the curated per-op harness. The
        # shared codecarbon backend is only for the per-op path — `make measure`
        # runs its own energy runner — so skip building it for CLBG.
        is_clbg = _is_clbg_cell(cell, title_slug)
        # Build the energy backend ONCE for the whole submission and reuse it
        # across every repeat — the codecarbon tracker start/stop is the
        # dominant fixed per-repeat cost. None when energy isn't requested.
        shared_backend = _make_energy_backend() if (do_measure and not is_clbg) else None
        results: list[dict[str, Any]] = []
        try:
            if is_clbg:
                # CLBG: `make measure` already loops PERFARENA_CLBG_MEASURE_RUNS
                # samples internally, so do it ONCE and emit one result per
                # sample. Looping the dispatch-level `repeats` here would re-run
                # the whole MEASURE loop repeats× (repeats × MEASURE executions)
                # for no new information — the bug we're avoiding. `n_runs`
                # (=repeats) does NOT gate CLBG execution count anymore.
                _t_run = time.perf_counter()
                results = _measure_clbg_samples(
                    cell, folder, title_slug, requested, do_measure, do_mem, timeout_s)
                _log_always(f"clbg make-measure done for {title_slug} ({language}) "
                            f"in {time.perf_counter() - _t_run:.2f}s → "
                            f"{len(results)} sample(s)")
            else:
                # LeetCode measurement ceiling (default 7 min). Bound the WHOLE
                # per-op measurement, not one run: stop launching runs once the
                # accumulated wall clock reaches the ceiling and record the rest
                # as Time Limit Exceeded, so a slow solution can't run for the
                # full CLBG-sized budget. Each individual run is separately
                # bounded by PERFARENA_CASE_TIMEOUT_S.
                _lc_ceiling = float(
                    os.environ.get("PERFARENA_LC_MEASURE_TIMEOUT_S", "420"))

                def _tle_dict(note: str) -> "dict[str, Any]":
                    return {
                        "status_msg": "Time Limit Exceeded",
                        "runtime_ms": None, "memory_mb": None,
                        "runtime_energy_j": None,
                        "machine": _machine_descriptor(),
                        "config": _measure_config(),
                        "raw_response": {"stage": "lc-measure", "note": note}}

                # First-run canary. If run 1 alone eats more than its share of the
                # whole measurement budget, the n_runs repeats cannot fit, so cap
                # run 1 and bail early instead of waiting out the full ceiling.
                # Cap = max(even split across runs, a third of the budget): a
                # third leaves room for at least three runs, and the even split
                # covers fewer than three runs. PERFARENA_FIRST_RUN_CAP_FRACTION=0
                # disables the cap.
                _first_run_frac = float(
                    os.environ.get("PERFARENA_FIRST_RUN_CAP_FRACTION", "3"))
                _first_run_cap = (
                    max(_lc_ceiling / n_runs, _lc_ceiling / _first_run_frac)
                    if _first_run_frac > 0 else None)
                _loop_start = time.perf_counter()
                for i in range(n_runs):
                    if i > 0 and (time.perf_counter() - _loop_start) >= _lc_ceiling:
                        _log_always(
                            f"LC measure ceiling {_lc_ceiling:.0f}s hit for "
                            f"{title_slug} ({language}) after {i}/{n_runs} run(s)"
                            f" — remaining marked Time Limit Exceeded")
                        for _ in range(i, n_runs):
                            results.append(_tle_dict(
                                f"LeetCode measurement exceeded the "
                                f"{_lc_ceiling:.0f}s ceiling"))
                        break
                    run_mem = do_mem and (not do_measure or i < mem_cap)
                    _t_run = time.perf_counter()
                    _deadline = (_t_run + _first_run_cap) if (
                        i == 0 and _first_run_cap is not None) else None
                    _res = _run_one_measurement(
                        cell, language, title_slug, requested,
                        do_measure, run_mem, energy_backend=shared_backend,
                        deadline=_deadline)
                    # First-run canary tripped: run 1 could not finish within its
                    # cap, so the repeats will not fit the budget. Mark every run
                    # Time Limit Exceeded and stop, rather than measuring on.
                    if i == 0 and _res.get("deadline_exceeded"):
                        _log_always(
                            f"first-run cap {_first_run_cap:.0f}s hit for "
                            f"{title_slug} ({language}) — marking all {n_runs} "
                            f"run(s) Time Limit Exceeded")
                        for _ in range(n_runs):
                            results.append(_tle_dict(
                                f"first measurement run exceeded its "
                                f"{_first_run_cap:.0f}s cap"))
                        break
                    results.append(_res)
                    # Always-on progress line (independent of PERFARENA_LOG) so the
                    # local perf server reports every test-suite run's iteration,
                    # elapsed time, and the metrics it produced — the primary
                    # signal for how long a submission takes and what it measured.
                    _r = _res
                    _st = _r.get("status_msg")
                    _rt = _r.get("runtime_ms")
                    _en = _r.get("runtime_energy_j")
                    _mem = _r.get("memory_mb")
                    _rt_s = f"{_rt:.1f}ms" if isinstance(_rt, (int, float)) else "n/a"
                    _en_s = f"{_en:.2f}J" if isinstance(_en, (int, float)) else "n/a"
                    _mem_s = f" mem={_mem:.1f}MB" if isinstance(_mem, (int, float)) else ""
                    _log_always(f"test-suite run {i + 1}/{n_runs} done for "
                                f"{title_slug} ({language}) in "
                                f"{time.perf_counter() - _t_run:.2f}s → {_st} "
                                f"rt={_rt_s} energy={_en_s}{_mem_s}")
        finally:
            if shared_backend is not None:
                shared_backend.close()
        # Record an invalid-flag fallback on every sample's raw_response (the
        # run compiled on defaults; _measure_config already reflects the default
        # profile since the env flags were cleared in the fallback).
        if measure_flag_fb:
            for r in results:
                rr = r.get("raw_response")
                if isinstance(rr, dict):
                    rr.update(measure_flag_fb)
        return results
    finally:
        # Always restore the canonical source, even on exception, so
        # the fork stays byte-identical to git between attempts.
        try:
            if backup.exists():
                shutil.copy2(backup, source)
                backup.unlink()
        except Exception as e:  # noqa: BLE001
            print(f"[perfarena_handler] WARN failed to restore {source}: {e}")
