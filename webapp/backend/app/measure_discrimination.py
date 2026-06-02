"""Per-(problem, language) efficiency-DISCRIMINATION data.

The novel question PerfArena can answer that existing benchmarks can't:
*which problems can actually measure efficiency in which languages?* A problem
whose runtime histogram is piled at LeetCode's resolution floor (high
mode-share, ~0 ms for most submissions) can't distinguish a fast model from a
slow one — and crucially this is LANGUAGE-DEPENDENT: `rectangle-overlap` has
~33% mode-share in Python (discriminative) but ~99.7% in Java (noise floor,
JIT crushes it).

This script builds `data/discrimination.json`:

    { "<language>": { "<correct_slug>": {
        "level", "mode_share", "spread_ratio",
        "p10_ms", "p50_ms", "p95_ms", "spread_ms",
        "n_buckets", "discriminative"   # bool, by the same filters as ranking
    }, ... }, ... }

`discriminative` = mode_share ≤ 50 AND spread_ms ≥ 10 AND p50 ≥ 10 AND
n_buckets ≥ 15 — i.e. the problem's histogram can tell fast from slow IN
THIS LANGUAGE.

Sources:
  --from-ranking   Seed python3 from data/runtime_ranking.json (which already
                   carries mode_share + the spread signals). NO LeetCode calls.
                   Corrects the legacy bogus slugs via the LC catalog +
                   backup parquet (same fix_parquet did).
  --language LANG  Measure LANG for the sampled problems by hitting
                   submissionDistribution per anchor. Needs a per-LANG anchor
                   submission_id for each problem; with --bootstrap-anchors it
                   submits a one-off stub to harvest a missing anchor first
                   (costs LeetCode quota — paces conservatively).

Run:
    python -m app.measure_discrimination --from-ranking
    python -m app.measure_discrimination --language cpp        # if anchors exist
    python -m app.measure_discrimination --language cpp --bootstrap-anchors
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).parent.parent.parent.parent
DATA = REPO_ROOT / "data"
RUNTIME_RANKING_JSON = DATA / "runtime_ranking.json"
PREMIUM_JSON = DATA / "premium_status.json"
PARQUET = DATA / "problems.parquet"
DISCRIM_JSON = DATA / "discrimination.json"
SAMPLED_JSON = DATA / "sampled_problems.json"
ANCHORS_JSON = DATA / "anchors.json"

# Same thresholds as rank_problems_by_runtime + the mode-share filter.
MIN_P50_MS = 10.0
MIN_SPREAD_MS = 10.0
MIN_BUCKETS = 15
MAX_MODE_SHARE = 50.0


def _is_discriminative(p50, spread, n_buckets, mode_share) -> bool:
    return (
        (p50 or 0) >= MIN_P50_MS
        and (spread or 0) >= MIN_SPREAD_MS
        and (n_buckets or 0) >= MIN_BUCKETS
        and (mode_share if mode_share is not None else 100.0) <= MAX_MODE_SHARE
    )


def _load_discrim() -> dict:
    if DISCRIM_JSON.exists():
        try:
            return json.loads(DISCRIM_JSON.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_discrim(d: dict) -> None:
    DISCRIM_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = DISCRIM_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True))
    tmp.replace(DISCRIM_JSON)


def _legacy_slug_map() -> dict[str, str]:
    """bogus runtime_ranking slug → correct LC slug, via backup parquet's
    (slug→fid) joined with the LC catalog's (fid→current slug)."""
    if not PREMIUM_JSON.exists():
        return {}
    cat = json.loads(PREMIUM_JSON.read_text())
    fid_to_correct = {m["frontend_id"]: cs for cs, m in cat.items() if m.get("frontend_id")}
    backups = sorted(glob.glob(str(PARQUET) + ".broken-*"))
    src = pd.read_parquet(backups[0]) if backups else (
        pd.read_parquet(PARQUET) if PARQUET.exists() else None)
    if src is None:
        return {}
    out: dict[str, str] = {}
    for _, r in src.iterrows():
        correct = fid_to_correct.get(int(r["id"]))
        if correct:
            out[r["title_slug"]] = correct
    return out


def seed_from_ranking() -> int:
    """Populate the python3 layer of discrimination.json from runtime_ranking."""
    if not RUNTIME_RANKING_JSON.exists():
        raise SystemExit(f"{RUNTIME_RANKING_JSON} not found")
    rows = json.loads(RUNTIME_RANKING_JSON.read_text())
    slug_map = _legacy_slug_map()
    discrim = _load_discrim()
    lang_layer: dict[str, dict] = {}
    for r in rows:
        slug = slug_map.get(r["title_slug"], r["title_slug"])
        lang_layer[slug] = {
            "level": r.get("level"),
            "mode_share": r.get("mode_share"),
            "spread_ratio": r.get("spread_ratio"),
            "p10_ms": r.get("p10_ms"), "p50_ms": r.get("p50_ms"),
            "p95_ms": r.get("p95_ms"), "spread_ms": r.get("spread_ms"),
            "n_buckets": r.get("n_buckets"),
            "discriminative": _is_discriminative(
                r.get("p50_ms"), r.get("spread_ms"),
                r.get("n_buckets"), r.get("mode_share")),
        }
    discrim["python3"] = lang_layer
    _save_discrim(discrim)
    return len(lang_layer)


def measure_language(language: str, *, rate: float, bootstrap: bool) -> int:
    """Measure `language` discrimination for the sampled problems via
    submissionDistribution. Requires per-language anchors; with bootstrap it
    harvests a missing anchor by submitting a stub once."""
    from . import leetcode_dist
    sampled = json.loads(SAMPLED_JSON.read_text()) if SAMPLED_JSON.exists() else []
    slugs = [s["title_slug"] for s in sampled]
    levels = {s["title_slug"]: s["level"] for s in sampled}
    anchors = json.loads(ANCHORS_JSON.read_text()) if ANCHORS_JSON.exists() else {}

    discrim = _load_discrim()
    layer = discrim.get(language, {})
    client = leetcode_dist.build_client()
    delay = 1.0 / max(rate, 0.01)
    measured = skipped = 0
    try:
        for slug in slugs:
            sid = (anchors.get(slug, {}) or {}).get(language)
            if not sid and bootstrap:
                sid = _bootstrap_anchor(slug, language)
                if sid:
                    anchors.setdefault(slug, {})[language] = int(sid)
                    ANCHORS_JSON.write_text(json.dumps(anchors, indent=2, sort_keys=True))
            if not sid:
                skipped += 1
                continue
            details = leetcode_dist.fetch_distribution(int(sid), client=client)
            if not details:
                skipped += 1
                time.sleep(delay)
                continue
            rt = json.loads(details["runtimeDistribution"])
            buckets = rt.get("distribution") or []
            sig = _signals(buckets)
            layer[slug] = {"level": levels.get(slug), **sig,
                           "discriminative": _is_discriminative(
                               sig["p50_ms"], sig["spread_ms"],
                               sig["n_buckets"], sig["mode_share"])}
            measured += 1
            print(f"  {language}/{slug:<45} mode_share={sig['mode_share']:.1f}% "
                  f"discriminative={layer[slug]['discriminative']}", flush=True)
            time.sleep(delay)
    finally:
        client.close()
    discrim[language] = layer
    _save_discrim(discrim)
    print(f"\n{language}: measured={measured} skipped(no anchor/data)={skipped}")
    return measured


def _signals(buckets: list) -> dict:
    if not buckets:
        return {"mode_share": None, "spread_ratio": None, "p10_ms": None,
                "p50_ms": None, "p95_ms": None, "spread_ms": None, "n_buckets": 0}
    pairs = sorted(((float(k), float(p)) for k, p in buckets), key=lambda x: x[0])
    def pct(target):
        cum = 0.0
        for v, p in pairs:
            cum += p
            if cum >= target:
                return v
        return pairs[-1][0]
    p10, p50, p95 = pct(10), pct(50), pct(95)
    return {
        "mode_share": round(max(p for _, p in pairs), 3),
        "p10_ms": round(p10, 3), "p50_ms": round(p50, 3), "p95_ms": round(p95, 3),
        "spread_ms": round(p95 - p10, 3),
        "spread_ratio": round(p95 / max(p10, 1.0), 3),
        "n_buckets": len(pairs),
    }


def _bootstrap_anchor(slug: str, language: str) -> int | None:
    """Submit a stub once via leetbot to harvest a (slug, language) anchor.
    Mirrors seed_anchors_from_parquet; only for non-python3 langs that lack
    one. Returns the submission_id or None."""
    import subprocess, sys, tempfile
    LEETBOT = REPO_ROOT / "leetbot.py"
    stub = "class Solution:\n    pass\n" if language == "python3" else "// stub\n"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(stub)
        path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, str(LEETBOT), slug, language, path, "--repeat", "1"],
            capture_output=True, text=True, timeout=180, cwd=str(LEETBOT.parent),
        )
        if proc.returncode == 0:
            try:
                data = json.loads((proc.stdout or "").strip() or "{}")
                sid = data.get("submission_id")
                return int(sid) if sid else None
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
    except subprocess.TimeoutExpired:
        return None
    finally:
        Path(path).unlink(missing_ok=True)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-ranking", action="store_true",
                    help="seed python3 from runtime_ranking.json (no LeetCode calls)")
    ap.add_argument("--language", default=None,
                    help="measure this language for the sampled problems (needs anchors)")
    ap.add_argument("--bootstrap-anchors", action="store_true",
                    help="with --language: submit a stub to harvest missing anchors")
    ap.add_argument("--rate", type=float, default=2.0)
    args = ap.parse_args()

    if not args.from_ranking and not args.language:
        ap.error("pass --from-ranking and/or --language LANG")

    if args.from_ranking:
        n = seed_from_ranking()
        print(f"seeded python3: {n} problems → {DISCRIM_JSON}")
    if args.language:
        measure_language(args.language, rate=args.rate, bootstrap=args.bootstrap_anchors)


if __name__ == "__main__":
    main()
