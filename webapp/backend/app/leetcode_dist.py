"""Shared LeetCode submissionDistribution helpers.

The histogram math + GraphQL client used by:
  * `app.pull_baselines` (CLI: bulk pull for a fixed slug/lang list)
  * `app.dispatch`       (auto-refresh after every real LeetCode submission)

The bucket math MUST stay byte-identical to `pull_baselines.derive` —
downstream rankings compare numbers between them.
"""
from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

import httpx
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).parent.parent.parent.parent

_GRAPHQL = """
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    runtimeDistribution
    memoryDistribution
    lang { name verboseName }
  }
}
"""


class AuthMissing(RuntimeError):
    """Raised when .env has no LeetCode cookies — caller decides whether to
    skip silently (auto-refresh) or hard-fail (manual pull)."""


def build_client() -> httpx.Client:
    load_dotenv(REPO_ROOT / ".env")
    host = os.getenv("LEETCODE_HOST", "https://leetcode.com")
    cookie_header = os.getenv("LEETCODE_COOKIE_HEADER")
    if cookie_header:
        jar: dict[str, str] = {}
        for piece in cookie_header.split(";"):
            if "=" in piece:
                k, v = piece.strip().split("=", 1)
                jar[k] = v
        session, csrf = jar.get("LEETCODE_SESSION"), jar.get("csrftoken")
    else:
        session = os.getenv("LEETCODE_SESSION")
        csrf = os.getenv("LEETCODE_CSRF_TOKEN")
    if not session or not csrf:
        raise AuthMissing(
            "LeetCode auth not configured — set LEETCODE_COOKIE_HEADER "
            "or LEETCODE_SESSION + LEETCODE_CSRF_TOKEN in the repo-root .env"
        )
    return httpx.Client(
        base_url=host,
        cookies={"LEETCODE_SESSION": session, "csrftoken": csrf},
        headers={
            "referer": host + "/",
            "x-csrftoken": csrf,
            "content-type": "application/json",
            "user-agent": "perfarena-baseline-fetcher/1.0",
        },
        timeout=30,
    )


def fetch_distribution(submission_id: int, *, client: httpx.Client) -> dict | None:
    """Returns the submissionDetails dict or None when the submission is
    inaccessible / has no histogram (deleted, not yours, judged failed, …)."""
    try:
        r = client.post("/graphql", json={
            "query": _GRAPHQL, "variables": {"submissionId": int(submission_id)},
        })
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    data = r.json().get("data", {}).get("submissionDetails")
    if not data or not data.get("runtimeDistribution"):
        return None
    return data


# ---- bucket math (must match pull_baselines.derive byte-for-byte) ----------

def _mean(buckets: list[tuple[float, float]]) -> float:
    total = sum(p for _, p in buckets) or 1.0
    return sum(v * p for v, p in buckets) / total


def _percentile_lo(buckets: list[tuple[float, float]], pct: float) -> float:
    sorted_b = sorted(buckets, key=lambda x: x[0])
    cum = 0.0
    for v, p in sorted_b:
        cum += p
        if cum >= pct:
            return v
    return sorted_b[-1][0] if sorted_b else 0.0


def _detect_memory_unit(raw: list[tuple[str, float]]) -> tuple[float, str]:
    """LeetCode reports memory in KB for most languages and (rarely) already
    in MB. Median > 1024 ⇒ KB; else if > 1 ⇒ MB; tiny values ⇒ KB."""
    vals = [float(k) for k, _ in raw]
    if not vals:
        return 1.0, "unknown"
    median = statistics.median(vals)
    if median > 1024:
        return 1 / 1024, "KB"
    if median > 1:
        return 1.0, "MB"
    return 1 / 1024, "KB"


def derive(runtime_dist_json: str, memory_dist_json: str) -> dict:
    rt = json.loads(runtime_dist_json)
    mem = json.loads(memory_dist_json)
    rt_buckets = [(float(k), float(p)) for k, p in rt["distribution"]]
    mem_scale, mem_unit = _detect_memory_unit(mem["distribution"])
    mem_buckets = [(float(k) * mem_scale, float(p)) for k, p in mem["distribution"]]
    return {
        "runtime_ms_avg": round(_mean(rt_buckets), 3),
        "runtime_ms_expert": round(_percentile_lo(rt_buckets, 10.0), 3),
        "memory_mb_avg": round(_mean(mem_buckets), 4),
        "memory_mb_expert": round(_percentile_lo(mem_buckets, 10.0), 4),
        "_meta": {
            "lang_reported": rt.get("lang"),
            "memory_unit_detected": mem_unit,
            "runtime_buckets": len(rt_buckets),
            "memory_buckets": len(mem_buckets),
        },
    }
