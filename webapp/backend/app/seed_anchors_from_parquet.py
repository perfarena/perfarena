"""Bootstrap anchor submission_ids for every problem in the parquet.

For every problem in `data/problems.parquet` that doesn't yet have a
submission_id anchor (in `data/anchors.json` or in the DB's
`Run.submission_id_external`), shells out to `leetbot.py` with a minimal
syntactically-valid stub and harvests the submission_id LeetCode returns.

The harvested id is written to `data/anchors.json` ATOMICALLY after every
success — so a Ctrl-C, network blip, or token expiry never loses progress.
Re-running picks up where it left off; already-anchored problems are skipped.

The stub is designed to compile and then fail at runtime so we never
accidentally pass a problem (which would pollute future ranking experiments).
LeetCode still returns a submission_id for failed submissions, which is all
we need — the `submissionDistribution` histogram is per-(problem, language)
and is queryable through any submission_id in that pair.

Cost: at `--rate 0.1` (the default — one submission per 10s), 1,970 problems
take ~5.5 hours of wall time. The submissions DO show up in your LeetCode
account's history (as compile/runtime errors).

Usage:
    cd webapp/backend
    .venv/bin/python -m app.seed_anchors_from_parquet            # all missing
    .venv/bin/python -m app.seed_anchors_from_parquet --max 50   # short run
    .venv/bin/python -m app.seed_anchors_from_parquet --levels Hard
    .venv/bin/python -m app.seed_anchors_from_parquet --rate 0.05   # slower
    .venv/bin/python -m app.seed_anchors_from_parquet --dry-run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd
from sqlmodel import Session, select

from .db import engine
from .models import Attempt, Problem, Run, Submission

REPO_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
PARQUET = DATA_DIR / "problems.parquet"
ANCHORS_JSON = DATA_DIR / "anchors.json"
LEETBOT = REPO_ROOT / "leetbot.py"

LEVELS = ("Easy", "Medium", "Hard")

# Minimal stub solutions — syntactically valid (LeetCode's compiler accepts
# them) but missing the required method so they fail at runtime. We still get
# a submission_id, which is all we need. python3-only on purpose: most
# anchors are python3 and the per-language harness shapes get tricky.
STUBS = {
    "python3": "class Solution:\n    pass\n",
}


# ---------- anchor I/O ------------------------------------------------------

def _load_anchors() -> dict[str, dict[str, int]]:
    if ANCHORS_JSON.exists():
        try:
            return json.loads(ANCHORS_JSON.read_text())
        except json.JSONDecodeError:
            print(f"[warn] {ANCHORS_JSON} is unreadable; starting fresh", file=sys.stderr)
    return {}


def _save_anchors(anchors: dict[str, dict[str, int]]) -> None:
    """Atomic: write tmp file then rename. A crash mid-write can't truncate
    the existing anchors.json."""
    ANCHORS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = ANCHORS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(anchors, indent=2, sort_keys=True))
    tmp.replace(ANCHORS_JSON)


def _db_anchors(lang: str) -> dict[str, int]:
    """Already-anchored slugs sourced from the DB's Run table (any accepted
    real-mode dispatch). Used to skip work the platform already did."""
    out: dict[str, int] = {}
    with Session(engine) as s:
        rows = s.exec(
            select(Run)
            .where(Run.status_msg == "Accepted")
            .where(Run.submission_id_external.is_not(None))
            .order_by(Run.created_at.desc())
        ).all()
        for r in rows:
            a = s.get(Attempt, r.attempt_id)
            if not a:
                continue
            sub = s.get(Submission, a.submission_id)
            p = s.get(Problem, a.problem_id)
            if not sub or not p or sub.deleted_at is not None:
                continue
            if sub.language != lang or p.title_slug in out:
                continue
            try:
                out[p.title_slug] = int(r.submission_id_external)
            except (TypeError, ValueError):
                continue
    return out


# ---------- leetbot wrapper -------------------------------------------------

def _check_auth() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, str(LEETBOT), "--whoami"],
            capture_output=True, text=True, timeout=20, cwd=str(LEETBOT.parent),
        )
    except Exception as e:                                          # noqa: BLE001
        return False, f"whoami subprocess error: {e}"
    if proc.returncode != 0:
        return False, f"whoami exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, f"whoami returned non-JSON: {(proc.stdout or '').strip()[:200]}"
    if data.get("isSignedIn"):
        return True, f"signed in as {data.get('username') or '?'}"
    return False, "LeetCode session expired — refresh .env cookies"


def _harvest_one(slug: str, lang: str, code: str, timeout: int = 180) -> int | str | None:
    """Submit `code` once via leetbot. Returns the submission_id, the string
    'rate_limited' when LeetCode 429s, or None on parse/network failure."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        try:
            proc = subprocess.run(
                [sys.executable, str(LEETBOT), slug, lang, path, "--repeat", "1"],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(LEETBOT.parent),
            )
        except subprocess.TimeoutExpired:
            return None
        blob = (proc.stdout + proc.stderr).lower()
        if "rate limit" in blob or "429" in blob:
            return "rate_limited"
        if proc.returncode != 0:
            return None
        try:
            data = json.loads((proc.stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            return None
        sid = data.get("submission_id")
        try:
            return int(sid) if sid is not None else None
        except (TypeError, ValueError):
            return None
    finally:
        Path(path).unlink(missing_ok=True)


# ---------- main ------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=0.1,
                    help="requests per second (default 0.1 = one per 10s)")
    ap.add_argument("--lang", default="python3", choices=sorted(STUBS),
                    help="stub solution language (currently only python3)")
    ap.add_argument("--max", type=int, default=None,
                    help="cap how many problems to process this run")
    ap.add_argument("--levels", nargs="+", default=list(LEVELS),
                    choices=LEVELS)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would happen — no LeetCode calls")
    args = ap.parse_args()

    if not PARQUET.exists():
        raise SystemExit(f"{PARQUET} not found")
    if not LEETBOT.exists():
        raise SystemExit(f"{LEETBOT} not found")

    df = pd.read_parquet(PARQUET).drop_duplicates("title_slug", keep="first")
    problems = [(r["title_slug"], r["level"])
                for _, r in df.iterrows() if r["level"] in args.levels]

    anchors = _load_anchors()
    db = _db_anchors(args.lang)

    missing = [
        (slug, lvl) for slug, lvl in problems
        if not anchors.get(slug, {}).get(args.lang) and slug not in db
    ]
    n_anchored = len(problems) - len(missing)
    todo = missing[: args.max] if args.max else missing

    print(f"parquet scope:   {len(problems)} problems ({', '.join(args.levels)})")
    print(f"already anchored:{n_anchored} "
          f"(anchors.json: {sum(1 for s, _ in problems if anchors.get(s, {}).get(args.lang))}, "
          f"db Run: {sum(1 for s, _ in problems if s in db)})")
    print(f"still missing:   {len(missing)}")
    print(f"to harvest now:  {len(todo)} (lang={args.lang}"
          f"{', --max ' + str(args.max) if args.max else ''})")

    if not todo:
        print("nothing to do — all anchored.")
        return

    eta_h = (len(todo) / max(args.rate, 0.001)) / 3600
    print(f"estimated time:  ~{eta_h:.1f} h at {args.rate} req/sec")

    if args.dry_run:
        print("\nfirst 10 to harvest:")
        for slug, lvl in todo[:10]:
            print(f"  {lvl:<6} {slug}")
        print("\ndry-run: no LeetCode calls made.")
        return

    ok, msg = _check_auth()
    if not ok:
        raise SystemExit(f"auth pre-flight failed: {msg}")
    print(f"auth ok: {msg}")
    print()

    code = STUBS[args.lang]
    delay = 1.0 / max(args.rate, 0.001)
    success = failed = throttled = 0
    backoff = 30.0
    start = time.time()

    MAX_RETRIES = 6   # ~30+60+120+240+480+600 = ~25 min worst-case per slug
    for i, (slug, lvl) in enumerate(todo, 1):
        eta_m = (len(todo) - i) * delay / 60
        print(f"[{i:>4}/{len(todo)} {lvl:<6}] {slug:<46}  eta {eta_m:>5.0f}m  ",
              end="", flush=True)

        # Inner retry loop: on a rate-limit response we back off and re-attempt
        # the SAME slug instead of dropping it. The outer loop only advances
        # on a definitive outcome (sid harvested, hard-failed, or retries
        # exhausted), so a long run isn't peppered with silently-skipped slugs.
        sid: int | str | None = None
        for attempt in range(MAX_RETRIES):
            sid = _harvest_one(slug, args.lang, code)
            if sid != "rate_limited":
                break
            print(f"\n        rate-limited (attempt {attempt + 1}/{MAX_RETRIES}); "
                  f"backing off {backoff:.0f}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 600.0)
            throttled += 1

        if sid == "rate_limited":
            print("        gave up after retries")
            failed += 1
        elif sid is None:
            print("failed")
            failed += 1
        else:
            anchors.setdefault(slug, {})[args.lang] = int(sid)
            _save_anchors(anchors)
            print(f"sid={sid}")
            success += 1
            backoff = 30.0      # reset on success

        time.sleep(delay)

    elapsed_m = (time.time() - start) / 60
    total_anchors = sum(len(v) for v in anchors.values())
    print(f"\ndone in {elapsed_m:.0f}m — success={success}  failed={failed}  "
          f"rate_limited={throttled}")
    print(f"{ANCHORS_JSON} now holds {total_anchors} anchors across {len(anchors)} slugs.")


if __name__ == "__main__":
    main()
