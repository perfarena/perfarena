"""OpenAI and OpenAI-compatible endpoints (Azure, OpenRouter, vLLM, ...).
Reasoning models (gpt-5 family, o-series) go through /v1/responses, the rest
through /v1/chat/completions, as in the scripts' OpenAIProvider."""
from __future__ import annotations

import os
import re
import time

from ..errors import ProviderError
from ..codegen import think_reasoning
from .base import Completion, GenerationRequest, Provider, http_error, register_provider
from .http import post_json

# Parameters sent on /chat/completions; others stay in the trace only.
_CHAT_PARAMS = ("temperature", "top_p", "max_tokens", "max_completion_tokens",
                "frequency_penalty", "presence_penalty", "seed", "stop",
                "reasoning_effort")


def is_reasoning_model(model: str) -> bool:
    """OpenAI SKUs that reject a custom temperature / top_p: the gpt-5
    family (not gpt-5-chat) and the o-series."""
    m = (model or "").lower().strip()
    if m.startswith("gpt-5-chat") or m.startswith("gpt-5.0-chat"):
        return False
    return m.startswith("gpt-5") or bool(re.match(r"^o[1-9](-|$)", m))


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict:
        return {"authorization": f"Bearer {self.api_key}"}

    def complete(self, req: GenerationRequest) -> Completion:
        if is_reasoning_model(req.model):
            return self._responses(req)
        body: dict = {"model": req.model, "messages": [
            {"role": "system", "content": req.system_prompt},
            {"role": "user", "content": req.user_prompt}]}
        body.update({k: req.params[k] for k in _CHAT_PARAMS
                     if req.params.get(k) is not None})
        t0 = time.perf_counter()
        status, resp = post_json(self.name, self.base_url + "/chat/completions", body,
                                 headers=self._headers(), timeout=req.timeout_s)
        ms = int((time.perf_counter() - t0) * 1000)
        if status != 200 or not isinstance(resp, dict):
            raise http_error("OpenAI", status, resp)
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        reasoning = (msg.get("reasoning_content") or msg.get("reasoning")
                     or think_reasoning(content))
        return Completion(content=content, reasoning=reasoning, wall_clock_ms=ms,
                          endpoint=self.base_url,
                          provider_meta=self._meta(resp, choice.get("finish_reason"),
                                                   "chat_completions"))

    def _responses(self, req: GenerationRequest) -> Completion:
        """Reasoning models through /v1/responses, which returns the
        reasoning summary that /chat/completions hides."""
        body: dict = {"model": req.model, "input": [
            {"role": "system", "content": req.system_prompt},
            {"role": "user", "content": req.user_prompt}],
            "reasoning": {"summary": "auto"}}
        if req.params.get("reasoning_effort") is not None:
            body["reasoning"]["effort"] = req.params["reasoning_effort"]
        if req.params.get("max_completion_tokens") is not None:
            body["max_output_tokens"] = req.params["max_completion_tokens"]
        t0 = time.perf_counter()
        status, resp = post_json(self.name, self.base_url + "/responses", body,
                                 headers=self._headers(), timeout=req.timeout_s)
        ms = int((time.perf_counter() - t0) * 1000)
        if status != 200 or not isinstance(resp, dict):
            raise http_error("OpenAI", status, resp)
        reasoning_parts, message_parts = [], []
        for item in resp.get("output") or []:
            if item.get("type") == "reasoning":
                reasoning_parts += [s["text"] for s in item.get("summary") or []
                                    if isinstance(s, dict) and s.get("text")]
            elif item.get("type") == "message":
                message_parts += [c["text"] for c in item.get("content") or []
                                  if isinstance(c, dict) and c.get("type") == "output_text"
                                  and c.get("text")]
        content = resp.get("output_text") or "\n".join(message_parts)
        usage = resp.get("usage") or {}
        if usage:
            # Chat Completions names, so cost and token code reads one shape.
            if "prompt_tokens" not in usage and "input_tokens" in usage:
                usage["prompt_tokens"] = usage["input_tokens"]
            if "completion_tokens" not in usage and "output_tokens" in usage:
                usage["completion_tokens"] = usage["output_tokens"]
            resp["usage"] = usage
        return Completion(content=content,
                          reasoning="\n\n".join(reasoning_parts) if reasoning_parts else None,
                          wall_clock_ms=ms, endpoint=self.base_url,
                          provider_meta=self._meta(resp, resp.get("status"), "responses"))

    @staticmethod
    def _meta(resp: dict, finish_reason, api: str) -> dict:
        return {"system_fingerprint": resp.get("system_fingerprint"),
                "usage": resp.get("usage"), "finish_reason": finish_reason,
                "model_resolved": resp.get("model"), "api": api}

    def model_digest(self, model: str) -> str | None:
        """A 1-token probe for system_fingerprint (the scripts' method)."""
        try:
            status, resp = post_json(self.name, self.base_url + "/chat/completions", {
                "model": model, "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1, "temperature": 0}, headers=self._headers(), timeout=20)
        except ProviderError:
            return None
        fp = resp.get("system_fingerprint") if status == 200 and isinstance(resp, dict) else None
        return f"openai:{fp}" if fp else None


@register_provider("openai")
def _factory(spec) -> OpenAIProvider:
    key = os.environ.get(spec.api_key_env or "OPENAI_API_KEY")
    if not key:
        raise ValueError(f"provider openai needs an API key in "
                         f"${spec.api_key_env or 'OPENAI_API_KEY'}")
    return OpenAIProvider(spec.endpoint or os.environ.get("OPENAI_BASE_URL",
                                                          "https://api.openai.com/v1"), key)
