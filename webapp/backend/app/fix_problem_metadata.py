"""Repair `Problem` rows whose title_slug / title / level / tags drifted.

Why this exists
---------------
The source parquet `data/problems.parquet` has 46% of rows where the
`title_slug`, `level`, and `tag` columns are paired with the WRONG row's
`id` + `question_content`. So a Problem inserted via app.seed ends up
labelled e.g. `design-hashset` (Easy, "Hash Table") but its content is
actually "Ambiguous Coordinates" (Medium, Stack). The mismatch is a
display bug only — id, content, and the LeetCode submissionDistribution
anchored against that id were all correctly paired — but every UI surface
shows misleading labels.

What this script does
---------------------
For every Problem in the DB:

  1. Take the `external_id` (= LeetCode questionFrontendId) as the anchor.
  2. Look up the CURRENT LeetCode slug via the cached problemset metadata
     in `data/premium_status.json` (built by seed_anchors_from_parquet).
  3. If the cached slug doesn't match the DB row, fetch live metadata
     (title, difficulty, topicTags) via GraphQL and update the row in
     place. Content is left alone — it's the one field that WAS paired
     correctly with `id`.
  4. Clear `code_snippets` since those were fetched against the wrong
     slug — they'd embed the wrong starter shape into model prompts.
     A subsequent `app.fetch_code_snippets --sampled-only --force` repopulates.

Idempotent: rows already correct are skipped. Rate-limited via the
shared credential pool; rotates on 429.

Run:
  .venv/bin/python -m app.fix_problem_metadata
  .venv/bin/python -m app.fix_problem_metadata --dry-run        # preview, no writes
  .venv/bin/python -m app.fix_problem_metadata --slug NAME      # one row only
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
from sqlmodel import Session, select

from .db import engine
from .leetcode_accounts import mark_exhausted, pick_account
from .models import Problem


REPO_ROOT = Path(__file__).parent.parent.parent.parent
PREMIUM_JSON = REPO_ROOT / "data" / "premium_status.json"

_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionFrontendId
    title
    titleSlug
    difficulty
    topicTags { name }
  }
}
"""


def _build_client() -> httpx.Client:
    acc = pick_account()
    if acc is None:
        raise SystemExit("no signed-in LeetCode account in pool")
    session, csrf = acc.parsed_cookies()
    c = httpx.Client(
        base_url="https://leetcode.com",
        cookies={"LEETCODE_SESSION": session, "csrftoken": csrf},
        headers={
            "user-agent": "perfarena-metadata-fix/1.0",
            "x-csrftoken": csrf,
            "content-type": "application/json",
            "referer": "https://leetcode.com/",
            "origin": "https://leetcode.com",
        },
        timeout=30,
    )
    c._account_name = acc.name  # type: ignore[attr-defined]
    return c


def _fetch_meta(client: httpx.Client, slug: str) -> dict | None:
    """Return {title, slug, level, tags} for the given slug, or None on
    transient failure. On 429, marks the account exhausted and raises."""
    try:
        r = client.post("/graphql", json={
            "query": _QUERY, "variables": {"titleSlug": slug},
            "operationName": "questionData",
        })
    except httpx.HTTPError:
        return None
    if r.status_code == 429:
        name = getattr(client, "_account_name", None)
        if name:
            mark_exhausted(name)
        raise PermissionError("429 — rotate account")
    if r.status_code != 200:
        return None
    try:
        q = r.json()["data"]["question"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not q:
        return None
    return {
        "title": q.get("title") or "",
        "slug": q.get("titleSlug") or "",
        "level": q.get("difficulty") or "",
        "tags": ",".join(t["name"] for t in (q.get("topicTags") or []) if t.get("name")),
    }


def _load_premium_cache() -> dict[int, str]:
    """frontend_id → current slug, from the cache built by seed_anchors_from_parquet."""
    if not PREMIUM_JSON.exists():
        return {}
    try:
        data = json.loads(PREMIUM_JSON.read_text())
    except json.JSONDecodeError:
        return {}
    return {int(m["frontend_id"]): cs for cs, m in data.items() if m.get("frontend_id")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change, don't write")
    ap.add_argument("--slug", default=None,
                    help="only fix this one row (helpful for testing)")
    ap.add_argument("--rate", type=float, default=2.0,
                    help="GraphQL requests per second (default 2)")
    args = ap.parse_args()

    fid_to_real_slug = _load_premium_cache()
    if not fid_to_real_slug:
        raise SystemExit(
            f"{PREMIUM_JSON} missing or empty — run "
            f"`python -m app.seed_anchors_from_parquet --dry-run` once to "
            f"populate the LeetCode-truth slug cache, then re-run me."
        )

    with Session(engine) as s:
        q = select(Problem)
        if args.slug:
            q = q.where(Problem.title_slug == args.slug)
        rows = s.exec(q).all()

    drifted: list[Problem] = []
    for p in rows:
        real_slug = fid_to_real_slug.get(p.external_id)
        if real_slug is None or real_slug == p.title_slug:
            continue
        drifted.append(p)

    print(f"scanned {len(rows)} problem(s); {len(drifted)} have a drifted slug")
    if not drifted:
        return

    client = _build_client()
    delay = 1.0 / max(args.rate, 0.01)
    fixed = 0
    failed = 0
    try:
        for i, p in enumerate(drifted, 1):
            real_slug = fid_to_real_slug[p.external_id]
            try:
                meta = _fetch_meta(client, real_slug)
            except PermissionError:
                client.close()
                client = _build_client()
                print("  rotated account on 429", flush=True)
                try:
                    meta = _fetch_meta(client, real_slug)
                except PermissionError:
                    meta = None
                    failed += 1
            if not meta:
                failed += 1
                print(f"[{i}/{len(drifted)}] FAILED  fid={p.external_id}  "
                      f"target_slug={real_slug}", flush=True)
                time.sleep(delay)
                continue

            print(f"[{i}/{len(drifted)}] fix fid={p.external_id}  "
                  f"{p.title_slug!r} → {meta['slug']!r}  "
                  f"(level {p.level} → {meta['level']})", flush=True)
            if args.dry_run:
                fixed += 1
                time.sleep(delay)
                continue
            # Atomic in-place update of the misaligned columns. Content +
            # external_id are left alone (already paired correctly with id).
            # code_snippets is wiped because the cached snippets were
            # fetched against the wrong slug — they're for the WRONG
            # problem and would mislead model prompts. Re-fetch via
            # `app.fetch_code_snippets --sampled-only --force` after.
            with Session(engine) as s:
                row = s.get(Problem, p.id)
                row.title_slug = meta["slug"]
                row.title = meta["title"]
                row.level = meta["level"]
                row.tags = meta["tags"]
                row.code_snippets = None
                s.add(row)
                s.commit()
            fixed += 1
            time.sleep(delay)
    finally:
        client.close()

    print(f"\ndone — fixed={fixed} (dry_run={args.dry_run})  failed={failed}")
    if fixed and not args.dry_run:
        print("next: python -m app.fetch_code_snippets --sampled-only --force")


if __name__ == "__main__":
    main()
