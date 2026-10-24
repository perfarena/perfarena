"""ArenaClient (retries, errors, fallbacks for older servers, host cap),
Settings precedence, and the providers / middleware without a network."""
from __future__ import annotations

import threading
import time

import httpx
import pytest

from perfarena import Arena, ArenaHTTPError, Settings
from perfarena.api.client import ArenaClient
from perfarena.config import Settings as S
from perfarena.errors import ArenaConnectionError, ProviderError
from perfarena.llm import FakeProvider, GenerationRequest
from perfarena.llm.anthropic import AnthropicProvider
from perfarena.llm.base import ProviderConnectionError, http_error
from perfarena.llm.middleware import HardTimeout, RetryOnConnectionError
from perfarena.runs.waiter import AttemptWaiter


def _client(handler, **kw) -> ArenaClient:
    return ArenaClient(Settings(base_url="http://old.test", api_key="k", retries=2, **kw),
                       http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_get_is_retried_post_is_not(monkeypatch):
    monkeypatch.setattr("perfarena.api.client.time.sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(503, text="busy")
    c = _client(handler)
    with pytest.raises(ArenaHTTPError) as e:
        c.get("/api/problems")
    assert e.value.status == 503 and e.value.transient and calls == ["GET"] * 3
    calls.clear()
    with pytest.raises(ArenaHTTPError):
        c.post("/api/submissions", {})
    assert calls == ["POST"]                         # never retried: no duplicates


def test_errors_and_auth_header():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        if req.url.path == "/down":
            raise httpx.ConnectError("refused")
        return httpx.Response(404, json={"detail": "attempt not found"})
    c = _client(handler)
    with pytest.raises(ArenaHTTPError) as e:
        c.get("/api/attempts/1")
    assert e.value.status == 404 and not e.value.transient and seen["auth"] == "Bearer k"
    with pytest.raises(ArenaConnectionError):
        c.request("GET", "/down")


def _old_server(req):
    """A server from before the client-helper endpoints."""
    p = req.url.path
    if p in ("/api/pricing", "/api/submissions/upsert") or p.startswith("/api/attempts/"):
        return httpx.Response(404 if req.method != "PUT" else 405,
                              json={"detail": "Not Found" if req.method != "PUT"
                                    else "Method Not Allowed"})
    if p == "/api/me/submissions":
        return httpx.Response(200, json=[{"id": 7, "model_name": "m", "model_version": "v",
                                          "language": "python3", "n_rounds": 1,
                                          "competition_slug": None, "harness_slug": "h",
                                          "created_at": "2026-10-01"}])
    if p == "/api/submissions/7/attempts":
        return httpx.Response(200, json=[{"attempt_id": 3, "problem_slug": "two-sum",
                                          "status": "Accepted", "accepted": True}])
    return httpx.Response(500, text="unexpected")


def test_fallbacks_for_older_servers():
    a = Arena(_client(_old_server))
    assert a.leaderboard.pricing() == []
    cell = {"model_name": "m", "model_version": "v", "language": "python3",
            "harness_slug": "h", "n_rounds": 1}
    assert a.submissions.upsert({**cell, "create": False})["submission_id"] == 7
    other = {**cell, "language": "cpp", "create": False}
    assert a.submissions.upsert(other) == {"submission_id": None, "created": False,
                                          "status": None}
    st = AttemptWaiter(a.attempts, a.submissions, sleep=lambda s: None).wait(
        3, 5, submission_id=7)
    assert st["state"] == "done" and st["verdict"] == "Accepted"


def test_host_cap_limits_parallel_requests():
    active, peak, lock = [0], [0], threading.Lock()

    def handler(req):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return httpx.Response(200, json=[])
    c = ArenaClient(Settings(base_url="http://public.example", max_concurrency=None),
                    http=httpx.Client(transport=httpx.MockTransport(handler)))
    ts = [threading.Thread(target=c.get, args=("/api/problems",)) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] <= 2                     # public host: two requests at a time


def test_settings_precedence(tmp_path, monkeypatch):
    keyfile = tmp_path / "key"
    keyfile.write_text("file-key\n")
    profiles = {"default": {"base_url": "http://def"},
                "prod": {"base_url": "https://prod.example/", "api_key_file": str(keyfile)}}
    monkeypatch.setenv("ARENA_API_KEY", "shell-key")
    monkeypatch.setenv("ARENA_BASE_URL", "http://shell")
    d = S.resolve(profiles=profiles)
    assert (d.base_url, d.api_key) == ("http://shell", "shell-key")
    p = S.resolve("prod", profiles=profiles)          # a named profile ignores the env
    assert (p.base_url, p.api_key) == ("https://prod.example", "file-key")
    assert p.host_cap == 2 and S(base_url="http://127.0.0.1:8000").host_cap == 8
    assert S.resolve("prod", profiles=profiles, base_url="http://x").base_url == "http://x"
    assert "file-key" not in repr(p)


# ---------- providers + middleware ----------------------------------------------------


def _req(**params) -> GenerationRequest:
    return GenerationRequest(model="claude-x", system_prompt="S", user_prompt="U",
                             params=params, timeout_s=5)


def test_anthropic_mapping(monkeypatch):
    sent = {}

    def post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, body=json, headers=headers)
        return httpx.Response(200, json={
            "id": "msg_1", "model": "claude-x-2026", "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": "plan it"},
                        {"type": "text", "text": "```python\nx = 1\n```"}],
            "usage": {"input_tokens": 11, "output_tokens": 7}},
            request=httpx.Request("POST", url))
    monkeypatch.setattr("perfarena.llm.http.httpx.post", post)
    out = AnthropicProvider("https://api.anthropic.test", "key").complete(
        _req(temperature=0.2, max_tokens=900, thinking={"type": "enabled",
                                                         "budget_tokens": 500}, junk=1))
    assert sent["url"].endswith("/v1/messages")
    assert sent["headers"] == {"x-api-key": "key", "anthropic-version": "2023-06-01"}
    assert sent["body"]["system"] == "S" and sent["body"]["max_tokens"] == 900
    assert "junk" not in sent["body"] and sent["body"]["thinking"]["budget_tokens"] == 500
    assert out.content.startswith("```python") and out.reasoning == "plan it"
    assert out.provider_meta["usage"]["prompt_tokens"] == 11
    assert out.provider_meta["finish_reason"] == "end_turn"


@pytest.mark.parametrize("status, transient", [(400, False), (401, False), (429, True),
                                               (431, True), (500, True), (529, True)])
def test_http_error_classification(status, transient):
    assert http_error("X", status, "body").transient is transient


def test_retry_only_on_connection_errors():
    class Flaky(FakeProvider):
        def __init__(self, errors):
            super().__init__("```python\nok = 1\n```")
            self.errors = list(errors)

        def complete(self, req):
            if self.errors:
                raise self.errors.pop(0)
            return super().complete(req)
    slept = []
    ok = RetryOnConnectionError(Flaky([ProviderConnectionError("reset", provider="f")] * 2),
                                sleep=slept.append)
    assert ok.complete(_req()).content.startswith("```") and slept == [5.0, 10.0]
    http = RetryOnConnectionError(Flaky([ProviderError("HTTP 500", provider="f",
                                                       transient=True)]), sleep=slept.append)
    with pytest.raises(ProviderError):
        http.complete(_req())                    # an HTTP answer is not retried


def test_hard_timeout():
    class Slow(FakeProvider):
        def complete(self, req):
            time.sleep(2)
            return super().complete(req)
    with pytest.raises(ProviderError) as e:
        HardTimeout(Slow("x"), 0.2).complete(_req())
    assert e.value.timeout is True


def test_missing_provider_key_is_clear(monkeypatch):
    from perfarena.llm import create_provider
    from perfarena.spec import ModelSpec
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_provider(ModelSpec(name="gpt-5", provider="openai"))
    with pytest.raises(ValueError, match="unknown provider"):
        create_provider(ModelSpec(name="m", provider="nope"))
