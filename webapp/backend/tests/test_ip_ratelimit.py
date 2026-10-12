"""Per-IP GET rate limiter — runs only against UNAUTHENTICATED reads.

The conftest disables the limiter for the rest of the suite (the suite makes
far more anonymous reads than its production cap would tolerate). This file
flips it on with a tiny cap and walks through the contract:

  * anonymous bursts hit 429 with Retry-After once the cap is exceeded
  * an Authorization header skips the limit
  * exempt paths (captcha) never count
  * resetting the state clears the cooldown
"""
import pytest

from conftest import ADMIN, HARNESS
from app import ip_ratelimit


@pytest.fixture(autouse=True)
def _tiny_window():
    """Per-test: cap = 3 GETs / 60s, then restore the test-suite default
    (disabled) so other files keep running with the global no-limit state."""
    ip_ratelimit.configure(enabled=True, per_min=3, window_s=60.0)
    ip_ratelimit.reset()
    yield
    ip_ratelimit.configure(enabled=False)
    ip_ratelimit.reset()


def test_anonymous_burst_hits_429(client):
    """The first `per_min` reads succeed; the next one is refused with
    429 and a Retry-After header pointing at the oldest in-window hit."""
    for _ in range(3):
        r = client.get("/api/problems")
        assert r.status_code == 200, r.text
    blocked = client.get("/api/problems")
    assert blocked.status_code == 429
    body = blocked.json()
    assert "rate limit" in body["detail"].lower()
    assert "retry-after" in {k.lower() for k in blocked.headers.keys()}


def test_authorization_header_bypasses_limit(client):
    """The whole point of the auth-bypass: signed-in admins doing dashboard
    work shouldn't hit a wall just because they're polling endpoints."""
    # Burn through the cap anonymously first so we know we're at the edge.
    for _ in range(3):
        client.get("/api/problems")
    # Same IP, but now with credentials → no 429.
    r = client.get("/api/problems", headers=ADMIN)
    assert r.status_code == 200


def test_authenticated_traffic_does_not_consume_anonymous_budget(client):
    """Authenticated GETs are completely off the counter — they shouldn't
    even nudge the anonymous quota toward the ceiling."""
    for _ in range(10):
        r = client.get("/api/problems", headers=ADMIN)
        assert r.status_code == 200
    # The full 3-request anon budget should still be intact.
    for _ in range(3):
        r = client.get("/api/problems")
        assert r.status_code == 200
    assert client.get("/api/problems").status_code == 429


def test_exempt_paths_never_count(client):
    """`/api/captcha` is exempt because it's part of the sign-in flow —
    throttling it would lock anonymous users out of the registration page."""
    # Hammer captcha well past the anonymous cap.
    for _ in range(10):
        assert client.get("/api/captcha").status_code == 200
    # The non-exempt budget should still be untouched.
    for _ in range(3):
        assert client.get("/api/problems").status_code == 200


def test_non_get_requests_are_not_rate_limited(client):
    """Writes have their own per-user limits (submission cooldown / daily
    cap). The IP-GET limiter must not double-count them."""
    body = {
        "harness_slug": HARNESS,
        "model_name": "rl-write", "model_version": "v1",
        "language": "python3", "prompt_template": "x" * 30,
        "model_params": {"temperature": 0.0},
        "attempts": [{"title_slug": "two-sum", "code": "class Solution:\n    def twoSum(self, nums, target):\n        seen = {}\n        for i, n in enumerate(nums):\n            if target - n in seen:\n                return [seen[target - n], i]\n            seen[n] = i\n", "inference_trace": "uses an O(n) hash-map seen-pass"}],
    }
    # Multiple write requests in quick succession — the GET budget shouldn't
    # tick down at all.
    for _ in range(5):
        r = client.post("/api/submissions", headers=ADMIN, json=body)
        assert r.status_code == 202
    # All three anon GETs should still be available.
    for _ in range(3):
        assert client.get("/api/problems").status_code == 200


def test_snapshot_reports_in_window_counts(client):
    """The diagnostics snapshot reflects what the limiter sees. Useful for
    a /metrics-style endpoint later, and now for asserting the counter
    actually moved when we expected it to."""
    assert ip_ratelimit.snapshot() == {}
    client.get("/api/problems")
    snap = ip_ratelimit.snapshot()
    # Single client = one IP key, count = 1.
    assert sum(snap.values()) == 1
