"""Anthropic Messages API (POST /v1/messages). Text blocks are the answer,
thinking blocks the reasoning channel. Pass extended thinking through
model params, e.g. {"thinking": {"type": "enabled", "budget_tokens": 8000},
"max_tokens": 16000}."""
from __future__ import annotations

import os
import time

from .base import Completion, GenerationRequest, Provider, http_error, register_provider
from .http import post_json

API_VERSION = "2023-06-01"
_PASS = ("temperature", "top_p", "top_k", "stop_sequences", "thinking")


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def complete(self, req: GenerationRequest) -> Completion:
        body: dict = {"model": req.model,
                      "max_tokens": int(req.params.get("max_tokens") or 8192),
                      "system": req.system_prompt,
                      "messages": [{"role": "user", "content": req.user_prompt}]}
        body.update({k: req.params[k] for k in _PASS if req.params.get(k) is not None})
        t0 = time.perf_counter()
        status, resp = post_json(self.name, self.base_url + "/v1/messages", body, headers={
            "x-api-key": self.api_key, "anthropic-version": API_VERSION},
            timeout=req.timeout_s)
        ms = int((time.perf_counter() - t0) * 1000)
        if status != 200 or not isinstance(resp, dict):
            raise http_error("Anthropic", status, resp)
        blocks = resp.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        thinking = "\n\n".join(b.get("thinking", "") for b in blocks
                               if b.get("type") == "thinking" and b.get("thinking"))
        usage = dict(resp.get("usage") or {})
        if "input_tokens" in usage:
            usage["prompt_tokens"] = usage["input_tokens"]
        if "output_tokens" in usage:
            usage["completion_tokens"] = usage["output_tokens"]
        return Completion(content=text, reasoning=thinking or None, wall_clock_ms=ms,
                          endpoint=self.base_url,
                          provider_meta={"usage": usage, "finish_reason": resp.get("stop_reason"),
                                         "model_resolved": resp.get("model"),
                                         "id": resp.get("id"), "api": "messages"})


@register_provider("anthropic")
def _factory(spec) -> AnthropicProvider:
    key = os.environ.get(spec.api_key_env or "ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(f"provider anthropic needs an API key in "
                         f"${spec.api_key_env or 'ANTHROPIC_API_KEY'}")
    return AnthropicProvider(spec.endpoint or os.environ.get(
        "ANTHROPIC_BASE_URL", "https://api.anthropic.com"), key)
