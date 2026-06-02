"""Populate `Problem.code_snippets` from LeetCode's GraphQL.

LeetCode ships a starter-code stub per language for every problem — the exact
class + method signature their judge expects. Without it, models often emit
the right algorithm under the wrong shape (`twoSum` vs `two_sum`, `vector<int>`
vs `List[int]`, …) and fail every test case for a trivial reason.

This script asks LeetCode for `codeSnippets { lang langSlug code }` per problem
and stores them on `Problem.code_snippets` as `{ langSlug: code }`. The
client driver in `webapp/examples/iterative_refine.py` reads them back through
`/api/problems/{slug}` and expands a `{{code_template}}` placeholder so the
model sees the required shape inline in the prompt.

Run:
  # default: every problem in the DB that has no snippets yet, all languages.
  .venv/bin/python -m app.fetch_code_snippets

  # only the sampled set (matches what we benchmark on):
  .venv/bin/python -m app.fetch_code_snippets --sampled-only

  # force re-fetch even if snippets already exist:
  .venv/bin/python -m app.fetch_code_snippets --force

  # subset of languages:
  .venv/bin/python -m app.fetch_code_snippets --langs python3,cpp

Uses the shared credential pool — rotates accounts on 429 automatically.
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
SAMPLED_JSON = REPO_ROOT / "data" / "sampled_problems.json"

_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    titleSlug
    codeSnippets {
      lang
      langSlug
      code
    }
  }
}
"""


def _build_client():
    """Re-create the same shape leetcode_dist uses, with rotation hooks."""
    acc = pick_account()
    if acc is None:
        raise SystemExit(
            "no signed-in LeetCode account available. Refresh cookies via "
            "`python -m app.leetcode_accounts --add NAME --cookie '...'`."
        )
    session, csrf = acc.parsed_cookies()
    c = httpx.Client(
        base_url="https://leetcode.com",
        cookies={"LEETCODE_SESSION": session, "csrftoken": csrf},
        headers={
            "user-agent": "perfarena-code-snippets/1.0",
            "x-csrftoken": csrf,
            "content-type": "application/json",
            "referer": "https://leetcode.com/",
            "origin": "https://leetcode.com",
        },
        timeout=30,
    )
    c._account_name = acc.name  # type: ignore[attr-defined]
    return c


def _fetch_one(client: httpx.Client, slug: str) -> dict[str, str] | None:
    """Return {langSlug: code} or None on transient failure. On 429, marks
    the active account exhausted and the caller can re-build the client."""
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
        snippets = r.json()["data"]["question"]["codeSnippets"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not snippets:
        return None
    return {s["langSlug"]: s["code"] for s in snippets
            if s.get("langSlug") and s.get("code")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sampled-only", action="store_true",
                    help="only fetch for problems in data/sampled_problems.json "
                         "(the curated benchmark set)")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even when code_snippets is already populated")
    ap.add_argument("--langs", default=None,
                    help="comma-separated allowlist (e.g. 'python3,cpp'); "
                         "default: store all languages LeetCode returns")
    ap.add_argument("--rate", type=float, default=2.0,
                    help="requests per second (default 2)")
    args = ap.parse_args()

    lang_allow: set[str] | None = None
    if args.langs:
        lang_allow = {x.strip() for x in args.langs.split(",") if x.strip()}

    # Pick the candidate slug set
    if args.sampled_only:
        if not SAMPLED_JSON.exists():
            raise SystemExit(
                f"{SAMPLED_JSON} not found — run `app.rank_problems_by_runtime` "
                f"first to produce the sampled set."
            )
        sampled = json.loads(SAMPLED_JSON.read_text())
        target_slugs = {p["title_slug"] for p in sampled}
    else:
        target_slugs = None

    with Session(engine) as s:
        rows = s.exec(select(Problem)).all()
    if target_slugs is not None:
        rows = [p for p in rows if p.title_slug in target_slugs]
    if not args.force:
        rows = [p for p in rows if not p.code_snippets]

    if not rows:
        print("nothing to fetch — every targeted problem already has snippets "
              "(re-run with --force to refresh).")
        return

    print(f"will fetch snippets for {len(rows)} problem(s) "
          f"({'sampled only' if args.sampled_only else 'all in DB'}, "
          f"{'force-refresh' if args.force else 'skip-already-populated'})")

    client = _build_client()
    delay = 1.0 / max(args.rate, 0.01)
    success = failed = rotated = 0
    try:
        for i, p in enumerate(rows, 1):
            try:
                snippets = _fetch_one(client, p.title_slug)
            except PermissionError:
                client.close()
                client = _build_client()
                rotated += 1
                print(f"  rotated account; retrying {p.title_slug}", flush=True)
                try:
                    snippets = _fetch_one(client, p.title_slug)
                except PermissionError:
                    snippets = None
                    failed += 1
            if not snippets:
                failed += 1
                if i % 25 == 0 or i == len(rows):
                    print(f"[{i}/{len(rows)}] FAILED {p.title_slug}", flush=True)
                time.sleep(delay)
                continue
            if lang_allow is not None:
                snippets = {k: v for k, v in snippets.items() if k in lang_allow}
            with Session(engine) as s:
                row = s.get(Problem, p.id)
                row.code_snippets = snippets
                s.add(row)
                s.commit()
            success += 1
            if i % 10 == 0 or i == len(rows):
                print(f"[{i}/{len(rows)}] {p.title_slug:<42} "
                      f"langs={len(snippets)}", flush=True)
            time.sleep(delay)
    finally:
        client.close()

    print(f"\ndone — success={success}  failed={failed}  rotated={rotated}")


if __name__ == "__main__":
    main()
