#!/usr/bin/env python3
"""Delete every FAILED attempt for CLBG problems on the deployed platform.

A "failed" attempt is one with a terminal non-Accepted verdict: Wrong Answer,
Runtime Error, Compile Error, Time Limit Exceeded, or a dispatch failure.
In-flight attempts (queued / Pending) and Accepted attempts are never touched.

Requires the ADMIN API key. The harness-runner token is rejected with 401 by
/api/admin/attempts. The key stays in the environment and never on disk.

Usage (dry run prints what would be deleted, changes nothing):
    ARENA_KEY=arena_... python3 scripts/purge_failed_clbg_attempts.py
Delete for real:
    ARENA_KEY=arena_... DELETE=1 python3 scripts/purge_failed_clbg_attempts.py
Optional:
    ARENA_BASE   (default https://perfarena.ngrok.app)
    COMPETITION  (default clbg-10)
    WORKERS      (default 12)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("ARENA_BASE", "https://perfarena.ngrok.app").rstrip("/")
KEY = os.environ.get("ARENA_KEY", "")
DO_DELETE = os.environ.get("DELETE", "") == "1"
WORKERS = int(os.environ.get("WORKERS", "12"))
COMPETITION = os.environ.get("COMPETITION", "clbg-10")

# Never delete an in-flight or accepted attempt. Everything else is a failure.
KEEP_STATUS = {"queued", "Pending", "Accepted"}

if not KEY:
    sys.exit("ERROR: set ARENA_KEY to your ADMIN API key (arena_...).")


def api(method, path, retries=4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                BASE + path, method=method,
                headers={"Authorization": f"Bearer {KEY}",
                         "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"ERROR: {e.code} from {path}. Use the ADMIN key; the "
                         f"harness-runner token cannot delete attempts.")
            last = e
            time.sleep(1.0 * (i + 1))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            last = e
            time.sleep(1.0 * (i + 1))
    raise last


def main():
    subs = api("GET", "/api/submissions")
    clbg = [s for s in subs if s.get("competition_slug") == COMPETITION]
    print(f"{len(subs)} submissions total; {len(clbg)} in '{COMPETITION}' "
          f"(delete={DO_DELETE}, workers={WORKERS})\n")

    hits, by_cell, unreadable = [], Counter(), 0

    def fetch(sub):
        try:
            return sub.get("language", "?"), api(
                "GET", f"/api/submissions/{sub['id']}/attempts")
        except Exception:                                       # noqa: BLE001
            return sub.get("language", "?"), None

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for lang, atts in ex.map(fetch, clbg):
            if atts is None:
                unreadable += 1
                continue
            for a in atts:
                st = a.get("status")
                if st in KEEP_STATUS:
                    continue
                hits.append(a["attempt_id"])
                by_cell[(a.get("problem_slug"), lang, st)] += 1

    print("=== failing (problem, language, status) ===")
    for (slug, lang, st), n in sorted(by_cell.items()):
        print(f"  {str(slug):20} {str(lang):11} {str(st):18} {n:4d}")
    print(f"\n{len(hits)} failing attempt(s) selected"
          f"{f' ({unreadable} submissions unreadable)' if unreadable else ''}.")

    if not DO_DELETE:
        print("\nDRY RUN. Re-run with DELETE=1 to remove them.")
        return

    def rm(aid):
        try:
            api("DELETE", f"/api/admin/attempts/{aid}")
            return True
        except Exception as e:                                  # noqa: BLE001
            print(f"  ! delete {aid}: {e}", file=sys.stderr)
            return False

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        ok = sum(1 for r in ex.map(rm, hits) if r)
    print(f"\nDELETED {ok}/{len(hits)} failing attempts.")


if __name__ == "__main__":
    main()
