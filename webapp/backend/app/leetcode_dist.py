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
    """Build an httpx client using the credential pool's next available
    account. The pool bootstraps itself from .env on first read, so this
    works for both single-account and multi-account setups."""
    load_dotenv(REPO_ROOT / ".env")
    host = os.getenv("LEETCODE_HOST", "https://leetcode.com")
    # Local import keeps leetcode_dist.py importable in contexts where the
    # accounts module isn't on the path (e.g. some standalone scripts).
    from app.leetcode_accounts import pick_account
    acc = pick_account()
    if acc is None:
        raise AuthMissing(
            "LeetCode auth not configured — set LEETCODE_COOKIE_HEADER "
            "or LEETCODE_SESSION + LEETCODE_CSRF_TOKEN in the repo-root .env, "
            "or seed data/leetcode_accounts.json. All pool accounts may "
            "also be exhausted (run `python -m app.leetcode_accounts`)."
        )
    session, csrf = acc.parsed_cookies()
    client = httpx.Client(
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
    # Tag so callers (or fetch_distribution) can mark exhausted on 429.
    client._account_name = acc.name  # type: ignore[attr-defined]
    return client


def fetch_distribution(submission_id: int, *, client: httpx.Client) -> dict | None:
    """Returns the submissionDetails dict or None when the submission is
    inaccessible / has no histogram (deleted, not yours, judged failed, …).

    On HTTP 429 (daily cap), marks the active account exhausted in the
    credential pool and returns None — the caller can rebuild the client
    via `build_client()` to pick up the next available account.
    """
    try:
        r = client.post("/graphql", json={
            "query": _GRAPHQL, "variables": {"submissionId": int(submission_id)},
        })
    except httpx.HTTPError:
        return None
    if r.status_code == 429:
        name = getattr(client, "_account_name", None)
        if name:
            try:
                from app.leetcode_accounts import mark_exhausted
                mark_exhausted(name)
            except ImportError:
                pass
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
