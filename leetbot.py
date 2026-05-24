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


@dataclass
class LeetCodeClient:
    host: str
    session: str
    csrf: str

    def __post_init__(self) -> None:
        self.client = httpx.Client(
            base_url=self.host,
            cookies={"LEETCODE_SESSION": self.session, "csrftoken": self.csrf},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
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
            raise RuntimeError("rate limited by LeetCode (429); back off and retry")
        r.raise_for_status()
        body = r.json()
        if "submission_id" not in body:
            raise RuntimeError(f"unexpected submit response: {body!r}")
        return body["submission_id"]

    def poll(self, submission_id: int, *, interval: float = 0.5, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = self.client.get(f"/submissions/detail/{submission_id}/check/")
                r.raise_for_status()
                body = r.json()
                if body.get("state") == "SUCCESS":
                    return body
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                # Polling is idempotent — transient network blips are safe to retry.
                print(f"[poll] transient error: {type(e).__name__}; retrying", file=sys.stderr)
            time.sleep(interval + random.uniform(0, 0.2))
        raise TimeoutError(f"submission {submission_id} did not finish in {timeout}s")

    def submit_and_wait(self, slug: str, lang: str, code: str) -> dict[str, Any]:
        sid = self.submit(slug, lang, code)
        return self.poll(sid)

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
    p.add_argument("--log", type=Path, metavar="PATH", help="append each run as a JSON line to this file")
    args = p.parse_args()

    if args.repeat < 1:
        p.error("--repeat must be >= 1")

    load_dotenv()
    host = os.getenv("LEETCODE_HOST", "https://leetcode.com")
    cookie_header = os.getenv("LEETCODE_COOKIE_HEADER")
    if cookie_header:
        # Parse "name=value; name=value; ..." into our two fields
        jar = {}
        for piece in cookie_header.split(";"):
            piece = piece.strip()
            if "=" in piece:
                k, _, v = piece.partition("=")
                jar[k.strip()] = v.strip()
        session = jar.get("LEETCODE_SESSION")
        csrf = jar.get("csrftoken")
        if not session or not csrf:
            print("error: LEETCODE_COOKIE_HEADER is missing LEETCODE_SESSION or csrftoken", file=sys.stderr)
            return 2
    else:
        session = os.getenv("LEETCODE_SESSION")
        csrf = os.getenv("LEETCODE_CSRF_TOKEN")
        if not session or not csrf:
            print("error: set LEETCODE_SESSION and LEETCODE_CSRF_TOKEN in .env (or LEETCODE_COOKIE_HEADER)", file=sys.stderr)
            return 2

    client = LeetCodeClient(host=host, session=session, csrf=csrf)

    if args.whoami:
        print(json.dumps(client.whoami(), indent=2))
        return 0

    if args.stats_for is not None:
        details = client.submission_details(args.stats_for)
        print(json.dumps(compute_stats(details), indent=2))
        return 0

    if not (args.slug and args.lang and args.file):
        p.error("slug, lang, and file are required unless --whoami / --stats-for is set")

    code = args.file.read_text()
    runs: list[dict[str, Any]] = []
    last_accepted_sid: int | None = None

    for i in range(args.repeat):
        if i > 0:
            wait = args.delay * (1.0 + random.uniform(-0.2, 0.2))
            print(f"[run {i + 1}/{args.repeat}] sleeping {wait:.1f}s before next submission", file=sys.stderr)
            time.sleep(wait)

        result = client.submit_and_wait(args.slug, args.lang, code)
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
        runs.append(run)
        if run["status_msg"] == "Accepted":
            last_accepted_sid = int(run["submission_id"])

        print(f"[run {i + 1}/{args.repeat}] {json.dumps(run)}", file=sys.stderr)
        if args.log:
            with args.log.open("a") as f:
                f.write(json.dumps(run) + "\n")

    if args.repeat == 1:
        # Preserve original single-run output shape for backward compatibility
        last = runs[0]
        # We dropped the full result above, so re-fetch the raw if --raw asked
        if args.raw:
            # Re-poll cheaply: we still have the submission_id
            result = client.poll(int(last["submission_id"]))
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps(last, indent=2))
    else:
        print(json.dumps({"runs": runs, "aggregate": aggregate_runs(runs)}, indent=2))

    if args.stats and last_accepted_sid is not None:
        details = client.submission_details(last_accepted_sid)
        print("\n--- population distribution stats (from last accepted submission) ---")
        print(json.dumps(compute_stats(details), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
