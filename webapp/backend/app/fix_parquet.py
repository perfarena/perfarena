"""One-shot remediation for the parquet's column drift.

The source parquet (`data/problems.parquet`) ships with `title_slug`, `level`,
and `tag` columns decoupled from `id` + `question_content`. Across 1,973 rows,
46% are misaligned — the labels (slug/level/tag) point at one problem while
the id + content are correctly paired with a different problem. The selection
pipeline trusts these columns and ends up bucketing problems into wrong
difficulty levels.

What this script does (single pass, ~1 minute):

  1. Refresh the LeetCode catalog cache (`data/premium_status.json`) with
     the extended fields we need — title, difficulty, topicTags — for every
     problem on LeetCode. ~80 paginated GraphQL calls.
  2. Back up the source parquet to `problems.parquet.broken-<timestamp>`.
  3. Rewrite the parquet with corrected `title_slug`, `level`, `tag`. The
     `id` (= questionFrontendId) is the anchor; `question_content` and
     `question_hints` are left alone (those WERE correctly paired with id).
  4. Rekey `data/anchors.json` from {bogus_slug: ...} to {correct_slug: ...}
     so future ranking runs find the right anchor by slug.
  5. Re-bucket the existing `data/runtime_ranking.json` (886 measured
     problems with valid signals — just bogus level labels) using the
     corrected level for each problem.
  6. Write a new `data/sampled_problems.json` — honest top-33-per-level
     by `spread_ratio`, with truthful slugs/titles/levels/tags.

After this:
    python -m app.seed --reset
    python -m app.fetch_code_snippets --sampled-only --force

The output bears no LeetCode-side change — the platform's anchor sids,
distribution histograms, and signal numbers all stay valid. We're just
correcting the labels and re-bucketing.

Run:
    python -m app.fix_parquet
    python -m app.fix_parquet --skip-refresh    # use cached extended catalog
    python -m app.fix_parquet --dry-run         # plan only, no writes
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import time
from pathlib import Path

import httpx
import pandas as pd

from .leetcode_accounts import mark_exhausted, pick_account


REPO_ROOT = Path(__file__).parent.parent.parent.parent
DATA = REPO_ROOT / "data"
PARQUET = DATA / "problems.parquet"
PREMIUM_JSON = DATA / "premium_status.json"
ANCHORS_JSON = DATA / "anchors.json"
RUNTIME_RANKING_JSON = DATA / "runtime_ranking.json"
SAMPLED_JSON = DATA / "sampled_problems.json"

LEVELS = ("Easy", "Medium", "Hard")

# Extends the existing problemset cache with title/difficulty/topicTags. The
# catalog file's keys remain {current_slug: {…}}; we just store more per row.
_EXTENDED_QUERY = """
query problemsetQuestionList($limit: Int, $skip: Int) {
  problemsetQuestionList: questionList(
    categorySlug: "", limit: $limit, skip: $skip, filters: {}
  ) {
    total: totalNum
    questions: data {
      titleSlug
      title
      questionFrontendId
      isPaidOnly
      difficulty
      topicTags { name }
    }
  }
}
"""


def _build_client() -> httpx.Client:
    acc = pick_account()
    if acc is None:
        raise SystemExit("no signed-in LeetCode account in pool")
    sess, csrf = acc.parsed_cookies()
    c = httpx.Client(
        base_url="https://leetcode.com",
        cookies={"LEETCODE_SESSION": sess, "csrftoken": csrf},
        headers={
            "user-agent": "perfarena-parquet-fix/1.0",
            "x-csrftoken": csrf,
            "content-type": "application/json",
            "referer": "https://leetcode.com/",
            "origin": "https://leetcode.com",
        },
        timeout=30,
    )
    c._account_name = acc.name  # type: ignore[attr-defined]
    return c


def refresh_catalog() -> dict[str, dict]:
    """Paginated walk of LC's full problem list. Returns
    {current_slug: {frontend_id, isPaidOnly, title, difficulty, tags}}."""
    out: dict[str, dict] = {}
    client = _build_client()
    skip, limit = 0, 100
    try:
        while True:
            r = client.post("/graphql", json={
                "query": _EXTENDED_QUERY,
                "variables": {"limit": limit, "skip": skip},
            })
            if r.status_code == 429:
                name = getattr(client, "_account_name", None)
                if name:
                    mark_exhausted(name)
                client.close()
                client = _build_client()
                continue
            r.raise_for_status()
            data = r.json().get("data", {}).get("problemsetQuestionList") or {}
            for q in data.get("questions", []) or []:
                try:
                    fid = int(q.get("questionFrontendId") or 0)
                except (TypeError, ValueError):
                    fid = 0
                out[q["titleSlug"]] = {
                    "frontend_id": fid,
                    "isPaidOnly": bool(q.get("isPaidOnly")),
                    "title": q.get("title") or "",
                    "difficulty": q.get("difficulty") or "",
                    "tags": ",".join(t["name"] for t in (q.get("topicTags") or [])
                                     if t.get("name")),
                }
            total = int(data.get("total") or 0)
            skip += limit
            print(f"  catalog: {len(out)}/{total}", flush=True)
            if not data.get("questions") or skip >= total:
                break
            time.sleep(0.5)
    finally:
        client.close()
    return out


def write_catalog_atomic(cat: dict[str, dict]) -> None:
    PREMIUM_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = PREMIUM_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cat, indent=2, sort_keys=True))
    tmp.replace(PREMIUM_JSON)


def load_catalog() -> dict[str, dict]:
    if not PREMIUM_JSON.exists():
        return {}
    try:
        return json.loads(PREMIUM_JSON.read_text())
    except json.JSONDecodeError:
        return {}


def fix_parquet(catalog: dict[str, dict], *, dry_run: bool) -> tuple[int, int, dict[str, str]]:
    """Returns (n_fixed, n_unchanged, bogus_to_correct_slug_map)."""
    if not PARQUET.exists():
        raise SystemExit(f"{PARQUET} not found")
    df = pd.read_parquet(PARQUET)
    # Build fid -> catalog row for O(1) corrected-label lookup.
    fid_to_cat = {row["frontend_id"]: (slug, row)
                  for slug, row in catalog.items()
                  if row.get("frontend_id")}

    renames: dict[str, str] = {}            # bogus → correct slug
    n_fixed = n_unchanged = n_not_in_cat = 0
    new_slug_col: list[str] = []
    new_level_col: list[str] = []
    new_tag_col: list[str] = []
    for _, r in df.iterrows():
        fid = int(r["id"])
        match = fid_to_cat.get(fid)
        if match is None:
            # Problem isn't in LC's current catalog (deprecated). Keep
            # whatever the parquet had — we have nothing better to do.
            n_not_in_cat += 1
            new_slug_col.append(r["title_slug"])
            new_level_col.append(r["level"])
            new_tag_col.append(r["tag"])
            continue
        correct_slug, cat_row = match
        bogus_slug = r["title_slug"]
        if bogus_slug != correct_slug:
            renames[bogus_slug] = correct_slug
            n_fixed += 1
        else:
            n_unchanged += 1
        new_slug_col.append(correct_slug)
        new_level_col.append(cat_row.get("difficulty") or r["level"])
        new_tag_col.append(cat_row.get("tags") or r["tag"])

    df_new = df.copy()
    df_new["title_slug"] = new_slug_col
    df_new["level"] = new_level_col
    df_new["tag"] = new_tag_col

    print(f"parquet rows: {len(df)}  fixed_slugs: {n_fixed}  "
          f"unchanged: {n_unchanged}  not_in_lc_catalog: {n_not_in_cat}")
    if dry_run:
        print("(dry-run — parquet NOT rewritten)")
        return n_fixed, n_unchanged, renames

    # Atomic rewrite with timestamped backup of the original.
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = PARQUET.with_suffix(f".parquet.broken-{stamp}")
    shutil.copy2(PARQUET, backup)
    tmp = PARQUET.with_suffix(".parquet.tmp")
    df_new.to_parquet(tmp, index=False)
    tmp.replace(PARQUET)
    print(f"wrote corrected {PARQUET}  (backup: {backup.name})")
    return n_fixed, n_unchanged, renames


def _build_legacy_renames(catalog: dict[str, dict]) -> dict[str, str]:
    """Reconstruct the bogus → correct slug map by joining the BACKUP parquet's
    `(title_slug, id)` against the LeetCode catalog's `fid → current_slug`.

    Why this exists: `runtime_ranking.json` was authored when the parquet
    still had bogus slugs as keys. After fix_parquet rewrites the parquet,
    a fresh run's `renames` map is empty (current parquet matches catalog),
    yet runtime_ranking still needs translating. The backup parquet
    preserves the original bogus mapping so we can recover it.
    """
    import glob
    pattern = str(PARQUET) + ".broken-*"
    backups = sorted(glob.glob(pattern))
    if not backups:
        return {}
    # IMPORTANT: pick the OLDEST backup, not the latest. Every fix_parquet
    # run takes a backup of the *current* parquet — re-running on an
    # already-fixed parquet just produces backups of the corrected state,
    # which would yield an empty rename map. The first backup in time is
    # the original drifted parquet, which is what we actually need.
    try:
        df_old = pd.read_parquet(backups[0])
    except Exception:                                       # noqa: BLE001
        return {}
    fid_to_correct = {row["frontend_id"]: cs for cs, row in catalog.items()
                      if row.get("frontend_id")}
    out: dict[str, str] = {}
    for _, r in df_old.iterrows():
        correct = fid_to_correct.get(int(r["id"]))
        if correct and correct != r["title_slug"]:
            out[r["title_slug"]] = correct
    return out


def rekey_anchors(renames: dict[str, str], *, dry_run: bool) -> tuple[int, int]:
    """{bogus_slug → {lang: sid}}  becomes  {correct_slug → {lang: sid}}."""
    if not ANCHORS_JSON.exists():
        return 0, 0
    anchors = json.loads(ANCHORS_JSON.read_text())
    rekeyed = passthrough = 0
    out: dict[str, dict] = {}
    for slug, langs in anchors.items():
        new_slug = renames.get(slug, slug)
        if new_slug != slug:
            rekeyed += 1
        else:
            passthrough += 1
        # If two old slugs collide on the same new slug, merge — keep both
        # languages' SIDs.
        if new_slug in out:
            out[new_slug].update(langs)
        else:
            out[new_slug] = dict(langs)
    print(f"anchors.json: {len(anchors)} rows  rekeyed: {rekeyed}  unchanged: {passthrough}")
    if dry_run:
        return rekeyed, passthrough
    tmp = ANCHORS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2, sort_keys=True))
    tmp.replace(ANCHORS_JSON)
    return rekeyed, passthrough


def rebucket_ranking_and_resample(catalog: dict[str, dict], renames: dict[str, str],
                                  per_level: int, *, dry_run: bool,
                                  max_mode_share: float = 0.5) -> None:
    """Use the existing 886 measured signals — just correct their level labels
    and re-pick top-N per actual level. No re-measuring required.

    Additionally enforces the mode-share filter: a problem whose dominant
    histogram bucket holds more than `max_mode_share` of accepted submissions
    is dropped. That bucket is LeetCode's resolution floor — everyone in it
    gets reported as the same runtime, so the fast half of the population
    can't differentiate models even though the slow tail extends out. The
    `mode_share` field is populated by `app.add_mode_share` (rows lacking
    it are kept conservatively).
    """
    if not RUNTIME_RANKING_JSON.exists():
        print(f"!! {RUNTIME_RANKING_JSON} not found — skipping re-bucketing.")
        return
    rows = json.loads(RUNTIME_RANKING_JSON.read_text())
    fid_to_cat = {row["frontend_id"]: (slug, row)
                  for slug, row in catalog.items()
                  if row.get("frontend_id")}

    # The ranking JSON's title_slug is the BOGUS slug. We need the correct
    # one (plus the correct level). Use the rename map for slug correction
    # — and look up the catalog by correct slug for the level. For rows that
    # weren't renamed, the bogus slug is already correct, lookup direct.
    corrected: list[dict] = []
    for r in rows:
        bogus = r["title_slug"]
        correct = renames.get(bogus, bogus)
        cat_row = catalog.get(correct)
        if cat_row is None:
            continue  # truly orphan (not in current LC listing) — skip
        corrected.append({
            "title_slug": correct,
            "frontend_id": cat_row["frontend_id"],
            "level": cat_row["difficulty"] or r.get("level") or "?",
            "title": cat_row["title"],
            "tags": cat_row["tags"],
            "anchor_submission_id": r.get("anchor_submission_id"),
            "p10_ms": r.get("p10_ms"), "p50_ms": r.get("p50_ms"),
            "p95_ms": r.get("p95_ms"), "spread_ms": r.get("spread_ms"),
            "spread_ratio": r.get("spread_ratio"), "n_buckets": r.get("n_buckets"),
            "mode_share": r.get("mode_share"),  # may be None on legacy rows
            "isPaidOnly": cat_row.get("isPaidOnly", False),
        })

    by_lvl: dict[str, list[dict]] = {l: [] for l in LEVELS}
    skipped_premium = 0
    skipped_mode_share = 0
    skipped_mode_share_examples: list[tuple[str, float]] = []
    for r in corrected:
        # Skip premium-only — they can't be benchmarked even with valid signals.
        if r["isPaidOnly"]:
            skipped_premium += 1
            continue
        # Skip mode-share-clipped problems — their fast half is collapsed at
        # LeetCode's resolution floor (a single bucket holds > max_mode_share
        # of submissions). See app.add_mode_share for the rationale.
        ms = r.get("mode_share")
        if ms is not None and ms > max_mode_share * 100:
            skipped_mode_share += 1
            if len(skipped_mode_share_examples) < 5:
                skipped_mode_share_examples.append((r["title_slug"], ms))
            continue
        if r["level"] in by_lvl:
            by_lvl[r["level"]].append(r)

    print(f"\nrebucket summary (post-correction, free problems only):")
    print(f"  skipped premium-only:   {skipped_premium}")
    print(f"  skipped mode-share>{int(max_mode_share*100)}%: {skipped_mode_share}")
    if skipped_mode_share_examples:
        for slug, ms in skipped_mode_share_examples:
            print(f"    e.g. {slug:<55}  mode_share={ms:.1f}%")
    picked: list[dict] = []
    for lvl in LEVELS:
        sorted_rows = sorted(by_lvl[lvl], key=lambda x: -(x["spread_ratio"] or 0))
        top = sorted_rows[:per_level]
        print(f"  {lvl}: pool={len(sorted_rows)}, taking={len(top)}")
        for i, r in enumerate(top[:3], 1):
            print(f"    {i}. {r['title_slug']:<55}  spread_ratio={r['spread_ratio']}×")
        if len(top) > 3:
            print(f"    … +{len(top)-3} more")
        picked.extend(top)

    if dry_run:
        print("(dry-run — sampled_problems.json NOT rewritten)")
        return

    # Write new sampled set, keeping the original columns the seeder expects.
    # We also need parquet columns since seed.py reads `external_id`,
    # `title_slug`, `title`, `level`, `tags`, `content`, `hints`. Pull content
    # + hints from the (now-corrected) parquet by fid.
    df = pd.read_parquet(PARQUET)
    fid_to_row = {int(r["id"]): r for _, r in df.iterrows()}
    seeded: list[dict] = []
    for r in picked:
        df_row = fid_to_row.get(r["frontend_id"])
        if df_row is None:
            continue
        seeded.append({
            "external_id": r["frontend_id"],
            "title_slug": r["title_slug"],
            "title": r["title"] or r["title_slug"].replace("-", " ").title(),
            "level": r["level"],
            "tags": r["tags"],
            "content": df_row["question_content"] or "",
            "hints": None if pd.isna(df_row.get("question_hints"))
                     else df_row.get("question_hints"),
        })
    tmp = SAMPLED_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(seeded, indent=2, ensure_ascii=False))
    tmp.replace(SAMPLED_JSON)
    by = {l: sum(1 for s in seeded if s["level"] == l) for l in LEVELS}
    print(f"\nwrote {SAMPLED_JSON}  ({len(seeded)} problems, by level: {by})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-level", type=int, default=33)
    ap.add_argument("--skip-refresh", action="store_true",
                    help="use the existing premium_status.json instead of "
                         "re-fetching LC catalog (only meaningful when the "
                         "cache already has the extended fields)")
    ap.add_argument("--max-mode-share", type=float, default=0.5,
                    help="drop problems where the dominant histogram bucket "
                         "holds more than this fraction of submissions "
                         "(default 0.5 = 50 percent). Requires "
                         "add_mode_share to have populated the field.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the plan; no files written")
    args = ap.parse_args()

    # 1. catalog
    if args.skip_refresh and PREMIUM_JSON.exists():
        cat = load_catalog()
        # Verify the cache has the extended fields.
        sample = next(iter(cat.values()), {})
        if "difficulty" not in sample:
            print("cache lacks extended fields — refreshing.")
            cat = refresh_catalog()
            write_catalog_atomic(cat) if not args.dry_run else None
    else:
        print("• refreshing LeetCode catalog (with title/difficulty/tags) …")
        cat = refresh_catalog()
        if not args.dry_run:
            write_catalog_atomic(cat)
            print(f"  → wrote {PREMIUM_JSON} ({len(cat)} entries)")

    # 2. parquet
    n_fixed, n_unchanged, renames = fix_parquet(cat, dry_run=args.dry_run)

    # 3. anchors rekey
    if renames:
        rekey_anchors(renames, dry_run=args.dry_run)
    else:
        print("anchors.json: no renames needed.")

    # 4. re-bucket ranking + write new sampled_problems.json
    # `runtime_ranking.json` was authored at original ranking time, before the
    # parquet got corrected — its `title_slug` entries are the legacy bogus
    # slugs. If the current parquet is already clean (e.g. fix_parquet was
    # already run once), the `renames` map above is empty even though
    # runtime_ranking still needs translating. Reconstruct the legacy map
    # from the backup parquet so the re-bucket joins signals to the right
    # problem.
    if not renames:
        legacy = _build_legacy_renames(cat)
        if legacy:
            print(f"\n(rebuilt {len(legacy)} legacy slug renames from backup parquet "
                  f"for runtime_ranking re-bucketing — parquet itself is already clean)")
            renames = legacy
    rebucket_ranking_and_resample(cat, renames, args.per_level,
                                  dry_run=args.dry_run,
                                  max_mode_share=args.max_mode_share)

    if args.dry_run:
        return
    print("\nnext:")
    print("  .venv/bin/python -m app.seed --reset")
    print("  .venv/bin/python -m app.fetch_code_snippets --sampled-only --force")


if __name__ == "__main__":
    main()
