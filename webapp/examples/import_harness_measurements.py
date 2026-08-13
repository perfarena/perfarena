#!/usr/bin/env python3
"""Import external-harness measurement bundles into PerfArena.

Walks a `perfarena_out/leetcode_measurements/` tree, one subfolder per
model, and POSTs each model's per-problem measurements to
`/api/admin/import-harness-measurements`. The server creates a Submission
per model with the runtime + runtime-energy numbers already baked in, so
the leaderboard math + energy columns light up the instant the import
finishes.

Expected folder layout (matches the perfarena-local harness output):

    <root>/leetcode_measurements/
        ollama__qwen3_4b/
            python_casewise.jsonl               # one row per (problem, case)
            python_casewise_problems.csv        # per-problem medians
            python_casewise_summary.json        # full summary + provenance
        ollama__qwen2.5_7b/
            …
        ollama__gemma4_e4b/
            …

The model_name / model_version are parsed from the subfolder name:
`{provider}__{model_slug}` → `model_name={model_slug}`, `model_version={provider}`.
Colons are hyphenated (`_4b` stays `_4b`; anything like `qwen2.5_7b` maps
back to `qwen2.5:7b` when the caller wants the LeetCode-flavour name — we
keep the underscore form as the on-disk convention).

Solution source code is read from each JSONL row's `source` path. If the
absolute path in the jsonl points at a machine that isn't this one, use
`--source-root <path>` to rewrite the prefix — everything under the
per-problem `Python/<slug>/solution.py` is kept relative.

Example:

    export ARENA_API_KEY=arena_p_jRJoJpDmuOoFxPR3JdDBMCTYp8B5uTjNH6JNVePVM
    python webapp/examples/import_harness_measurements.py \\
        --base-url http://localhost:8000 \\
        --root /Users/rar9993/Downloads/perfarena_out/leetcode_measurements \\
        --competition leetcode-99-perf \\
        --harness-slug arena-local \\
        --correctness-oracle-slug arena-local
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def http_post(url: str, token: str, body: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"HTTP {e.code} POST {url}\n{detail[:2000]}"
        ) from e


def load_problems_csv(path: Path) -> dict[str, dict]:
    """Return {slug: {median_wall_ms, median_energy_j, case_count, warmup_stable}}."""
    out: dict[str, dict] = {}
    with path.open() as f:
        r = csv.DictReader(f)
        for row in r:
            slug = row["problem"]
            out[slug] = {
                "median_case_wall_ms": float(row["median_case_wall_ms"]),
                "median_case_cpu_energy_j": float(row["median_case_cpu_energy_j"]),
                "case_count": int(row["case_count"]),
                "expected_case_count": int(row["expected_case_count"]),
                "warmup_stable": row["warmup_stable"].lower() == "true",
            }
    return out


def load_jsonl_sources(path: Path) -> dict[str, str]:
    """Scan the jsonl for the `source` path of each problem's solution.

    JSONL has many rows per problem (one per test case × measurement
    iteration); the `source` field is stable across them. We pick the
    first row per slug and read the file lazily below."""
    slug_to_source_path: dict[str, str] = {}
    slug_to_accepted: dict[str, bool] = {}
    with path.open() as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = row.get("problem")
            if not slug:
                continue
            if slug not in slug_to_source_path:
                sp = row.get("source")
                if sp:
                    slug_to_source_path[slug] = sp
                # `accepted` on the first row we see for the slug — the
                # harness records the same verdict for every case row
                # within one problem.
                slug_to_accepted[slug] = bool(row.get("accepted", True))
    return {
        slug: {"source_path": p, "accepted": slug_to_accepted.get(slug, True)}
        for slug, p in slug_to_source_path.items()
    }


def read_source(source_path: str, source_root: str | None,
                fallback_note: str) -> str:
    """Return the solution code from the given path. Applies `source_root`
    rewrite if the on-disk absolute path doesn't resolve on this machine
    (harness runs on a workstation, ingest runs on the arena host)."""
    candidates: list[Path] = []
    p = Path(source_path)
    candidates.append(p)
    if source_root:
        # Try replacing everything up to `leetcode_dataset_solutions/…` with
        # the caller-provided root.
        marker = "leetcode_dataset_solutions"
        if marker in p.parts:
            i = p.parts.index(marker)
            rewritten = Path(source_root, *p.parts[i:])
            candidates.append(rewritten)
    for c in candidates:
        try:
            if c.exists():
                return c.read_text()
        except OSError:
            continue
    # Fallback: stub with a note so the attempt row is still valid and the
    # operator knows the source wasn't reachable.
    return (
        f"# perfarena-local harness measurement — source unavailable\n"
        f"# {fallback_note}\n"
    )


def infer_model(dir_name: str) -> tuple[str, str]:
    """Parse `<provider>__<slug>` → (model_name, model_version).

    Falls back to (dir_name, "unknown") if the double-underscore
    convention isn't there."""
    if "__" not in dir_name:
        return dir_name, "unknown"
    provider, slug = dir_name.split("__", 1)
    # ollama-style model names use `:` on-wire (`qwen2.5:7b`) but the
    # harness stores them with `_` for filesystem safety. Restore the `:`
    # so the imported submission matches what a live Ollama sweep would
    # produce, keeping "same model" grouping intact on the SPA.
    canonical = slug.replace("_", ":", 1) if slug.count("_") >= 1 else slug
    # But only apply the swap when the FIRST underscore looks like a
    # tag separator (letter-digit or digit-letter around it). Otherwise
    # keep the raw dir slug so we don't corrupt names like `gemma4_e4b`.
    if ":" in canonical and any(c.isdigit() for c in canonical.split(":", 1)[0]):
        model_name = canonical
    else:
        model_name = slug
    return model_name, provider


def build_bundle(*, model_dir: Path, provider: str, model_name: str,
                 competition: str, language: str,
                 harness_slug: str, harness_name: str,
                 correctness_slug: str, correctness_name: str,
                 source_root: str | None) -> dict:
    problems_csv = model_dir / "python_casewise_problems.csv"
    jsonl = model_dir / "python_casewise.jsonl"
    if not problems_csv.exists() or not jsonl.exists():
        raise SystemExit(
            f"{model_dir.name}: missing expected files "
            f"(python_casewise_problems.csv + python_casewise.jsonl)"
        )
    problems = load_problems_csv(problems_csv)
    sources = load_jsonl_sources(jsonl)
    measurements: list[dict] = []
    for slug, agg in problems.items():
        src_info = sources.get(slug, {})
        code = read_source(
            src_info.get("source_path", ""),
            source_root,
            fallback_note=f"model={model_name} problem={slug}",
        )
        measurements.append({
            "title_slug": slug,
            "code": code,
            # Millisecond wall-clock per call (median across cases).
            "runtime_ms": agg["median_case_wall_ms"],
            # CPU energy in Joules per call (median across cases).
            "runtime_energy_j": agg["median_case_cpu_energy_j"],
            "memory_mb": None,        # harness doesn't report memory yet
            "n_test_cases": agg["case_count"],
            "accepted": src_info.get("accepted", True),
        })
    return {
        "harness_slug": harness_slug,
        "harness_name": harness_name,
        "correctness_oracle_slug": correctness_slug,
        "correctness_oracle_name": correctness_name,
        "competition_slug": competition,
        "model_name": model_name,
        "model_version": provider,
        "language": language,
        "prompt_template": (
            "(perfarena-local harness — measurements imported offline; "
            "no LLM prompt was executed at ingest time)"
        ),
        "model_params": {},
        "measurements": measurements,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--base-url", default=os.environ.get("ARENA_BASE_URL",
                                                         "http://localhost:8000"))
    ap.add_argument("--api-key", default=os.environ.get("ARENA_API_KEY"))
    ap.add_argument("--root", required=True, type=Path,
                    help="path to leetcode_measurements/ folder holding "
                         "per-model subdirs")
    ap.add_argument("--competition", default="leetcode-99-perf",
                    help="competition slug to attach the submissions to")
    ap.add_argument("--language", default="python3",
                    help="LeetCode-flavour language name for the submission")
    ap.add_argument("--harness-slug", default="arena-local")
    ap.add_argument("--harness-name", default="PerfArena Local Harness")
    ap.add_argument("--correctness-oracle-slug", default="arena-local")
    ap.add_argument("--correctness-oracle-name",
                    default="PerfArena Local Correctness Runner")
    ap.add_argument("--source-root", default=None,
                    help="rewrite the absolute path in jsonl `source` fields "
                         "before reading — use when the harness ran on a "
                         "different machine than the ingest client")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the bundle summary without POSTing")
    args = ap.parse_args()

    if not args.api_key and not args.dry_run:
        raise SystemExit("set --api-key or ARENA_API_KEY (or use --dry-run)")
    if not args.root.exists():
        raise SystemExit(f"--root not found: {args.root}")
    base = args.base_url.rstrip("/")

    model_dirs = sorted(
        d for d in args.root.iterdir()
        if d.is_dir() and (d / "python_casewise_problems.csv").exists()
    )
    if not model_dirs:
        raise SystemExit(f"no model subdirs with python_casewise_problems.csv "
                         f"under {args.root}")

    print(f"found {len(model_dirs)} model bundle(s) under {args.root}:")
    for d in model_dirs:
        print(f"  · {d.name}")
    print()

    for model_dir in model_dirs:
        model_name, provider = infer_model(model_dir.name)
        print(f"[{model_dir.name}] model={model_name} provider={provider}")
        bundle = build_bundle(
            model_dir=model_dir,
            provider=provider,
            model_name=model_name,
            competition=args.competition,
            language=args.language,
            harness_slug=args.harness_slug,
            harness_name=args.harness_name,
            correctness_slug=args.correctness_oracle_slug,
            correctness_name=args.correctness_oracle_name,
            source_root=args.source_root,
        )
        n = len(bundle["measurements"])
        n_accepted = sum(1 for m in bundle["measurements"] if m["accepted"])
        med_rt = sorted(m["runtime_ms"] for m in bundle["measurements"]
                        if m["runtime_ms"] is not None)
        med_en = sorted(m["runtime_energy_j"] for m in bundle["measurements"]
                        if m["runtime_energy_j"] is not None)
        print(f"  parsed: {n} problem(s), {n_accepted} accepted, "
              f"median runtime {med_rt[len(med_rt)//2]:.3f} ms, "
              f"median runtime energy {med_en[len(med_en)//2]:.3e} J")
        if args.dry_run:
            print("  --dry-run: not POSTing.")
            continue
        result = http_post(
            f"{base}/api/admin/import-harness-measurements",
            args.api_key, bundle,
        )
        print(f"  → submission #{result['submission_id']} "
              f"(pass@1={result['pass_at_1']:.3f}, "
              f"avg_runtime={result['avg_runtime_ms']:.3f} ms, "
              f"avg_runtime_energy={result['avg_runtime_energy_j']:.3e} J)")


if __name__ == "__main__":
    main()
