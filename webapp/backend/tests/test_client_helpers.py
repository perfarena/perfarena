"""Unit tests for client-side helpers in webapp/examples/submit_with_local_model.py.

Currently covers:
  * _ollama_post connection-retry-with-backoff (transient blips like
    ngrok's "Remote end closed connection without response" must retry,
    not abort).
"""
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

EXAMPLES = Path(__file__).parent.parent.parent / "examples"
sys.path.insert(0, str(EXAMPLES))


def test_ollama_post_retries_url_error_then_succeeds():
    """Transient URLError → retries with backoff → eventual success.
    Asserts urlopen is called the expected number of times."""
    # Re-import after env-var setup so the module-level constants reflect
    # test values. Lower retry delay to 0 so the test runs fast.
    os.environ["ARENA_OLLAMA_RETRY_DELAY"] = "0"
    os.environ["ARENA_OLLAMA_RETRIES"] = "3"
    # Drop cached module so the constants reload.
    sys.modules.pop("submit_with_local_model", None)
    import submit_with_local_model as swlm

    # 2 transient failures, then a clean response on the 3rd attempt.
    succeed_resp = MagicMock()
    succeed_resp.read.return_value = b'{"message": {"content": "ok"}}'
    succeed_resp.status = 200
    succeed_resp.__enter__ = lambda s: s
    succeed_resp.__exit__ = lambda s, *a: None

    side_effects = [
        urllib.error.URLError("Remote end closed connection without response"),
        urllib.error.URLError("ngrok tunnel hiccup"),
        succeed_resp,
    ]
    with patch("urllib.request.urlopen", side_effect=side_effects) as mock:
        status, body = swlm._ollama_post(
            "http://example/api/chat", {"x": 1}, timeout=10,
        )
    assert status == 200
    assert body["message"]["content"] == "ok"
    assert mock.call_count == 3, (
        f"expected 3 urlopen calls (2 retries + success); got {mock.call_count}")


def test_ollama_post_gives_up_after_max_retries():
    """Sustained outage: exhaust retries then raise SystemExit with a
    useful diagnostic. The user should see the retry budget mentioned so
    they know to bump ARENA_OLLAMA_RETRIES if their network is genuinely
    slow."""
    os.environ["ARENA_OLLAMA_RETRY_DELAY"] = "0"
    os.environ["ARENA_OLLAMA_RETRIES"] = "2"
    sys.modules.pop("submit_with_local_model", None)
    import submit_with_local_model as swlm

    err = urllib.error.URLError("ngrok dead")
    with patch("urllib.request.urlopen", side_effect=err) as mock:
        try:
            swlm._ollama_post("http://example/api/chat", {"x": 1}, timeout=10)
        except SystemExit as e:
            assert "after 3 attempts" in str(e)         # 2 retries + 1 initial = 3
            assert "ngrok dead" in str(e)
            assert "ARENA_OLLAMA_RETRIES" in str(e)     # mention the override
        else:
            raise AssertionError("expected SystemExit after exhausting retries")
    assert mock.call_count == 3


def test_ollama_post_does_not_retry_http_errors():
    """HTTP errors (4xx/5xx with a body) are protocol responses, NOT
    network failures — they should be returned to the caller immediately
    so call_ollama can do its 'no thinking channel' fallback dance."""
    os.environ["ARENA_OLLAMA_RETRY_DELAY"] = "0"
    os.environ["ARENA_OLLAMA_RETRIES"] = "5"
    sys.modules.pop("submit_with_local_model", None)
    import submit_with_local_model as swlm

    # urllib.HTTPError requires a few positional args; build one with a body
    # containing the 'thinking' error qwen2.5 returns on non-thinking models.
    import io
    err = urllib.error.HTTPError(
        url="http://x", code=400, msg="Bad Request", hdrs={},
        fp=io.BytesIO(b'{"error": "model does not support thinking"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err) as mock:
        status, body = swlm._ollama_post(
            "http://example/api/chat", {"x": 1}, timeout=10,
        )
    assert status == 400
    assert "thinking" in str(body).lower()
    # Single call — HTTP errors mustn't trigger retries.
    assert mock.call_count == 1
