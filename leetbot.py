"""LeetCode auto-submit bot.

Submits a solution to a LeetCode problem and returns the judge's metadata
(status, runtime, memory, percentiles, testcase counts).

Usage:
    python leetbot.py <problem-slug> <lang> <path-to-solution-file>

Example:
    python leetbot.py two-sum python3 solutions/two_sum.py
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make the webapp's `app.*` package importable regardless of cwd. This
# matters when leetbot is invoked as a subprocess (e.g. by app.dispatch or
# app.seed_anchors_from_parquet) — cwd is then the repo root, not the
# webapp/backend folder, so a bare `from app.leetcode_accounts import …`
# would fail and the credential-pool rotation would silently fall back to
# the .env-only path (single account, no rotation on 429).
_REPO_ROOT = Path(__file__).parent
_BACKEND_DIR = _REPO_ROOT / "webapp" / "backend"
if _BACKEND_DIR.exists() and str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import httpx
from dotenv import load_dotenv


GRAPHQL_QUESTION_ID = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionId
    questionFrontendId
    title
    titleSlug
  }
}
"""

GRAPHQL_USER_STATUS = """
query globalData { userStatus { isSignedIn username } }
"""

GRAPHQL_SUBMISSION_DETAILS = """
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    runtime
    runtimeDisplay
    runtimePercentile
    runtimeDistribution
    memory
    memoryDisplay
    memoryPercentile
    memoryDistribution
    lang { name verboseName }
  }
}
"""


class _RateLimited(RuntimeError):
    """LeetCode returned HTTP 429. Carries the account name (when known) so
    the rotator can mark it exhausted before trying the next account."""
    def __init__(self, account_name: str | None = None):
        super().__init__(
            f"rate limited by LeetCode (429)"
            + (f" on account {account_name!r}" if account_name else "")
        )
        self.account_name = account_name


@dataclass
class LeetCodeClient:
    host: str
    session: str
    csrf: str
    cookies: dict[str, str] | None = None

    def __post_init__(self) -> None:
        cookies = dict(self.cookies or {})
        cookies.setdefault("LEETCODE_SESSION", self.session)
        cookies.setdefault("csrftoken", self.csrf)
        self.client = httpx.Client(
            base_url=self.host,
            cookies=cookies,
            headers={
                "User-Agent": (
                    os.getenv("LEETCODE_USER_AGENT")
                    or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "x-csrftoken": self.csrf,
                "Origin": self.host,
                "Content-Type": "application/json",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    def whoami(self) -> dict[str, Any]:
        r = self.client.post(
            "/graphql",
            json={"query": GRAPHQL_USER_STATUS, "operationName": "globalData", "variables": {}},
            headers={"Referer": f"{self.host}/"},
        )
        r.raise_for_status()
        return r.json()["data"]["userStatus"]

    def question_id(self, slug: str) -> str:
        r = self.client.post(
            "/graphql",
            json={
                "query": GRAPHQL_QUESTION_ID,
                "variables": {"titleSlug": slug},
                "operationName": "questionData",
            },
            headers={"Referer": f"{self.host}/problems/{slug}/"},
        )
        r.raise_for_status()
        data = r.json()["data"]["question"]
        if not data:
            raise ValueError(f"no question found for slug {slug!r}")
        return data["questionId"]

    def submit(self, slug: str, lang: str, code: str) -> int:
        qid = self.question_id(slug)
        r = self.client.post(
            f"/problems/{slug}/submit/",
            json={"lang": lang, "question_id": qid, "typed_code": code},
            headers={"Referer": f"{self.host}/problems/{slug}/"},
        )
        if r.status_code == 429:
            # Signal to the outer rotation loop in `main()` which account got
            # the 429 (so it can mark it exhausted and try the next one). We
            # carry the account name on the client so the rotator can read it.
            account_name = getattr(self, "_account_name", None)
            raise _RateLimited(account_name)
        # LeetCode returns a 302 to the login page (with an empty HTML body)
        # for premium-only problems when the account isn't subscribed. That
        # used to slip past `raise_for_status` (which only flags 4xx/5xx)
        # and then crash inside `r.json()`. Detect explicitly so the caller
        # gets a useful message instead of a stack trace.
        ct = r.headers.get("content-type", "")
        if r.status_code != 200 or "json" not in ct.lower():
            snippet = (r.text or "").strip()[:200]
            raise RuntimeError(
                f"submit failed: HTTP {r.status_code} content-type={ct!r}; "
                f"likely a premium-only problem or expired session "
                f"(body[:200]={snippet!r})"
            )
        body = r.json()
        if "submission_id" not in body:
            raise RuntimeError(f"unexpected submit response: {body!r}")
        return body["submission_id"]

    def poll(self, submission_id: int, *, interval: float = 0.5, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = self.client.get(f"/submissions/detail/{submission_id}/check/")
                if r.status_code == 429:
                    raise _RateLimited(getattr(self, "_account_name", None))
                r.raise_for_status()
                body = r.json()
                if body.get("state") == "SUCCESS":
                    return body
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                # Polling is idempotent — transient network blips are safe to retry.
                print(f"[poll] transient error: {type(e).__name__}; retrying", file=sys.stderr)
            time.sleep(interval + random.uniform(0, 0.2))
        raise TimeoutError(f"submission {submission_id} did not finish in {timeout}s")

    def submit_and_wait(self, slug: str, lang: str, code: str,
                        tle_retries: int = 0,
                        tle_retry_delay: float = 8.0,
                        poll_timeout: float = 180.0) -> dict[str, Any]:
        """Submit once and poll for the verdict.

        Time Limit Exceeded is LeetCode's flakiest verdict — its 2 s wall-clock
        budget is measured on shared judge hardware, so a solution sitting near
        the boundary can TLE on one run and pass on the next purely from host
        load. When `tle_retries > 0`, a TLE result is re-submitted up to that
        many extra times; the FIRST non-TLE verdict wins. If every retry also
        TLEs, the TLE stands. Re-submitting the SAME problem repeatedly is the
        pattern LeetCode tolerates (a human iterating), so the short delay
        between tries is just politeness, not anti-throttle pacing.

        `poll_timeout` is the ceiling for ONE LC roundtrip (post + poll). If
        LeetCode doesn't return a verdict within that window the caller
        sees TimeoutError; the per-repeat loop in `main` catches it and
        synthesises a `Dispatch timeout` run rather than killing the whole
        subprocess.
        """
        result = self.poll(self.submit(slug, lang, code), timeout=poll_timeout)
        for attempt in range(tle_retries):
            if result.get("status_msg") != "Time Limit Exceeded":
                return result
            print(f"[tle-retry] {slug}: Time Limit Exceeded — re-submitting "
                  f"(retry {attempt + 1}/{tle_retries}) after {tle_retry_delay:.0f}s",
                  file=sys.stderr)
            time.sleep(tle_retry_delay)
            result = self.poll(self.submit(slug, lang, code), timeout=poll_timeout)
        return result

    def submission_details(self, submission_id: int) -> dict[str, Any]:
        r = self.client.post(
            "/graphql",
            json={
                "query": GRAPHQL_SUBMISSION_DETAILS,
                "operationName": "submissionDetails",
                "variables": {"submissionId": int(submission_id)},
            },
            headers={"Referer": f"{self.host}/submissions/detail/{submission_id}/"},
        )
        if r.status_code == 429:
            raise _RateLimited(getattr(self, "_account_name", None))
        r.raise_for_status()
        return r.json()["data"]["submissionDetails"]


def _hist_stats(distribution_json: str, *, unit_scale: float = 1.0) -> dict[str, float]:
    """Compute weighted stats from LeetCode's bucket histogram.

    `distribution_json` is the raw string field, shaped like:
        {"lang": "python3", "distribution": [["0", 86.47], ["1", 2.78], ...]}
    Bucket keys are strings: ms for runtime, KB for memory.
    `unit_scale` lets the caller convert (e.g. KB -> MB by passing 1/1024).
    """
    payload = json.loads(distribution_json)
    buckets = [(float(k) * unit_scale, float(pct)) for k, pct in payload["distribution"]]
    total_pct = sum(p for _, p in buckets) or 1.0
    # Normalize so percentages sum to 1 (LeetCode sometimes truncates the tail)
    norm = [(v, p / total_pct) for v, p in buckets]
    mean = sum(v * p for v, p in norm)
    # Approximate median / p95: walk the CDF
    norm_sorted = sorted(norm, key=lambda x: x[0])
    cum = 0.0
    median = p95 = norm_sorted[-1][0]
    for v, p in norm_sorted:
        cum += p
        if cum >= 0.5 and median == norm_sorted[-1][0]:
            median = v
        if cum >= 0.95:
            p95 = v
            break
    return {
        "mean": round(mean, 3),
        "median": round(median, 3),
        "p95": round(p95, 3),
        "buckets_covered_pct": round(total_pct, 2),
    }


def _summary(xs: list[float]) -> dict[str, float] | None:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    mean = statistics.fmean(xs)
    return {
        "n": len(xs),
        "mean": round(mean, 4),
        "stdev": round(statistics.stdev(xs), 4) if len(xs) > 1 else 0.0,
        "min": round(min(xs), 4),
        "max": round(max(xs), 4),
        "range": round(max(xs) - min(xs), 4),
        "cv": round(statistics.stdev(xs) / mean, 4) if len(xs) > 1 and mean else None,
    }


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-submission metrics across repeated runs of the same solution."""
    accepted = [r for r in runs if r.get("status_msg") == "Accepted"]
    return {
        "n_submissions": len(runs),
        "n_accepted": len(accepted),
        "statuses": [r.get("status_msg") for r in runs],
        "runtime_ms": _summary([r["runtime_ms"] for r in accepted if "runtime_ms" in r]),
        "memory_mb": _summary([r["memory_mb"] for r in accepted if "memory_mb" in r]),
        "runtime_percentile": _summary([r["runtime_percentile"] for r in accepted if "runtime_percentile" in r]),
        "memory_percentile": _summary([r["memory_percentile"] for r in accepted if "memory_percentile" in r]),
    }


def compute_stats(details: dict[str, Any]) -> dict[str, Any]:
    runtime = _hist_stats(details["runtimeDistribution"])  # ms
    memory = _hist_stats(details["memoryDistribution"], unit_scale=1 / 1024)  # KB -> MB
    return {
        "lang": details["lang"]["name"],
        "your_runtime_ms": details["runtime"],
        "your_runtime_display": details["runtimeDisplay"],
        "runtime_percentile": details["runtimePercentile"],
        "runtime_avg_ms": runtime["mean"],
        "runtime_median_ms": runtime["median"],
        "runtime_p95_ms": runtime["p95"],
        "runtime_buckets_covered_pct": runtime["buckets_covered_pct"],
        "your_memory_mb": round(details["memory"] / (1024 * 1024), 3),
        "your_memory_display": details["memoryDisplay"],
        "memory_percentile": details["memoryPercentile"],
        "memory_avg_mb": memory["mean"],
        "memory_median_mb": memory["median"],
        "memory_p95_mb": memory["p95"],
        "memory_buckets_covered_pct": memory["buckets_covered_pct"],
    }


def extract_metadata(result: dict[str, Any]) -> dict[str, Any]:
    """Pull the interesting fields out of the raw judge response."""
    keys = (
        "status_msg",
        "status_code",
        "lang",
        "status_runtime",
        "display_runtime",
        "status_memory",
        "memory",
        "runtime_percentile",
        "memory_percentile",
        "total_correct",
        "total_testcases",
        "compare_result",
        "submission_id",
        "question_id",
        "elapsed_time",
        "task_finish_time",
        "finished",
        "code_answer",
        "expected_code_answer",
        "last_testcase",
        "compile_error",
        "runtime_error",
        "full_compile_error",
        "full_runtime_error",
    )
    return {k: result[k] for k in keys if k in result}


def _parse_runtime_ms(result: dict[str, Any]) -> float | None:
    """Best-effort parse of runtime-in-ms from a /check/ response."""
    if isinstance(result.get("display_runtime"), str) and result["display_runtime"].strip():
        try:
            return float(result["display_runtime"])
        except ValueError:
            pass
    sr = result.get("status_runtime")
    if isinstance(sr, str):
        parts = sr.split()
        if parts and parts[0].replace(".", "", 1).isdigit():
            return float(parts[0])
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="Submit a LeetCode solution and print judge metadata.")
    p.add_argument("slug", nargs="?", help="problem title slug, e.g. two-sum")
    p.add_argument("lang", nargs="?", help="language id, e.g. python3, cpp, java, rust")
    p.add_argument("file", nargs="?", type=Path, help="path to source file with solution")
    p.add_argument("--raw", action="store_true", help="print full judge response, not just trimmed metadata")
    p.add_argument("--whoami", action="store_true", help="check auth via GraphQL userStatus and exit")
    p.add_argument("--stats", action="store_true", help="also fetch population avg/median/p95 from the distribution histogram")
    p.add_argument("--stats-for", type=int, metavar="SUBMISSION_ID", help="just fetch stats for an existing submission id and exit")
    p.add_argument("--repeat", type=int, default=1, metavar="N", help="submit the same solution N times and aggregate results (default 1)")
    p.add_argument("--delay", type=float, default=12.0, metavar="SEC", help="seconds between repeats; jittered ±20%% (default 12)")
    p.add_argument("--poll-timeout", type=float, default=180.0, metavar="SEC",
                   help="how long to wait for LeetCode to return a verdict on ONE "
                        "submission. Default 180s. If exceeded, that submission is "
                        "recorded as 'Dispatch timeout' (the next repeats still run "
                        "from scratch).")
    p.add_argument("--tle-retries", type=int, default=3, metavar="N",
                   help="on a Time Limit Exceeded verdict, re-submit up to N more "
                        "times before accepting the TLE (default 3). TLE is "
                        "judge-load-flaky; a borderline solution often passes on "
                        "retry. Applies per repeat.")
    p.add_argument("--tle-retry-delay", type=float, default=8.0, metavar="SEC",
                   help="seconds to wait between TLE re-submissions (default 8)")
    p.add_argument("--log", type=Path, metavar="PATH", help="append each run as a JSON line to this file")
    args = p.parse_args()

    if args.repeat < 1:
        p.error("--repeat must be >= 1")

    load_dotenv()
    host = os.getenv("LEETCODE_HOST", "https://leetcode.com")

    # Credential pool first; falls back to .env transparently for callers that
    # haven't migrated to the multi-account setup. The pool bootstraps itself
    # from .env on first read, so even the single-account case ends up going
    # through the same rotation code path (rotation just has nothing to rotate).
    try:
        from app.leetcode_accounts import (
            pick_account, mark_exhausted, load_accounts as _load_accounts,
            pick_account_by_name,
        )
    except ImportError:
        # leetbot used standalone outside the webapp tree — fall back hard.
        pick_account = mark_exhausted = _load_accounts = None  # type: ignore
        pick_account_by_name = None  # type: ignore

    def _build_client() -> tuple[LeetCodeClient | None, str | None]:
        """Build a client from the next available account. Returns
<<<<<<< HEAD
        (client, account_name) or (None, None) if the pool is dry.

        Honours `LEETCODE_FORCE_ACCOUNT` env var — the dispatcher sets
        this when it's already picked which account should make THIS
        submission (per-account round-robin pacing). If the forced
        account is missing/exhausted/has bad cookies, fall through to
        the default `pick_account()` ordering rather than failing the
        whole submission."""
=======
        (client, account_name) or (None, None) if the pool is dry."""
        def parse_cookie_header(cookie_header: str | None) -> dict[str, str]:
            jar: dict[str, str] = {}
            for piece in (cookie_header or "").split(";"):
                if "=" in piece:
                    k, _, v = piece.partition("=")
                    jar[k.strip()] = v.strip()
            return jar

>>>>>>> 882f453203b4236d51a887082780c2418b4ea30c
        if pick_account is None:
            # Standalone fallback: read .env directly (legacy single-account).
            session = os.getenv("LEETCODE_SESSION")
            csrf = os.getenv("LEETCODE_CSRF_TOKEN")
            ch = os.getenv("LEETCODE_COOKIE_HEADER")
            jar = parse_cookie_header(ch)
            if jar:
                session = jar.get("LEETCODE_SESSION", session)
                csrf = jar.get("csrftoken", csrf)
            if not session or not csrf:
                return None, None
            c = LeetCodeClient(host=host, session=session, csrf=csrf, cookies=jar)
            c._account_name = ".env"  # type: ignore[attr-defined]
            return c, ".env"
        # Dispatcher-pinned account first, if specified and usable.
        forced = os.getenv("LEETCODE_FORCE_ACCOUNT")
        if forced and pick_account_by_name:
            acc = pick_account_by_name(forced)
            if acc and not acc.is_exhausted():
                try:
                    session, csrf = acc.parsed_cookies()
                    c = LeetCodeClient(host=host, session=session, csrf=csrf)
                    c._account_name = acc.name  # type: ignore[attr-defined]
                    return c, acc.name
                except ValueError as e:
                    print(f"warning: forced account {forced!r} unusable: "
                          f"{e} — falling back to pool order",
                          file=sys.stderr)
        acc = pick_account()
        if acc is None:
            return None, None
        try:
            session, csrf = acc.parsed_cookies()
        except ValueError as e:
            print(f"warning: skipping account {acc.name!r}: {e}", file=sys.stderr)
            mark_exhausted(acc.name)
            return _build_client()       # recurse to try the next one
        c = LeetCodeClient(
            host=host,
            session=session,
            csrf=csrf,
            cookies=parse_cookie_header(acc.cookie_header),
        )
        c._account_name = acc.name      # type: ignore[attr-defined]
        return c, acc.name

    client, account_name = _build_client()
    if client is None:
        print("error: no usable LeetCode credentials. Either set "
              "LEETCODE_COOKIE_HEADER (or LEETCODE_SESSION + LEETCODE_CSRF_TOKEN) "
              "in .env, or seed data/leetcode_accounts.json. All pool accounts "
              "may also be exhausted — run "
              "`python -m app.leetcode_accounts` to check.",
              file=sys.stderr)
        return 2
    if account_name and account_name != ".env":
        print(f"[leetbot] using account {account_name!r}", file=sys.stderr)

    def _rotate_on_429(op):
        """Run `op(client)` and on 429 rotate to the next available account.

        CRITICAL — we DO NOT call `mark_exhausted` on a 429 anymore.
        LeetCode 429s have two very different lifetimes:
          * Per-account quota (rare, recovers in ~24h).
          * Per-IP throttle (common, recovers in 1-5 min).
        Conflating them was the cause of "accounts marked exhausted
        immediately even though they aren't" — a per-IP throttle from
        too many recent submissions would burn a 30-minute cooldown on
        a perfectly-good cookie, then the next submit picked the OTHER
        account, also got 429'd, also got cooldown-stamped, and the
        sweep would correctly conclude "pool exhausted, sleep 30 min".

        New behaviour: within THIS one call we track which accounts we
        already tried (`seen` set, in-memory only); we rotate past them
        but never persist a cooldown. If we burn through every account
        in this single call, raise RuntimeError so the dispatch.py
        retry loop kicks in — that one has its own backoff curve (cap
        90s, up to 7 retries) which is exactly the right window for a
        per-IP throttle to clear.
        """
        nonlocal client, account_name
        seen: set[str] = set()
        while True:
            try:
                return op(client)
            except _RateLimited as e:
                name = e.account_name or account_name or "?"
                if name in ("?", ".env"):
                    # Standalone .env mode (single account, nothing to rotate to).
                    raise RuntimeError(
                        "rate limited by LeetCode (429); back off and retry"
                    ) from e
                print(f"[leetbot] account {name!r} hit 429 — rotating to "
                      f"another (NOT marking exhausted — likely IP throttle, "
                      f"not account quota)", file=sys.stderr)
                seen.add(name)
                client, account_name = _build_client()
                if client is None:
                    raise RuntimeError(
                        f"all {len(seen)} account(s) hit 429 in this call; "
                        f"likely per-IP throttle — dispatch.py will back off",
                    ) from e
                if account_name in seen:
                    # _build_client picked an account we already burned this
                    # call — pool drained from our perspective.
                    raise RuntimeError(
                        f"pool drained mid-call (re-picked already-tried "
                        f"account {account_name!r})"
                    ) from e
                print(f"[leetbot] retrying with account {account_name!r}",
                      file=sys.stderr)

    def _validate_session_or_rotate() -> bool:
        """Probe whoami on the active account and rotate past any that aren't
        signed in (expired cookies). Daily-cap rotation is for HTTP 429; this
        is for the OTHER way a pool account goes bad — session lapses.

        Returns True if we end up on a signed-in account, False if the whole
        pool is unusable (caller should exit). Side effect: marks expired
        accounts exhausted in the pool so the next run skips them.
        """
        nonlocal client, account_name
        tried: set[str] = set()
        while True:
            us: dict[str, Any] = {}
            rate_limited_on_whoami = False
            try:
                us = client.whoami()
            except httpx.HTTPStatusError as e:
                # CRITICAL: distinguish 429 (LC frontend throttle —
                # transient, account is fine) from a real auth fail
                # (4xx other than 429, or 5xx). Earlier this branch
                # blindly marked the account exhausted on ANY error,
                # so a single rate-limited whoami call set a 24h
                # cooldown on a perfectly good cookie — and a sweep
                # would chew through every pool account in minutes.
                code = e.response.status_code if e.response is not None else 0
                if code == 429:
                    rate_limited_on_whoami = True
                    print(f"[leetbot] whoami for {account_name!r} got 429; "
                          f"NOT marking exhausted — the cookie is fine, LC's "
                          f"per-IP throttle is just hot", file=sys.stderr)
                else:
                    print(f"[leetbot] whoami HTTP {code} for "
                          f"{account_name!r}: {e}", file=sys.stderr)
                    us = {"isSignedIn": False}
            except (httpx.HTTPError, Exception) as e:  # noqa: BLE001
                # Transport-level error (timeout, connection reset, etc.).
                # Also TRANSIENT — don't burn a 24h cooldown on a network
                # blip. The dispatcher's retry loop will pick up.
                rate_limited_on_whoami = True
                print(f"[leetbot] whoami transport error for "
                      f"{account_name!r}: {e} — treating as transient",
                      file=sys.stderr)
            if rate_limited_on_whoami:
                # Bail out of pre-flight WITHOUT marking exhausted. The
                # outer subprocess invoker (dispatch.py) will see leetbot
                # exit non-zero, classify as rate-limited via stderr, and
                # retry the whole call with its own backoff curve.
                raise _RateLimited(account_name)
            if us.get("isSignedIn"):
                if account_name and account_name != ".env":
                    print(f"[leetbot] account {account_name!r} signed in as "
                          f"{us.get('username') or '?'}", file=sys.stderr)
                return True
            # Account is in pool but its session is dead. Mark exhausted so
            # the pool skips it on the next pick (the user can refresh by
            # `python -m app.leetcode_accounts --add NAME --cookie ...`).
            if mark_exhausted and account_name and account_name not in ("?", ".env"):
                mark_exhausted(account_name)
                print(f"[leetbot] account {account_name!r} not signed in "
                      f"(cookies expired) — marked exhausted; rotating",
                      file=sys.stderr)
            elif account_name == ".env":
                # Standalone single-account mode, nothing to rotate to.
                return False
            tried.add(account_name or "?")
            client, account_name = _build_client()
            if client is None or (account_name or "?") in tried:
                return False
            print(f"[leetbot] trying next account {account_name!r}",
                  file=sys.stderr)

    if args.whoami:
        # Rotate past expired-cookie accounts so the printed result reflects
        # the first signed-in account in the pool, not just whichever one was
        # picked first. Without this, callers like dispatch._check_leetcode_auth
        # would see {"isSignedIn": false} for a stale `alt2` and skip dispatch
        # entirely even when a healthy `alt3` sits behind it.
        if _validate_session_or_rotate():
            print(json.dumps(_rotate_on_429(lambda c: c.whoami()), indent=2))
            return 0
        # Pool is fully dry — emit the not-signed-in JSON the caller expects
        # so the auth pre-flight can fail cleanly with its usual message.
        print(json.dumps({"isSignedIn": False, "username": None}, indent=2))
        return 0

    if args.stats_for is not None:
        details = _rotate_on_429(lambda c: c.submission_details(args.stats_for))
        print(json.dumps(compute_stats(details), indent=2))
        return 0

    if not (args.slug and args.lang and args.file):
        p.error("slug, lang, and file are required unless --whoami / --stats-for is set")

    # Auth-validate before burning a daily-cap submission slot. If every
    # account in the pool has stale cookies this returns False — fail fast
    # with a clear message rather than 0-for-N a thousand slugs.
    if not _validate_session_or_rotate():
        print("error: no signed-in LeetCode account available. "
              "Refresh cookies via `python -m app.leetcode_accounts --add "
              "NAME --cookie '...'` (or update .env) and try again.",
              file=sys.stderr)
        return 2

    code = args.file.read_text()
    runs: list[dict[str, Any]] = []
    last_accepted_sid: int | None = None

    for i in range(args.repeat):
        if i > 0:
            wait = args.delay * (1.0 + random.uniform(-0.2, 0.2))
            print(f"[run {i + 1}/{args.repeat}] sleeping {wait:.1f}s before next submission", file=sys.stderr)
            time.sleep(wait)

        try:
            result = _rotate_on_429(lambda c: c.submit_and_wait(
                args.slug, args.lang, code,
                tle_retries=args.tle_retries, tle_retry_delay=args.tle_retry_delay,
                poll_timeout=args.poll_timeout))
        except TimeoutError as e:
            # LC didn't return a verdict within --poll-timeout. Record THIS
            # repeat as a dispatch timeout and continue — the next repeat
            # starts fresh. Status_msg "Dispatch timeout" is in dispatch.py's
            # SYSTEM_FAILURE_STATUSES, so if EVERY repeat times out the
            # whole attempt gets stamped with dispatch_warning and zero
            # Run rows are written; the sweep auto-retries it.
            print(f"[run {i + 1}/{args.repeat}] poll timeout: {e}", file=sys.stderr)
            result = {
                "submission_id": None,
                "status_msg": "Dispatch timeout",
                "runtime_ms": None,
                "memory": None,
                "runtime_percentile": None,
                "memory_percentile": None,
                "total_correct": None,
                "total_testcases": None,
                "lang": args.lang,
                "error_log": str(e),
            }
        run = {
            "run": i + 1,
            "submission_id": result.get("submission_id"),
            "status_msg": result.get("status_msg"),
            "runtime_ms": _parse_runtime_ms(result),
            "memory_mb": round(result["memory"] / (1024 * 1024), 3) if result.get("memory") else None,
            "runtime_percentile": result.get("runtime_percentile"),
            "memory_percentile": result.get("memory_percentile"),
            "total_correct": result.get("total_correct"),
            "total_testcases": result.get("total_testcases"),
            # The language LeetCode actually GRADED the submission in. Usually
            # matches what we sent in `args.lang`, but LeetCode silently
            # re-classifies submissions whose typed_code looks like a different
            # language (e.g. `public class Solution` ⇒ Java even when lang=python3).
            # The server compares this to the declared submission language and
            # marks mismatches as "Language mismatch" so they don't pollute
            # cross-language baselines.
            "lang": result.get("lang"),
            "pretty_lang": result.get("pretty_lang"),
        }
        # Preserve LeetCode's diagnostic fields on non-Accepted runs so the UI
        # can show *why* it failed (which test, what the traceback was). Only
        # included when the value is non-empty.
        for k in ("compile_error", "full_compile_error",
                  "runtime_error", "full_runtime_error",
                  "last_testcase", "input",
                  "expected_output", "code_output", "std_output"):
            v = result.get(k)
            if v:
                run[k] = v
        # ALSO stash the entire raw /check/ response so the platform can
        # archive whatever LeetCode returned — including fields we don't
        # enumerate above (status_code, state, pretty_lang, task_finish_time,
        # …). The DB persists this verbatim on Run.leetcode_full_response;
        # the UI shows it on non-Accepted runs for unfamiliar error shapes.
        run["leetcode_full_response"] = result
        runs.append(run)
        if run["status_msg"] == "Accepted":
            last_accepted_sid = int(run["submission_id"])

        print(f"[run {i + 1}/{args.repeat}] {json.dumps(run)}", file=sys.stderr)
        if args.log:
            with args.log.open("a") as f:
                f.write(json.dumps(run) + "\n")

        # Early-fail short-circuit: if THIS repeat produced a deterministic
        # failure verdict, the remaining repeats would do the exact same
        # thing — same code, same problem, same LC judge. Skip them to
        # save LC quota + wall time.
        #
        # We DO continue on:
        #   * Accepted             — need all repeats for runtime/memory averaging
        #   * Time Limit Exceeded  — flaky on shared judge hardware; the
        #                            existing --tle-retries handles its
        #                            own re-submission within one repeat
        #                            and other repeats may still pass.
        #   * Dispatch timeout / system-level statuses — caller may want
        #                            to see whether subsequent repeats
        #                            recover from a transient hiccup.
        #
        # We DO bail on:
        #   * Wrong Answer
        #   * Runtime Error
        #   * Compile Error / Memory Limit Exceeded / Output Limit Exceeded
        #     — all deterministic for fixed code; one verdict is plenty.
        DEFINITIVE_FAILURES = {
            "Wrong Answer",
            "Runtime Error",
            "Compile Error",
            "Memory Limit Exceeded",
            "Output Limit Exceeded",
        }
        if run["status_msg"] in DEFINITIVE_FAILURES and i + 1 < args.repeat:
            print(f"[run {i + 1}/{args.repeat}] {run['status_msg']} is "
                  f"deterministic — skipping remaining "
                  f"{args.repeat - i - 1} repeat(s)", file=sys.stderr)
            break

    if args.repeat == 1:
        # Preserve original single-run output shape for backward compatibility
        last = runs[0]
        # We dropped the full result above, so re-fetch the raw if --raw asked
        if args.raw:
            # Re-poll cheaply: we still have the submission_id
            result = _rotate_on_429(lambda c: c.poll(int(last["submission_id"])))
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps(last, indent=2))
    else:
        print(json.dumps({"runs": runs, "aggregate": aggregate_runs(runs)}, indent=2))

    if args.stats and last_accepted_sid is not None:
        details = _rotate_on_429(lambda c: c.submission_details(last_accepted_sid))
        print("\n--- population distribution stats (from last accepted submission) ---")
        print(json.dumps(compute_stats(details), indent=2))

    return 0


if __name__ == "__main__":
    # Top-level _RateLimited catch. `_validate_session_or_rotate` raises
    # _RateLimited when LC's pre-flight whoami returns 429 — we want THAT
    # to bubble out as "this dispatch should be retried by the caller",
    # NOT as an opaque "leetbot exited 1: traceback". The dispatcher's
    # `_is_rate_limited` classifier scans stdout+stderr for the literal
    # "rate limited" / "429", so printing that phrase here is what makes
    # the dispatch.py retry loop kick in. Exit code 1 still — we DON'T
    # want this counted as a clean success — but the textual signal
    # routes the dispatcher into the "back off and retry" path instead
    # of the "give up + stamp Dispatch error" path.
    try:
        sys.exit(main())
    except _RateLimited as e:
        name = getattr(e, "account_name", None) or "(unknown)"
        print(f"[leetbot] rate limited (429) at pre-flight on account "
              f"{name!r}; caller should back off and retry",
              file=sys.stderr)
        sys.exit(1)
