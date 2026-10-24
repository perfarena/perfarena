"""Local Ollama (native /api/chat, think on). Same wire behavior and trace
metadata as the scripts' OllamaProvider."""
from __future__ import annotations

import json
import os
import time

from ..codegen import think_reasoning
from .base import Completion, GenerationRequest, Provider, http_error, register_provider
from .http import get_json, post_json

_META_KEYS = ("total_duration", "load_duration", "prompt_eval_count",
              "prompt_eval_duration", "eval_count", "eval_duration", "done_reason")


class OllamaProvider(Provider):
    name = "ollama"
    local = True

    def __init__(self, url: str, think: bool = True):
        self.url = url.rstrip("/")
        self.think = think

    def complete(self, req: GenerationRequest) -> Completion:
        payload = {
            "model": req.model,
            "messages": [{"role": "system", "content": req.system_prompt},
                         {"role": "user", "content": req.user_prompt}],
            "think": self.think, "stream": False, "options": req.params,
        }
        endpoint = self.url + "/api/chat"
        t0 = time.perf_counter()
        status, resp = post_json(self.name, endpoint, payload, timeout=req.timeout_s)
        if status == 400 and "thinking" in json.dumps(resp).lower():
            # A model without a thinking channel rejects `think`: retry once.
            payload.pop("think", None)
            status, resp = post_json(self.name, endpoint, payload, timeout=req.timeout_s)
        ms = int((time.perf_counter() - t0) * 1000)
        if status != 200 or not isinstance(resp, dict):
            raise http_error("Ollama", status, resp)
        msg = resp.get("message", {})
        content = msg.get("content", "") or ""
        return Completion(
            content=content,
            reasoning=msg.get("thinking") or think_reasoning(content),
            provider_meta={k: resp[k] for k in _META_KEYS if k in resp},
            wall_clock_ms=ms, endpoint=self.url)

    def model_digest(self, model: str) -> str | None:
        tags = get_json(self.url + "/api/tags")
        for m in (tags or {}).get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return f"ollama:{m['digest']}" if m.get("digest") else None
        return None


@register_provider("ollama")
def _factory(spec) -> OllamaProvider:
    return OllamaProvider(spec.endpoint or os.environ.get("OLLAMA_URL", "http://localhost:11434"),
                          think=spec.think)
