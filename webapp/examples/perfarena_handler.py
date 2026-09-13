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
    "php": "php", "kotlin": "kotlin",
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
            from codecarbon import EmissionsTracker  # type: ignore
            _TRACKER_CLS = EmissionsTracker
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


def _measure_config() -> dict[str, Any]:
    """The measurement parameters that affect the per-op numbers — the config
    half of a MeasurementProfile (machine x tool x config). The energy tool is
    tracked separately; this captures the loop/budget knobs so the arena can
    dedup a submission's runs to one profile."""
    return {
        "budget_s": float(os.environ.get("PERFARENA_BUDGET_S", "1.5")),
        "mem_budget_s": float(os.environ.get("PERFARENA_MEM_BUDGET_S", "0.5")),
        "warmup_frac": 0.3,
        "skip_mem": bool(os.environ.get("PERFARENA_SKIP_MEM")),
    }


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


def _run_grouped(
    cmd: list[str], cwd: Path, timeout: float,
    env: "dict[str, str] | None" = None,
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
    CompletedProcess and re-raises subprocess.TimeoutExpired on timeout."""
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True, env=env,
    )
    # The child's group id IS its pid (start_new_session). Use proc.pid rather
    # than os.getpgid — see _killpg for why (never risk killing our own group).
    pgid = proc.pid
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _killpg(proc, pgid)
            try:
                out, err = proc.communicate(timeout=5)  # drain the now-dead group
            except subprocess.TimeoutExpired:
                # A process OUTSIDE the group still holds the pipe open. The
                # child itself is dead; do NOT block the single-threaded runner
                # waiting for a straggler's EOF — give up the output and move on.
                out, err = None, None
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
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
    kw = dict(project_name="perfarena-curated", log_level="error",
              save_to_file=False, save_to_api=False, save_to_logger=False)
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
    *, energy: "_EnergyBackend | None" = None, poll_rss: bool = False,
) -> dict[str, Any]:
    """Run `run_harness.sh <lang> <budget> <idx>` FROM `cell`. Returns raw
    per-invocation numbers; per-op normalization is the caller's job.

    `energy` is a shared per-submission backend (RAPL or reused codecarbon)
    whose begin()/end() bracket the run — None on the memory pass. `poll_rss`
    polls the process-tree peak RSS. Energy and RSS are kept in SEPARATE passes
    so the memory poll never perturbs the energy/time numbers — the CLBG
    methodology's separate-run rule (see `_measure_curated_looped`)."""
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
         f"energy={energy is not None} rss={poll_rss}")
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd, cwd=str(cell), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, start_new_session=True, env=env,
    )
    # The child's group id IS its pid (start_new_session); use proc.pid, never
    # os.getpgid (which can transiently return the RUNNER's own group). Lets us
    # reap stragglers after the leader exits without risking a self-kill.
    pgid = proc.pid

    # Drain stderr on a background thread. The harness emits one small line,
    # but a crashing solution or a chatty compiler can exceed the ~64 KB pipe
    # buffer; if we only read after exit the child blocks on write and we
    # deadlock until the timeout. A reader thread keeps the pipe empty.
    stderr_chunks: list[str] = []

    def _drain() -> None:
        try:
            if proc.stderr is not None:
                stderr_chunks.append(proc.stderr.read())
        except Exception:  # noqa: BLE001
            pass

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    peak_rss = 0
    ps_root = None
    if poll_rss:
        try:
            import psutil  # type: ignore
            ps_root = psutil.Process(proc.pid)
        except Exception:  # noqa: BLE001
            ps_root = None

    timed_out = False
    # Poll cadence: tight when sampling RSS (to catch the peak), relaxed
    # otherwise (we only need to notice exit / timeout).
    cadence = 0.003 if poll_rss else 0.01
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
        if ps_root is not None:
            try:
                procs = [ps_root, *ps_root.children(recursive=True)]
                peak_rss = max(peak_rss, sum(p.memory_info().rss for p in procs))
            except Exception:  # noqa: BLE001
                pass
        time.sleep(cadence)
    wall_s = time.monotonic() - t0
    reader.join(timeout=5)
    # Reap any straggler still in the harness group — a build daemon or a
    # detached measurement binary that outlived `bash run_harness.sh`. On the
    # normal path the leader has already exited so this is a no-op; on timeout
    # the group was already killed above. Either way nothing survives the call.
    _killpg(proc, pgid)
    stderr = "".join(stderr_chunks)
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
                "energy_source": "none", "peak_rss_kb": peak_rss // 1024,
                "iters": None, "meas_s": None, "case_name": None,
                "harness_line": "", "stderr_tail": stderr[-2000:],
                "timed_out": True}

    parsed = _parse_harness_line(stderr)
    return {
        "exit_code": int(proc.returncode), "wall_s": wall_s, "energy_j": energy_j,
        "energy_source": energy_source, "peak_rss_kb": peak_rss // 1024,
        "stderr_tail": stderr[-2000:], "timed_out": False, **parsed,
    }


# Sentinel: when passed as `energy_backend`, _measure_curated_looped builds and
# owns its own backend (legacy per-call behaviour). Any other value (including
# None) is a caller-owned backend reused across calls and NOT closed here.
_MAKE_OWN_BACKEND: Any = object()


def _measure_curated_looped(
    cell: Path, arena_lang: str, slug: str, budget: float, per_case_timeout: float,
    *, measure_energy: bool = True, measure_memory: bool = False,
    energy_backend: Any = _MAKE_OWN_BACKEND,
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

    for (idx, w, name) in cases:
        row: dict[str, Any] = {
            "idx": idx, "weight": w, "name": name,
            "iters": 0, "meas_s": None, "wall_s": 0.0,
            "energy_j_window": None, "time_us_per_op": None,
            "energy_j_per_op": None, "exit_code": None,
            "harness_line": None, "stderr_tail": None,
        }

        if measure_energy:
            r = _run_harness_measured(cell, harness_lang, budget, idx,
                                      per_case_timeout, energy=energy_backend,
                                      poll_rss=False)
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
            m = _run_harness_measured(cell, harness_lang, mem_budget, idx,
                                      per_case_timeout, energy=None,
                                      poll_rss=True)
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
        "per_case": per_case,
        "n_cases": len(cases),
        "any_nonzero_exit": any_nonzero_exit,
    }


def _root() -> Path:
    root = os.environ.get("PERFARENA_ROOT")
    if not root:
        raise RuntimeError(
            "PERFARENA_ROOT env var not set — point it at the "
            "Energy-Languages fork root (the directory containing "
            "Python/, C++/, perfarena.mk, ...).")
    p = Path(root).resolve()
    if not (p / "perfarena.mk").exists():
        raise RuntimeError(
            f"PERFARENA_ROOT={p} does not look like an Energy-Languages "
            "fork — perfarena.mk not found.")
    return p


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


def _normalize_solution_code(language: str, code: str) -> str:
    """Best-effort fixups so common model-output quirks still compile."""
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


_MEM_HELPER = r"""
import json, resource, subprocess, sys
try:
    rc = subprocess.run(['make', '-s', 'run'], cwd=sys.argv[1],
                        capture_output=True, text=True, timeout=float(sys.argv[2]))
except subprocess.TimeoutExpired as e:
    print(json.dumps({'ok': False, 'note': f'timeout: {e}'}))
    sys.exit(0)
u = resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps({
    'ok': rc.returncode == 0,
    'returncode': rc.returncode,
    'ru_maxrss': u.ru_maxrss,
    'stderr_tail': (rc.stderr or '')[-1000:],
}))
"""


def _measure_mem(cell: Path, timeout: float) -> tuple[float | None, dict[str, Any]]:
    """Peak-RSS-in-MB via `make run` in a FRESH Python subprocess. The
    fresh process is important: `getrusage(RUSAGE_CHILDREN)` is
    cumulative across the parent's children, so calling it in this
    same interpreter (which already ran `make measure` with N heavy
    codecarbon invocations) would return the peak of ALL prior
    children — not the mem run's. Spawning a helper gives it a fresh
    RUSAGE_CHILDREN starting at 0.

    Follows the original Energy-Languages methodology: memory is
    measured in a SEPARATE run from energy+time so the memory
    tracker's overhead (`getrusage` / `time -v`) doesn't inflate the
    energy/time numbers.

    Returns (memory_mb, diagnostic_dict). memory_mb is None when the
    measurement failed for any reason (child crashed, `make run`
    target missing, timeout) — the caller still records the run but
    leaves memory_mb null.
    """
    env = os.environ.copy()
    root = _root()
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}:{prev_pp}" if prev_pp else str(root)
    try:
        # Group-safe: the helper spawns `make run` -> the measurement binary,
        # all inside the helper's process group. _run_grouped SIGKILLs that
        # whole group on timeout and on return, so an overrunning `make run`
        # (or the helper's own inner make-timeout, which orphans the binary
        # within the group) leaves nothing burning CPU behind.
        rc = _run_grouped(
            [sys.executable, "-c", _MEM_HELPER, str(cell), str(timeout)],
            cwd=cell, timeout=timeout + 10.0, env=env,
        )
    except subprocess.TimeoutExpired as e:
        return None, {"note": f"mem helper timeout: {e}"}
    if rc.returncode != 0:
        return None, {
            "note": f"mem helper crashed rc={rc.returncode}",
            "stderr": rc.stderr[-1000:],
        }
    try:
        payload = json.loads(rc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None, {"note": f"mem helper produced no JSON: {rc.stdout[-400:]}"}
    if not payload.get("ok"):
        return None, {
            "note": "mem helper reported failure",
            **payload,
        }
    ru_maxrss = int(payload["ru_maxrss"])
    # ru_maxrss unit is bytes on macOS, kilobytes on Linux — the
    # child process ran on the same platform as the parent so it
    # follows the same rule.
    mb = (ru_maxrss / (1024 * 1024)) if sys.platform == "darwin" else (ru_maxrss / 1024)
    return max(mb, 0.0), {
        "stage": "mem",
        "returncode": payload.get("returncode"),
        "ru_maxrss": ru_maxrss,
        "ru_maxrss_unit": "bytes" if sys.platform == "darwin" else "kilobytes",
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


def _run(cmd: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
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
    return _run_grouped(cmd, cwd, timeout, env=env)


def _parse_metrics(cell: Path, folder: str, title_slug: str) -> dict[str, Any]:
    """Parse the RAPL/codecarbon runner's JSONL output for THIS cell.

    Both runners write to `../<Folder>.jsonl` (relative to the cell),
    appending. We take the trailing rows that match (test=title_slug,
    language=folder, phase=measure) and average across iterations."""
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
    tail = rows[-n_measure:]

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
        source.write_text(_normalize_solution_code(language, code))

        rc = _run(["make", "-s", "clean", "compile"], cwd=cell, timeout=60.0)
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
            status = "Accepted" if vr.returncode == 0 else "Wrong Answer"
            # clbg_validate.sh emits `passed=<K> ncases=<N>` — same shape the
            # full-suite parser reads, so the per-run breakdown is populated too.
            total_correct, total_testcases = _parse_validate_counts(vr.stderr or "")
        else:
            vr = _full_suite_validate(cell, language)
            if vr.returncode == 2:
                vr = _run(["make", "-s", "validate"], cwd=cell, timeout=120.0)
                status = "Accepted" if vr.returncode == 0 else "Wrong Answer"
            else:
                status = {0: "Accepted", 3: "Runtime Error"}.get(vr.returncode, "Wrong Answer")
                # Full-suite validators emit `passed=<K> ncases=<N>`: K cases passed
                # before the verdict (== LeetCode total_correct), N total.
                total_correct, total_testcases = _parse_validate_counts(vr.stderr or "")
        if status != "Accepted":
            fd = _fail_dict("validate", status, vr)
            return {"status_msg": fd["status_msg"],
                    "error_log":  fd["error_log"],
                    "total_correct": total_correct,
                    "total_testcases": total_testcases,
                    "raw_response": fd["raw_response"]}
        return dict(
            status_msg="Accepted",
            total_correct=total_correct,
            total_testcases=total_testcases,
            raw_response=dict(
                stage="validate",
                validate=(vr.stderr or "")[-2000:],
            ),
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
                         energy_backend: Any = _MAKE_OWN_BACKEND) -> dict[str, Any]:
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
            energy_backend=energy_backend)
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
        raw_response=raw_response,
    )


def _measure_clbg_once(
    cell: Path, folder: str, title_slug: str,
    requested, do_measure: bool, do_mem: bool, timeout: float,
) -> dict[str, Any]:
    """ONE measurement pass for a CLBG whole-program benchmark.

    CLBG cells are entire programs, not LeetCode `Solution` methods: they carry
    NO `test_suite.py` / curated per-op harness, so the per-op path
    (_run_one_measurement -> run_harness.sh -> test_suite.py) fails with
    "can't open test_suite.py". Measure them the way they are built instead:
    `make measure` runs the program under the energy runner and appends
    wall/energy rows to <Folder>.jsonl (read by _parse_metrics), and memory
    uses the same separate `make run` pass as every language (_measure_mem).
    Mirrors _run_one_measurement's result shape so measure_once treats CLBG and
    LeetCode identically. CLBG numbers are whole-program (not per-op), so the
    `*_per_op` fields stay None."""
    runtime_ms = energy_j = memory_mb = None
    energy_source: str | None = None
    any_nonzero = False
    if do_measure:
        mr = _run(["make", "-s", "measure"], cwd=cell, timeout=timeout)
        if mr.returncode != 0:
            return _fail_dict("measure", "Runtime Error", mr)
        try:
            m = _parse_metrics(cell, folder, title_slug)
        except RuntimeError as e:
            return {
                "status_msg": "Runtime Error", "runtime_ms": None,
                "memory_mb": None, "runtime_energy_j": None,
                "machine": _machine_descriptor(), "config": _measure_config(),
                "raw_response": {"stage": "clbg-make-measure",
                                 "note": "CLBG `make measure` produced no metric",
                                 "error_log": str(e), "cell": str(cell)},
            }
        runtime_ms = m.get("runtime_ms") if "time" in requested else None
        energy_j = m.get("runtime_energy_j") if "energy" in requested else None
        energy_source = m.get("energy_source")
        any_nonzero = bool(m.get("any_nonzero_exit"))
    if do_mem:
        memory_mb, _diag = _measure_mem(cell, timeout)

    if do_measure:
        status = "Accepted" if (runtime_ms is not None or energy_j is not None) else "Runtime Error"
    elif do_mem:
        status = "Accepted" if memory_mb is not None else "Runtime Error"
    else:
        status = "Accepted"

    machine = _machine_descriptor()
    energy_tool_out = (energy_source if (energy_j is not None and energy_source
                       and energy_source not in ("none", "codecarbon-error"))
                       else None)
    raw_response = dict(
        stage="clbg-make-measure",
        requested_metrics=sorted(requested),
        energy_source=energy_source,
        runtime_ms=runtime_ms,
        peak_rss_mb=memory_mb,
        any_nonzero_exit=any_nonzero,
        machine=machine,
        cell=str(cell),
    )
    return dict(
        status_msg=status,
        runtime_ms=runtime_ms,
        memory_mb=memory_mb,
        runtime_energy_j=energy_j,
        iterations=None,
        energy_j_per_op=None,
        time_us_per_op=None,
        energy_tool=energy_tool_out,
        machine=machine,
        config=_measure_config(),
        raw_response=raw_response,
    )


def measure_once(
    title_slug: str, language: str, code: str,
    metrics: Any = None, repeats: int = 1, do_compile: bool = False,
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
        source.write_text(_normalize_solution_code(language, code))

        # 1. compile — ONCE, only when explicitly requested. Default /measure
        # assumes the artifact is already built (compile once, then measure
        # many); it never recompiles per repeat.
        if do_compile:
            _tc = time.perf_counter()
            rc = _run(["make", "-s", "clean", "compile"], cwd=cell, timeout=60.0)
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
                _vstatus = "Accepted" if vr.returncode == 0 else "Wrong Answer"
            else:
                vr = _full_suite_validate(cell, language)
                if vr.returncode == 2:
                    vr = _run(["make", "-s", "validate"], cwd=cell, timeout=120.0)
                    _vstatus = "Accepted" if vr.returncode == 0 else "Wrong Answer"
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
            for i in range(n_runs):
                run_mem = do_mem and (not do_measure or i < mem_cap)
                _t_run = time.perf_counter()
                if is_clbg:
                    results.append(_measure_clbg_once(
                        cell, folder, title_slug, requested,
                        do_measure, run_mem, timeout_s))
                else:
                    results.append(
                        _run_one_measurement(cell, language, title_slug, requested,
                                             do_measure, run_mem,
                                             energy_backend=shared_backend))
                # Always-on progress line (independent of PERFARENA_LOG) so the
                # local perf server reports every test-suite run's iteration and
                # elapsed time — the primary signal for how long a submission takes.
                _log_always(f"test-suite run {i + 1}/{n_runs} done for "
                            f"{title_slug} ({language}) in "
                            f"{time.perf_counter() - _t_run:.2f}s"
                            f"{' (+mem)' if run_mem else ''}")
        finally:
            if shared_backend is not None:
                shared_backend.close()
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
