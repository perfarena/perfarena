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
FAILURES_JSON = DATA_DIR / "anchor_failures.json"
PREMIUM_JSON = DATA_DIR / "premium_status.json"
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


def _load_failures() -> dict[str, dict]:
    """Slugs that previously failed to anchor — recorded so a resume doesn't
    re-burn LeetCode quota on durably-unanchorable problems (premium-only,
    deleted, or persistently rate-limited). Keyed by slug. Same atomic
    write pattern as anchors.json."""
    if FAILURES_JSON.exists():
        try:
            return json.loads(FAILURES_JSON.read_text())
        except json.JSONDecodeError:
            print(f"[warn] {FAILURES_JSON} is unreadable; starting fresh",
                  file=sys.stderr)
    return {}


def _save_failures(failures: dict[str, dict]) -> None:
    FAILURES_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = FAILURES_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(failures, indent=2, sort_keys=True))
    tmp.replace(FAILURES_JSON)


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ---------- LeetCode problemset metadata cache ------------------------------
#
# We need three things per problem, fetched once:
#   - isPaidOnly  → so we skip premium-only slugs (302/empty body on submit).
#   - frontend_id → so we can resolve parquet slugs that LeetCode has renamed
#                   (frontend_id is stable; titleSlug isn't — e.g.
#                   `array-partition-i` was renamed to `array-partition`,
#                   `coin-change-2` to `coin-change-ii`, etc.).
# Both come from the same paginated GraphQL call, so we cache them together.

_PROBLEMSET_QUERY = """
query problemsetQuestionList($limit: Int, $skip: Int) {
  problemsetQuestionList: questionList(
    categorySlug: "", limit: $limit, skip: $skip, filters: {}
  ) {
    total: totalNum
    questions: data { titleSlug questionFrontendId isPaidOnly }
  }
}
"""


def _fetch_problemset_meta() -> dict[str, dict]:
    """One paginated walk of LeetCode's full problem list, returning
    {current_slug: {"isPaidOnly": bool, "frontend_id": int}}."""
    from . import leetcode_dist     # imported lazily; not needed for --dry-run
    print("• fetching LeetCode problemset metadata (one paginated sweep) …",
          file=sys.stderr)
    out: dict[str, dict] = {}
    skip, limit = 0, 100
    client = leetcode_dist.build_client()
    try:
        while True:
            r = client.post("/graphql", json={
                "query": _PROBLEMSET_QUERY,
                "variables": {"limit": limit, "skip": skip},
            })
            r.raise_for_status()
            data = r.json().get("data", {}).get("problemsetQuestionList") or {}
            for q in data.get("questions", []) or []:
                try:
                    fid = int(q.get("questionFrontendId") or 0)
                except (TypeError, ValueError):
                    fid = 0
                out[q["titleSlug"]] = {
                    "isPaidOnly": bool(q.get("isPaidOnly")),
                    "frontend_id": fid,
                }
            total = int(data.get("total") or 0)
            skip += limit
            print(f"  problemset meta: {len(out)}/{total}", file=sys.stderr)
            if not data.get("questions") or skip >= total:
                break
            time.sleep(1.0)
    finally:
        client.close()
    return out


def _load_or_refresh_problemset_meta(parquet_slugs: set[str]) -> dict[str, dict]:
    """Read cache from PREMIUM_JSON; refresh when the file is missing or in
    the old `{slug: bool}` shape. We DON'T re-fetch just because a parquet
    slug is missing — that's now the expected case (stale parquet slug);
    the caller resolves those via frontend_id instead."""
    cached: dict[str, dict] = {}
    if PREMIUM_JSON.exists():
        try:
            raw = json.loads(PREMIUM_JSON.read_text())
            # Migrate the old `{slug: bool}` shape on the fly.
            if raw and isinstance(next(iter(raw.values()), {}), bool):
                cached = {}     # old shape; force re-fetch to add frontend_id
            else:
                cached = raw
        except json.JSONDecodeError:
            cached = {}
    if not cached:
        cached = _fetch_problemset_meta()
        PREMIUM_JSON.parent.mkdir(parents=True, exist_ok=True)
        tmp = PREMIUM_JSON.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cached, indent=2, sort_keys=True))
        tmp.replace(PREMIUM_JSON)
    return cached


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
    """Pre-flight: probe every non-exhausted account in the pool. Returns
    OK if AT LEAST ONE is signed in (leetbot will rotate past expired ones
    at submit time and we'll auto-mark them). Reports per-account state so
    the user knows which cookies to refresh."""
    try:
        from app.leetcode_accounts import load_accounts, whoami, mark_exhausted
    except ImportError:
        # Fallback: ask leetbot --whoami once (legacy single-account path).
        try:
            proc = subprocess.run(
                [sys.executable, str(LEETBOT), "--whoami"],
                capture_output=True, text=True, timeout=20,
                cwd=str(LEETBOT.parent),
            )
        except Exception as e:                                       # noqa: BLE001
            return False, f"whoami subprocess error: {e}"
        if proc.returncode != 0:
            return False, f"whoami exited {proc.returncode}"
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return False, "whoami returned non-JSON"
        return (data.get("isSignedIn", False),
                f"signed in as {data.get('username') or '?'}"
                if data.get("isSignedIn") else "LeetCode session expired")

    accs = load_accounts()
    if not accs:
        return False, "no LeetCode accounts in pool — add one with " \
                      "`python -m app.leetcode_accounts --add NAME --cookie '...'`"
    healthy: list[str] = []
    for a in accs:
        if a.is_exhausted():
            continue
        ok, info = whoami(a)
        if ok:
            healthy.append(f"{a.name}={info.split(' as ', 1)[-1]}")
        else:
            # Auto-mark expired accounts so the pool skips them straight away
            # — saves the per-slug discovery cost.
            print(f"  account {a.name!r}: NOT signed in ({info}) — marking exhausted",
                  file=sys.stderr)
            mark_exhausted(a.name)
    if not healthy:
        return False, ("no signed-in accounts in pool; refresh cookies via "
                       "`python -m app.leetcode_accounts --add NAME --cookie '...'`")
    return True, f"{len(healthy)} signed-in account(s): " + ", ".join(healthy)


def _harvest_one(slug: str, lang: str, code: str, timeout: int = 180) -> int | str | None:
    """Submit `code` once via leetbot. Returns the submission_id, the string
    'rate_limited' when LeetCode 429s on *every* account in the pool, or
    None on parse/network failure.

    Success path runs FIRST: leetbot rotates accounts internally on 429 and
    logs the rotation to stderr (mentioning "429"); after a successful
    rotation it still exits 0 with the sid in stdout. If we checked for
    "429" in stderr before parsing stdout we'd misread every rotation as a
    failure and discard a perfectly good submission_id.
    """
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
        # Surface rotation events so a long bootstrap shows the pool working
        # rather than silently swallowing leetbot's stderr. The rotation log
        # lines are emitted by `_rotate_on_429` in leetbot.py.
        rotations = (proc.stderr or "").count("hit 429 — marked exhausted")
        if rotations:
            print(f"\n        rotated {rotations} account(s) on 429", end="",
                  flush=True)
        # Success path: clean exit + parseable JSON with a submission_id.
        # An account rotation may have happened along the way (stderr will
        # mention "429"), but that's leetbot doing its job — we still have
        # a valid sid in stdout.
        if proc.returncode == 0:
            try:
                data = json.loads((proc.stdout or "").strip() or "{}")
                sid = data.get("submission_id")
                if sid is not None:
                    return int(sid)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        # Failure path: was it a 429? Only meaningful now that we know there
        # was no usable sid (e.g. pool fully exhausted, or all-accounts-tried).
        blob = (proc.stdout + proc.stderr).lower()
        if "rate limit" in blob or "429" in blob or "exhausted" in blob:
            return "rate_limited"
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
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-attempt slugs recorded in data/anchor_failures.json "
                         "(by default they're skipped on resume so quota isn't "
                         "burned on durably-unanchorable problems)")
    ap.add_argument("--skip-failed", action="store_true",
                    help="explicitly skip slugs recorded in "
                         "data/anchor_failures.json. This is the DEFAULT "
                         "behaviour — the flag exists for scripts that want "
                         "to spell it out and pairs naturally with --no-retry. "
                         "Mutually exclusive with --retry-failed.")
    ap.add_argument("--no-retry", action="store_true",
                    help="don't retry rate-limited slugs — fail fast and "
                         "move on. By default a rate-limited slug is retried "
                         "up to 6 times with exponential back-off (30→60→…→600s, "
                         "~25 min worst-case per slug). With --no-retry it's "
                         "recorded as a failure immediately and the run "
                         "proceeds to the next slug.")
    args = ap.parse_args()

    if args.retry_failed and args.skip_failed:
        raise SystemExit("--retry-failed and --skip-failed are mutually exclusive")

    if not PARQUET.exists():
        raise SystemExit(f"{PARQUET} not found")
    if not LEETBOT.exists():
        raise SystemExit(f"{LEETBOT} not found")

    df = pd.read_parquet(PARQUET).drop_duplicates("title_slug", keep="first")
    # The parquet's `id` column is the LeetCode `frontendQuestionId` —
    # a stable identifier across renames. We need it to resolve slugs that
    # LeetCode has since renamed (e.g. array-partition-i → array-partition).
    problems = [(r["title_slug"], int(r["id"]), r["level"])
                for _, r in df.iterrows() if r["level"] in args.levels]

    anchors = _load_anchors()
    db = _db_anchors(args.lang)
    failures = _load_failures()

    # LeetCode problemset metadata: drives both premium-skip and slug
    # resolution (the parquet has slugs that LeetCode has renamed; the
    # `frontend_id` from this metadata is the stable key we resolve against).
    lc_meta: dict[str, dict] = {}
    if not args.dry_run:
        lc_meta = _load_or_refresh_problemset_meta({s for s, _, _ in problems})
    fid_to_current_slug = {m["frontend_id"]: cs for cs, m in lc_meta.items() if m.get("frontend_id")}

    # Resolve each parquet slug to its CURRENT LeetCode slug + premium status.
    # Three outcomes per parquet row:
    #   * lc_slug exists, not premium → submittable; lc_slug may equal parquet
    #     slug or be a renamed one (which we transparently use for the submit
    #     but key the anchor under the parquet's slug for system consistency).
    #   * lc_slug exists, premium → skip (unanchorable without subscription).
    #   * no lc_slug + no fid match → slug truly deprecated by LeetCode.
    submittable: list[tuple[str, str, str]] = []   # (parquet_slug, lc_slug, level)
    premium_skip: set[str] = set()
    deprecated_slugs: set[str] = set()
    renames: dict[str, str] = {}                    # parquet_slug → lc_slug
    for parquet_slug, fid, lvl in problems:
        meta = lc_meta.get(parquet_slug)
        if meta is None:
            # Slug not in current LC listing — try frontend_id fallback.
            current = fid_to_current_slug.get(fid)
            if current is None:
                deprecated_slugs.add(parquet_slug)
                continue
            meta = lc_meta[current]
            renames[parquet_slug] = current
            lc_slug = current
        else:
            lc_slug = parquet_slug
        if meta["isPaidOnly"]:
            premium_skip.add(parquet_slug)
            continue
        submittable.append((parquet_slug, lc_slug, lvl))

    # Hygiene: clear previously-recorded failures for slugs that are now
    # definitively skipped (premium or deprecated) — they were recorded
    # before this fix as `no_submission_id_returned` and don't belong in the
    # failures file under that reason.
    stale_failure_slugs = [
        s for s in failures if s in premium_skip or s in deprecated_slugs
    ]
    if stale_failure_slugs:
        for s in stale_failure_slugs:
            del failures[s]
        _save_failures(failures)
        print(f"[hygiene] cleared {len(stale_failure_slugs)} slug(s) from "
              f"anchor_failures.json (now classified as premium-only or "
              f"deprecated)", file=sys.stderr)

    # Already-processed = anchored (success) OR previously failed (unless the
    # user explicitly asked to retry failures). Premium / deprecated were
    # already excluded above. Resumes always pick up where the last run left
    # off.
    skip_failed = {s for s in failures if not args.retry_failed}
    missing = [
        (pq, lc, lvl) for (pq, lc, lvl) in submittable
        if not anchors.get(pq, {}).get(args.lang)
        and pq not in db
        and pq not in skip_failed
    ]
    n_anchored = sum(
        1 for s, _, _ in problems
        if anchors.get(s, {}).get(args.lang) or s in db
    )
    n_skipped_failed = sum(1 for s, _, _ in problems if s in skip_failed)
    n_premium = len(premium_skip)
    n_deprecated = len(deprecated_slugs)
    n_renamed = len(renames)
    todo = missing[: args.max] if args.max else missing

    print(f"parquet scope:    {len(problems)} problems ({', '.join(args.levels)})")
    print(f"already anchored: {n_anchored} "
          f"(anchors.json: {sum(1 for s, _, _ in problems if anchors.get(s, {}).get(args.lang))}, "
          f"db Run: {sum(1 for s, _, _ in problems if s in db)})")
    print(f"premium-only:     {n_premium} (skipped — unanchorable without a "
          f"LeetCode Premium subscription)")
    print(f"slug-renamed:     {n_renamed} (resolved to current LC slug via "
          f"frontend_id — submitted under the current slug, anchor stored "
          f"under the parquet slug)")
    print(f"slug-deprecated:  {n_deprecated} (not findable on LeetCode by "
          f"slug or frontend_id — likely retired)")
    print(f"prior failures:   {n_skipped_failed}"
          f"{' (will retry — --retry-failed)' if args.retry_failed else ' (skipped — pass --retry-failed to re-attempt)'}")
    print(f"still to do:      {len(missing)}")
    print(f"this run:         {len(todo)} (lang={args.lang}"
          f"{', --max ' + str(args.max) if args.max else ''})")

    # Credential pool status: leetbot transparently rotates accounts on 429,
    # so it's useful to see up front how many accounts can take submissions
    # right now (and when exhausted ones will roll over).
    try:
        from app.leetcode_accounts import pool_summary_line
        print(pool_summary_line())
    except ImportError:
        pass

    if not todo:
        print("nothing to do — all anchored.")
        return

    eta_h = (len(todo) / max(args.rate, 0.001)) / 3600
    print(f"estimated time:  ~{eta_h:.1f} h at {args.rate} req/sec")

    if args.dry_run:
        print("\nfirst 10 to harvest:")
        for parquet_slug, lc_slug, lvl in todo[:10]:
            tag = "" if parquet_slug == lc_slug else f"  →  {lc_slug}  (renamed)"
            print(f"  {lvl:<6} {parquet_slug}{tag}")
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

    # With --no-retry we attempt each slug exactly once; a rate-limit is
    # recorded immediately and the run moves on. Default (6 attempts) gives
    # ~30+60+120+240+480+600 = ~25 min worst-case back-off per slug.
    max_retries = 1 if args.no_retry else 6
    for i, (slug, lc_slug, lvl) in enumerate(todo, 1):
        eta_m = (len(todo) - i) * delay / 60
        label = slug if slug == lc_slug else f"{slug} → {lc_slug}"
        print(f"[{i:>4}/{len(todo)} {lvl:<6}] {label:<46}  eta {eta_m:>5.0f}m  ",
              end="", flush=True)

        # Inner retry loop: on a rate-limit response we back off and re-attempt
        # the SAME slug instead of dropping it. The outer loop only advances
        # on a definitive outcome (sid harvested, hard-failed, or retries
        # exhausted), so a long run isn't peppered with silently-skipped slugs.
        # --no-retry collapses this to a single attempt with no sleep.
        sid: int | str | None = None
        for attempt in range(max_retries):
            # Submit under the CURRENT LeetCode slug (handles renames); the
            # anchor is keyed under the parquet slug for system consistency.
            sid = _harvest_one(lc_slug, args.lang, code)
            if sid != "rate_limited":
                break
            throttled += 1
            if attempt + 1 >= max_retries:
                # Last attempt — outer block will record the failure. Skip
                # the back-off sleep so we don't pay for a retry we won't do.
                break
            print(f"\n        rate-limited (attempt {attempt + 1}/{max_retries}); "
                  f"backing off {backoff:.0f}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 600.0)

        def _record_failure(reason: str) -> None:
            prev = failures.get(slug, {})
            failures[slug] = {
                "lang": args.lang,
                "reason": reason,
                "first_attempted_at": prev.get("first_attempted_at", _now_iso()),
                "last_attempted_at": _now_iso(),
                "attempts": (prev.get("attempts", 0) or 0) + 1,
            }
            _save_failures(failures)

        if sid == "rate_limited":
            if max_retries == 1:
                print("rate-limited (no retry)")
            else:
                print("        gave up after retries")
            _record_failure("gave_up_rate_limited")
            failed += 1
        elif sid is None:
            print("failed")
            _record_failure("no_submission_id_returned")
            failed += 1
        else:
            anchors.setdefault(slug, {})[args.lang] = int(sid)
            _save_anchors(anchors)
            # Clear any prior failure entry — this slug is now anchored.
            if slug in failures:
                del failures[slug]
                _save_failures(failures)
            print(f"sid={sid}")
            success += 1
            backoff = 30.0      # reset on success

        time.sleep(delay)

    elapsed_m = (time.time() - start) / 60
    total_anchors = sum(len(v) for v in anchors.values())
    print(f"\ndone in {elapsed_m:.0f}m — success={success}  failed={failed}  "
          f"rate_limited={throttled}")
    print(f"{ANCHORS_JSON} now holds {total_anchors} anchor(s) across {len(anchors)} slug(s).")
    if failures:
        print(f"{FAILURES_JSON} records {len(failures)} failed slug(s) — "
              f"skipped on next resume unless you pass --retry-failed.")


if __name__ == "__main__":
    main()
