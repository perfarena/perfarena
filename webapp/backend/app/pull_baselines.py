"""Pull LeetCode submissionDistribution histograms → human baselines (JSON).

For each (problem, language) pair in the 99-sample × 14 languages grid, anchor
the GraphQL `submissionDetails` query with a submission_id and derive:

  * runtime_ms_avg     = mean of the runtime histogram (ms)
  * runtime_ms_expert  = 10th-percentile bucket   (faster than 90% of accepted)
  * memory_mb_avg      = mean of the memory histogram (MB; KB→MB if needed)
  * memory_mb_expert   = 10th-percentile bucket

One JSON per pair is written to `data/baselines/<slug>__<lang>.json` and a
single combined file `data/baselines.json` (a list) for easy consumption by
`post_baselines.py`.

Anchor source — first match wins:
  1. data/anchors.json: {<slug>: {<lang>: <submission_id>, ...}, ...}
  2. webapp/backend/arena.db: latest accepted Run.submission_id_external for
     (problem, language)
  3. (none) — pair is skipped and listed in the coverage summary

Auth: same as leetbot.py — `.env` at repo root with LEETCODE_COOKIE_HEADER or
LEETCODE_SESSION + LEETCODE_CSRF_TOKEN.

Already-pulled pairs (a `data/baselines/<slug>__<lang>.json` exists) are
skipped by default. Pass `--force` to re-fetch them (e.g. when distributions
have drifted).

Run:
    python -m app.pull_baselines              # all 99 × 14, skip already-pulled
    python -m app.pull_baselines --force      # re-fetch every pair
    python -m app.pull_baselines --only python3 cpp java
    python -m app.pull_baselines --slug two-sum --lang python3
    python -m app.pull_baselines --rate 1.5   # requests per second (default 1)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlmodel import Session, select

from .db import engine
from .languages import LANGUAGES
from .models import Attempt, Problem, Run, Submission


REPO_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
SAMPLED_JSON = DATA_DIR / "sampled_problems.json"
ANCHORS_JSON = DATA_DIR / "anchors.json"
OUTPUT_DIR = DATA_DIR / "baselines"
COMBINED_JSON = DATA_DIR / "baselines.json"

GRAPHQL = """
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    runtimeDistribution
    memoryDistribution
    lang { name verboseName }
  }
}
"""


# ---------- distribution math ------------------------------------------------

def _normalize_buckets(payload: dict, *, unit_scale: float) -> list[tuple[float, float]]:
    return [
        (float(k) * unit_scale, float(pct))
        for k, pct in payload["distribution"]
    ]


def _mean(buckets: list[tuple[float, float]]) -> float:
    total = sum(p for _, p in buckets) or 1.0
    return sum(v * p for v, p in buckets) / total


def _percentile_lo(buckets: list[tuple[float, float]], pct_threshold: float) -> float:
    """Walk the histogram ascending; return the bucket value where cumulative
    density first crosses `pct_threshold` (e.g. 10 → 'expert' = faster than 90%)."""
    sorted_b = sorted(buckets, key=lambda x: x[0])
    cum = 0.0
    for v, p in sorted_b:
        cum += p
        if cum >= pct_threshold:
            return v
    return sorted_b[-1][0] if sorted_b else 0.0


def _detect_memory_unit(raw_buckets: list[tuple[str, float]]) -> tuple[float, str]:
    """LeetCode reports memory buckets in KB for most languages and (rarely)
    already in MB for some compiled languages. Heuristic: median raw bucket
    > 1024 ⇒ KB; else if all values are < 1024 and median > 1 ⇒ MB (already)."""
    raw_vals = [float(k) for k, _ in raw_buckets]
    if not raw_vals:
        return 1.0, "unknown"
    median = statistics.median(raw_vals)
    if median > 1024:
        return 1 / 1024, "KB"
    if median > 1:
        return 1.0, "MB"
    # very small values — assume KB (most likely sub-1MB programs reported in KB)
    return 1 / 1024, "KB"


def derive(runtime_dist_json: str, memory_dist_json: str) -> dict:
    rt_payload = json.loads(runtime_dist_json)
    mem_payload = json.loads(memory_dist_json)

    rt_buckets = _normalize_buckets(rt_payload, unit_scale=1.0)
    mem_scale, mem_unit_detected = _detect_memory_unit(mem_payload["distribution"])
    mem_buckets = _normalize_buckets(mem_payload, unit_scale=mem_scale)

    return {
        "runtime_ms_avg": round(_mean(rt_buckets), 3),
        "runtime_ms_expert": round(_percentile_lo(rt_buckets, 10.0), 3),
        "memory_mb_avg": round(_mean(mem_buckets), 4),
        "memory_mb_expert": round(_percentile_lo(mem_buckets, 10.0), 4),
        "_meta": {
            "lang_reported": rt_payload.get("lang"),
            "memory_unit_detected": mem_unit_detected,
            "runtime_buckets": len(rt_buckets),
            "memory_buckets": len(mem_buckets),
        },
    }


# ---------- LeetCode client (cookie auth from .env) --------------------------

def _build_client() -> httpx.Client:
    load_dotenv(REPO_ROOT / ".env")
    host = os.getenv("LEETCODE_HOST", "https://leetcode.com")
    cookie_header = os.getenv("LEETCODE_COOKIE_HEADER")
    if cookie_header:
        jar = {}
        for piece in cookie_header.split(";"):
            if "=" in piece:
                k, v = piece.strip().split("=", 1)
                jar[k] = v
        session, csrf = jar.get("LEETCODE_SESSION"), jar.get("csrftoken")
    else:
        session = os.getenv("LEETCODE_SESSION")
        csrf = os.getenv("LEETCODE_CSRF_TOKEN")
    if not session or not csrf:
        raise SystemExit(
            "missing LeetCode auth — set LEETCODE_COOKIE_HEADER or "
            "LEETCODE_SESSION + LEETCODE_CSRF_TOKEN in the repo-root .env "
            "(`python leetbot.py --whoami` to verify)"
        )
    return httpx.Client(
        base_url=host,
        cookies={"LEETCODE_SESSION": session, "csrftoken": csrf},
        headers={
            "referer": host + "/",
            "x-csrftoken": csrf,
            "content-type": "application/json",
            "user-agent": "perfarena-baseline-puller/1.0",
        },
        timeout=30,
    )


def _fetch_distribution(client: httpx.Client, submission_id: int) -> dict | None:
    """Returns the submissionDetails dict or None if the submission isn't
    available (deleted, not yours, judged failed, etc.)."""
    for attempt in range(4):
        r = client.post("/graphql", json={
            "query": GRAPHQL, "variables": {"submissionId": int(submission_id)},
        })
        if r.status_code == 429:
            sleep = (2 ** attempt) * (1 + random.uniform(-0.2, 0.2))
            print(f"  [429] backing off {sleep:.1f}s", file=sys.stderr)
            time.sleep(sleep)
            continue
        r.raise_for_status()
        data = r.json().get("data", {}).get("submissionDetails")
        if not data or not data.get("runtimeDistribution"):
            return None
        return data
    return None


# ---------- anchor resolution ------------------------------------------------

def _load_anchors() -> dict[str, dict[str, int]]:
    if not ANCHORS_JSON.exists():
        return {}
    return json.loads(ANCHORS_JSON.read_text())


def _anchor_from_db(session: Session, problem_id: int, language: str) -> int | None:
    """Most recent accepted Run with a stored external submission_id."""
    row = session.exec(
        select(Run.submission_id_external)
        .join(Attempt, Attempt.id == Run.attempt_id)
        .join(Submission, Submission.id == Attempt.submission_id)
        .where(
            Attempt.problem_id == problem_id,
            Submission.language == language,
            Run.status_msg == "Accepted",
            Run.submission_id_external.is_not(None),
        )
        .order_by(Run.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    try:
        return int(row)
    except (TypeError, ValueError):
        return None


def resolve_anchor(slug: str, language: str,
                   anchors: dict[str, dict[str, int]],
                   session: Session, slug_to_id: dict[str, int]) -> int | None:
    explicit = anchors.get(slug, {}).get(language)
    if explicit:
        return int(explicit)
    pid = slug_to_id.get(slug)
    if pid is None:
        return None
    return _anchor_from_db(session, pid, language)


# ---------- main loop --------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=1.0,
                    help="requests per second (default 1)")
    ap.add_argument("--only", nargs="+", default=None,
                    help="restrict to these language slugs")
    ap.add_argument("--slug", default=None, help="run for a single problem slug")
    ap.add_argument("--lang", default=None, help="single language (paired with --slug)")
    ap.add_argument("--no-network", action="store_true",
                    help="skip LeetCode calls; just summarise anchor coverage")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch pairs that already have a baseline JSON on disk")
    args = ap.parse_args()

    if not SAMPLED_JSON.exists():
        raise SystemExit(f"{SAMPLED_JSON} not found — run `python -m app.sample` first")
    problems = json.loads(SAMPLED_JSON.read_text())
    languages = list(LANGUAGES.keys()) if args.only is None else args.only
    if args.slug:
        problems = [p for p in problems if p["title_slug"] == args.slug]
        if args.lang:
            languages = [args.lang]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    anchors = _load_anchors()
    pairs = [(p["title_slug"], lang) for p in problems for lang in languages]
    print(f"target pairs: {len(pairs)} = {len(problems)} problems × {len(languages)} languages")

    with Session(engine) as session:
        slug_to_id = {
            p.title_slug: p.id
            for p in session.exec(select(Problem)).all()
        }
        resolved = []
        no_anchor = []
        already_done = []
        for slug, lang in pairs:
            sid = resolve_anchor(slug, lang, anchors, session, slug_to_id)
            if sid is None:
                no_anchor.append((slug, lang))
                continue
            out_path = OUTPUT_DIR / f"{slug}__{lang}.json"
            if out_path.exists() and not args.force:
                already_done.append((slug, lang))
                continue
            resolved.append((slug, lang, sid))

    print(f"  with anchor:    {len(resolved) + len(already_done)}")
    print(f"  already pulled: {len(already_done)} (skip — pass --force to refetch)")
    print(f"  to pull now:    {len(resolved)}")
    print(f"  no anchor:      {len(no_anchor)} (skipped; populate data/anchors.json or run dispatch)")
    if args.no_network:
        return
    if not resolved:
        print("nothing to pull.")
        return

    client = _build_client()
    delay = 1.0 / max(args.rate, 0.01)
    combined: list[dict] = []
    if COMBINED_JSON.exists():
        try:
            combined = json.loads(COMBINED_JSON.read_text())
        except json.JSONDecodeError:
            combined = []
    seen = {(b["title_slug"], b["language"]): i for i, b in enumerate(combined)}

    ok = err = empty = 0
    for i, (slug, lang, sid) in enumerate(resolved, 1):
        try:
            details = _fetch_distribution(client, sid)
        except Exception as e:                                       # noqa: BLE001
            print(f"[{i}/{len(resolved)}] {slug}/{lang} #{sid}: ERROR {e}")
            err += 1
            time.sleep(delay)
            continue
        if details is None:
            print(f"[{i}/{len(resolved)}] {slug}/{lang} #{sid}: no distribution (anchor invalid)")
            empty += 1
            time.sleep(delay)
            continue
        stats = derive(details["runtimeDistribution"], details["memoryDistribution"])
        record = {
            "title_slug": slug,
            "language": lang,
            "runtime_ms_avg": stats["runtime_ms_avg"],
            "runtime_ms_expert": stats["runtime_ms_expert"],
            "memory_mb_avg": stats["memory_mb_avg"],
            "memory_mb_expert": stats["memory_mb_expert"],
            "source": "leetcode-distribution",
            "anchor_submission_id": sid,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "_meta": stats["_meta"],
        }
        out_path = OUTPUT_DIR / f"{slug}__{lang}.json"
        out_path.write_text(json.dumps(record, indent=2))
        if (slug, lang) in seen:
            combined[seen[(slug, lang)]] = record
        else:
            seen[(slug, lang)] = len(combined)
            combined.append(record)
        ok += 1
        if i % 10 == 0 or i == len(resolved):
            COMBINED_JSON.write_text(json.dumps(combined, indent=2))
            print(f"[{i}/{len(resolved)}] {slug}/{lang}: "
                  f"avg {record['runtime_ms_avg']} ms / {record['memory_mb_avg']} MB "
                  f"expert {record['runtime_ms_expert']} ms / {record['memory_mb_expert']} MB")
        time.sleep(delay)

    COMBINED_JSON.write_text(json.dumps(combined, indent=2))
    print(f"\ndone — ok={ok}  no-distribution={empty}  errors={err}  "
          f"skipped(already-pulled)={len(already_done)}  "
          f"skipped(no-anchor)={len(no_anchor)}")
    print(f"  per-pair files: {OUTPUT_DIR}/")
    print(f"  combined:       {COMBINED_JSON}")


if __name__ == "__main__":
    main()
