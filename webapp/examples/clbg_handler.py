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
        --measure-handler  clbg_handler:measure_once \\
        --validate-handler clbg_handler:validate_once \\
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


# Arena language slug (matches the "language" column on Attempt rows)
# -> (folder name in Energy-Languages fork, file extension). Only the
# languages CLBG-Energy-Languages actually ships with — attempts in
# other languages are rejected up front so the model never wastes an
# LLM call on something the runner can't measure.
_LANG_MAP: dict[str, tuple[str, str]] = {
    "python3":    ("Python",     ".py"),
    "cpp":        ("C++",        ".cpp"),
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
}


# Arena language slug -> the argument run_harness.sh expects. Mostly the
# identity, but the arena stores Go as "golang" (LeetCode's slug) while the
# shared inputs / harness key it under "golang" too — kept explicit so a new
# arena language that diverges from the harness name is a one-line add here.
_HARNESS_LANG: dict[str, str] = {
    "python3": "python3", "cpp": "cpp", "java": "java", "csharp": "csharp",
    "javascript": "javascript", "typescript": "typescript",
    "go": "golang", "golang": "golang", "rust": "rust", "ruby": "ruby",
    "php": "php",
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
            print(f"[clbg_handler] codecarbon unavailable ({e}); "
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


def _killpg(proc: subprocess.Popen) -> None:
    """Kill the child's WHOLE process group. The child is launched
    start_new_session=True (it's the group leader), so a plain proc.kill()
    would only reap the `bash run_harness.sh` wrapper and orphan the real
    workers (javac/g++/rustc/the compiled binary) — which keep burning CPU
    and skew the next measurement. SIGKILL the group instead."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _run_harness_measured(
    cell: Path, harness_lang: str, budget: float, idx: int, timeout: float,
    *, use_energy: bool = True, poll_rss: bool = False,
) -> dict[str, Any]:
    """Run `run_harness.sh <lang> <budget> <idx>` FROM `cell`. Returns raw
    per-invocation numbers; per-op normalization is the caller's job.

    `use_energy` wraps the run in the codecarbon tracker (energy pass).
    `poll_rss` polls the process-tree peak RSS (memory pass). They are kept
    SEPARATE (never both true) so the memory tracker's polling cost never
    perturbs the energy/time numbers — the CLBG methodology's separate-run
    rule (see `_measure_curated_looped`)."""
    run_harness = _selection_dir() / "run_harness.sh"
    cmd = ["bash", str(run_harness), harness_lang, str(budget), str(idx)]

    env = os.environ.copy()
    root = _root()
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}:{prev_pp}" if prev_pp else str(root)
    env["PATH"] = f"{str(Path(sys.executable).parent)}:{env.get('PATH', '')}"

    Tracker = _get_tracker_cls() if use_energy else None
    tracker = None
    if Tracker is not None:
        try:
            tracker = Tracker(project_name="perfarena-curated", log_level="error",
                              save_to_file=False, save_to_api=False,
                              save_to_logger=False)
            tracker.start()
        except Exception as e:  # noqa: BLE001
            print(f"[clbg_handler] tracker.start failed: {e}", file=sys.stderr)
            tracker = None

    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd, cwd=str(cell), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, start_new_session=True, env=env,
    )

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
    stderr = "".join(stderr_chunks)

    energy_j: float | None = None
    energy_source = "none"
    if tracker is not None:
        try:
            tracker.stop()
            d = tracker.final_emissions_data
            if (not timed_out and d is not None
                    and getattr(d, "energy_consumed", None) is not None):
                # codecarbon reports energy in kWh; 1 kWh = 3.6e6 J.
                energy_j = float(d.energy_consumed) * 3_600_000.0
                energy_source = _energy_tool_name()
        except Exception as e:  # noqa: BLE001
            energy_source = "codecarbon-error"
            print(f"[clbg_handler] tracker.stop failed: {e}", file=sys.stderr)

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


def _measure_curated_looped(
    cell: Path, arena_lang: str, slug: str, budget: float, per_case_timeout: float,
    *, measure_energy: bool = True, measure_memory: bool = False,
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
    per_case: list[dict[str, Any]] = []
    energy_source: str | None = None
    peak_rss_kb = 0
    any_nonzero_exit = False
    # Peak RSS is reached during solving and doesn't need the full >=1s window,
    # so the memory pass uses a short budget to keep the extra run cheap.
    mem_budget = float(os.environ.get("PERFARENA_MEM_BUDGET_S", "0.5"))

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
                                      per_case_timeout, use_energy=True,
                                      poll_rss=False)
            iters = r.get("iters") or 0
            meas_s = r.get("meas_s")
            wall_s = r.get("wall_s") or 0.0
            e_win = r.get("energy_j")
            if r.get("exit_code") != 0 or not iters:
                any_nonzero_exit = True
            if r.get("energy_source") and r["energy_source"] not in ("none", "codecarbon-error"):
                energy_source = r["energy_source"]
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
                                      per_case_timeout, use_energy=False,
                                      poll_rss=True)
            peak_rss_kb = max(peak_rss_kb, m.get("peak_rss_kb") or 0)
            row["peak_rss_kb_case"] = m.get("peak_rss_kb")
            if m.get("exit_code") not in (0, None):
                any_nonzero_exit = True

        per_case.append(row)

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
        rc = subprocess.run(
            [sys.executable, "-c", _MEM_HELPER, str(cell), str(timeout)],
            capture_output=True, text=True, timeout=timeout + 10.0, env=env,
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
    summary = (
        _first_meaningful_line(rc.stderr)
        or _first_meaningful_line(rc.stdout)
        or f"(no output; exit code {rc.returncode})"
    )
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
        raw["full_compile_error"] = stderr_tail or stdout_tail
    else:
        # validate + measure — both are runtime failures from the SPA's
        # POV. The stdout may carry the actual model output for wrong-
        # answer cases; surface it as std_output so the SPA renders it
        # in the "your output was" panel.
        raw["runtime_error"]      = summary
        raw["full_runtime_error"] = stderr_tail or stdout_tail
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
    return subprocess.run(
        cmd, cwd=str(cwd),
        capture_output=True, text=True, timeout=timeout,
        env=env,
    )


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
    # PERFARENA_MEASURE iterations (default 20) for this cell so a
    # re-run doesn't get skewed by earlier measurements.
    n_measure = int(os.environ.get("PERFARENA_MEASURE", "20"))
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
        shutil.copy2(source, backup)
        source.write_text(code)

        rc = _run(["make", "-s", "clean", "compile"], cwd=cell, timeout=60.0)
        if rc.returncode != 0:
            fd = _fail_dict("compile", "Compile Error", rc)
            # validate_once doesn't have perf fields — drop them so the
            # response shape stays lean for correctness callers.
            return {"status_msg": fd["status_msg"],
                    "error_log":  fd["error_log"],
                    "raw_response": fd["raw_response"]}
        rc = _run(["make", "-s", "validate"], cwd=cell, timeout=120.0)
        if rc.returncode != 0:
            fd = _fail_dict("validate", "Wrong Answer", rc)
            return {"status_msg": fd["status_msg"],
                    "error_log":  fd["error_log"],
                    "raw_response": fd["raw_response"]}
        return dict(
            status_msg="Accepted",
            raw_response=dict(
                stage="validate",
                stdout=rc.stdout[-2000:] if rc.stdout else None,
            ),
        )
    finally:
        try:
            if backup.exists():
                shutil.copy2(backup, source)
                backup.unlink()
        except Exception as e:  # noqa: BLE001
            print(f"[clbg_handler] WARN failed to restore {source}: {e}")


# Vocabulary for the `metrics` selector on the /measure body. Callers
# ask for any subset; the handler runs only the physical steps needed to
# satisfy the requested metrics — cost scales with what you asked for,
# not with the maximum possible. `time` and `energy` share a run (both
# come out of `make measure`), so requesting one runs measure; memory
# always costs a separate `make run` (paper methodology).
_KNOWN_METRICS = frozenset({"time", "energy", "memory"})
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


def measure_once(
    title_slug: str, language: str, code: str,
    metrics: Any = None,
) -> dict[str, Any]:
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
    cell = _cell_dir(title_slug, language)
    folder, _ext = _LANG_MAP[language]
    source = _discover_source(cell)
    backup = source.with_suffix(source.suffix + ".perfarena_backup")

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
        shutil.copy2(source, backup)
        source.write_text(code)

        # 1. compile
        rc = _run(["make", "-s", "clean", "compile"], cwd=cell, timeout=60.0)
        if rc.returncode != 0:
            return _fail_dict("compile", "Compile Error", rc)

        # 2. validate (correctness — diff against reference output)
        rc = _run(["make", "-s", "validate"], cwd=cell, timeout=120.0)
        if rc.returncode != 0:
            return _fail_dict("validate", "Wrong Answer", rc)

        # 3. Looped curated-case measurement (the fair per-language
        # methodology). Runs every curated case looped to a wall-time
        # budget (>=1s each) under the machine's energy tool, then
        # normalizes to per-op metrics that ARE comparable across
        # languages. Memory (peak RSS) is co-measured by polling the
        # process tree during the loop — the loop dwarfs the poll cost,
        # so it doesn't contaminate the perf numbers the way a naive
        # in-band tracker would.
        looped: dict[str, Any] = {}
        looped_wall_s: float | None = None
        if do_measure or do_mem:
            budget = float(os.environ.get("PERFARENA_BUDGET_S", "1.5"))
            per_case_timeout = float(
                os.environ.get("PERFARENA_CASE_TIMEOUT_S", "180"))
            t0 = time.perf_counter()
            looped = _measure_curated_looped(
                cell, language, title_slug, budget, per_case_timeout,
                measure_energy=do_measure, measure_memory=do_mem)
            looped_wall_s = time.perf_counter() - t0

        # A curated run that produced NO valid per-op on ANY case is a
        # Runtime Error (harness crashed / timed out for this solution).
        # If SOME cases produced per-op we accept (partial coverage noted
        # in raw_response.per_case).
        if do_measure:
            produced = looped.get("time_us_per_op") is not None
            status = "Accepted" if produced else "Runtime Error"
        elif do_mem:
            status = ("Accepted" if looped.get("peak_rss_kb")
                      else "Runtime Error")
        else:
            status = "Accepted"

        t_us = looped.get("time_us_per_op")
        e_op = looped.get("energy_j_per_op")
        # Legacy UI columns alias the per-op values so every existing
        # comparison view (which reads runtime_ms / runtime_energy_j)
        # shows the FAIR per-single-solve numbers with no frontend change.
        runtime_ms = (t_us / 1000.0) if (t_us is not None and "time" in requested) else None
        energy_j = e_op if (e_op is not None and "energy" in requested) else None
        memory_mb = ((looped.get("peak_rss_kb") or 0) / 1024.0) if do_mem and looped.get("peak_rss_kb") else None

        machine = _machine_descriptor()
        # Only stamp an energy tool when energy was actually MEASURED AND
        # STORED this run (A3). A time-only request still runs codecarbon
        # around the loop, but if we didn't keep an energy number the
        # submission must not claim a tool produced one.
        _src = looped.get("energy_source")
        energy_tool_out: str | None = (
            _src if (energy_j is not None and _src
                     and _src not in ("none", "codecarbon-error"))
            else None
        )

        return dict(
            status_msg=status,
            runtime_ms=runtime_ms,
            memory_mb=memory_mb,
            runtime_energy_j=energy_j,
            # ── per-op metrics (Part 4) ──
            iterations=looped.get("iterations"),
            energy_j_per_op=(e_op if "energy" in requested else None),
            time_us_per_op=(t_us if "time" in requested else None),
            # ── provenance (Parts 2+3) ──
            energy_tool=energy_tool_out,
            machine=machine,
            raw_response=dict(
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
            ),
        )
    finally:
        # Always restore the canonical source, even on exception, so
        # the fork stays byte-identical to git between attempts.
        try:
            if backup.exists():
                shutil.copy2(backup, source)
                backup.unlink()
        except Exception as e:  # noqa: BLE001
            print(f"[clbg_handler] WARN failed to restore {source}: {e}")
