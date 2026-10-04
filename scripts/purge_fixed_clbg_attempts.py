#!/usr/bin/env python3
"""Delete prod attempts for the CLBG (problem, language) cells whose harness,
reference, or statement we fixed, so they can be re-run cleanly.

Scope (delete a CLBG attempt if EITHER holds):
  * language in FIXED_LANGS   -> whole-language harness fixes
        typescript : dropped the unsupported `--noCheck` tsc flag
        csharp     : enabled <AllowUnsafeBlocks>
        php        : rewrote the multithreaded refs + restored deleted sources
  * problem  in FIXED_PROBLEMS -> all-language fixes
        fasta         : statement now gives the exact ALU / LCG / tables
        n-body        : float-tolerance validation (was exact-match)
        spectral-norm : float-tolerance validation
        regex-redux   : statement now lists the 9 patterns + 5 substitutions
        mandelbrot    : measurement N 3500 -> 3504 (multiple of 8)

Fast: pre-filters to the clbg-10 competition (skips every LeetCode submission)
and fetches attempts / issues deletes concurrently. In-flight attempts
(queued / Pending) are left alone.

Usage (key stays in your env, never on disk):
    ARENA_KEY=arena_... python3 scripts/purge_fixed_clbg_attempts.py          # dry run
    ARENA_KEY=arena_... DELETE=1 python3 scripts/purge_fixed_clbg_attempts.py # delete
Optional: ARENA_BASE (default https://perfarena.ngrok.app), WORKERS (default 12).
"""
import json
import os
import sys
import urllib.request
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("ARENA_BASE", "https://perfarena.ngrok.app").rstrip("/")
KEY = os.environ.get("ARENA_KEY", "")
DO_DELETE = os.environ.get("DELETE", "") == "1"
WORKERS = int(os.environ.get("WORKERS", "12"))
COMPETITION = os.environ.get("COMPETITION", "clbg-10")

CLBG = {"binary-trees", "fannkuch-redux", "fasta", "k-nucleotide", "mandelbrot",
        "n-body", "pidigits", "regex-redux", "reverse-complement", "spectral-norm"}
FIXED_LANGS = {"typescript", "csharp", "php"}
FIXED_PROBLEMS = {"fasta", "n-body", "spectral-norm", "regex-redux", "mandelbrot"}
KEEP_STATUS = {"queued", "Pending"}          # never touch in-flight attempts
# By default remove only FAILED ("bad") attempts and keep Accepted measurements.
# Set ONLY_FAILED=0 for a full clean slate (also deletes Accepted runs in the
# fixed cells, e.g. when the workload N changed like mandelbrot).
ONLY_FAILED = os.environ.get("ONLY_FAILED", "1") != "0"

if not KEY:
    sys.exit("ERROR: set ARENA_KEY to your ADMIN API key (arena_...).")


def api(method, path, retries=4):
    import time
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                BASE + path, method=method,
                headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
                return json.loads(body) if body else None
        except (urllib.error.HTTPError, urllib.error.URLError,
                OSError, json.JSONDecodeError) as e:
            last = e
            time.sleep(1.0 * (i + 1))
    raise last


def targeted(lang, slug):
    return slug in CLBG and (lang in FIXED_LANGS or slug in FIXED_PROBLEMS)


def main():
    subs = api("GET", "/api/submissions")
    clbg = [s for s in subs if s.get("competition_slug") == COMPETITION]
    print(f"{len(subs)} submissions total; {len(clbg)} in '{COMPETITION}' "
          f"(delete={DO_DELETE}, workers={WORKERS})\n")

    hits, by_cell, unreadable = [], Counter(), 0

    def fetch(sub):
        sid, lang = sub["id"], sub.get("language", "?")
        try:
            return sid, lang, api("GET", f"/api/submissions/{sid}/attempts")
        except Exception:                                       # noqa: BLE001
            return sid, lang, None

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sid, lang, atts in ex.map(fetch, clbg):
            if atts is None:
                unreadable += 1
                continue
            for a in atts:
                slug, st = a.get("problem_slug"), a.get("status")
                if st in KEEP_STATUS:
                    continue
                if ONLY_FAILED and st == "Accepted":
                    continue
                if targeted(lang, slug):
                    hits.append(a["attempt_id"])
                    by_cell[(slug, lang, st)] += 1

    print("=== targeted (problem, language, status) ===")
    for (slug, lang, st), n in sorted(by_cell.items()):
        print(f"  {slug:20} {lang:11} {st:16} {n:4d}")
    print(f"\n{len(hits)} attempt(s) selected"
          f"{f' ({unreadable} submissions unreadable)' if unreadable else ''}.")

    if not DO_DELETE:
        print("\nDRY RUN — re-run with DELETE=1 to remove them.")
        return

    ok = 0

    def rm(aid):
        try:
            api("DELETE", f"/api/admin/attempts/{aid}")
            return True
        except Exception as e:                                  # noqa: BLE001
            print(f"  ! delete {aid}: {e}", file=sys.stderr)
            return False

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        ok = sum(1 for r in ex.map(rm, hits) if r)
    print(f"\nDELETED {ok}/{len(hits)} attempts.")


if __name__ == "__main__":
    main()
