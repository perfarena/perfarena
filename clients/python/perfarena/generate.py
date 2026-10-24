"""Generator: prompt → provider → code, with the provenance trace the
platform stores. The trace has the scripts' shape (prompt, system_prompt,
raw_response, reasoning, model, provider, endpoint, params, created_at,
wall_clock_ms, provider_meta), so SDK and script attempts look the same."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .codegen import CodePipeline
from .languages import fence
from .llm.base import GenerationRequest, Provider
from .prompts import PromptBuilder, RoundState, is_clbg_statement


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass
class Generation:
    code: str
    trace: dict


class Generator:
    def __init__(self, provider: Provider, *, prompts: PromptBuilder | None = None,
                 pipeline: CodePipeline | None = None, timeout_s: float = 600.0):
        self.provider = provider
        self.prompts = prompts or PromptBuilder()
        self.pipeline = pipeline or CodePipeline()
        self.timeout_s = timeout_s

    def generate(self, model: str, prompt: dict | None, problem: dict, language: str,
                 params: dict, state: RoundState | None = None) -> Generation:
        sys_p, user_p = self.prompts.build(prompt, problem, language, state)
        comp = self.provider.complete(GenerationRequest(
            model=model, system_prompt=sys_p, user_prompt=user_p, params=params,
            timeout_s=self.timeout_s))
        code = self.pipeline.run(comp.content, language=language, fence=fence(language),
                                 is_clbg=is_clbg_statement(problem.get("content") or ""))
        trace = {
            "prompt": user_p, "system_prompt": sys_p, "raw_response": comp.content,
            "reasoning": comp.reasoning, "model": model, "provider": self.provider.name,
            "endpoint": comp.endpoint, "params": params, "created_at": now_iso(),
            "wall_clock_ms": comp.wall_clock_ms, "provider_meta": comp.provider_meta,
        }
        return Generation(code=code, trace=trace)


def failed_trace(*, model: str, provider: str, params: dict, verdict: str,
                 error: BaseException, wall_clock_ms: int | None = None) -> dict:
    """Trace of a generation that failed: no code; the error is kept in
    provider_meta (as the scripts record it)."""
    return {"prompt": None, "system_prompt": None, "raw_response": "",
            "reasoning": None, "model": model, "provider": provider, "endpoint": None,
            "params": params, "created_at": now_iso(), "wall_clock_ms": wall_clock_ms,
            "provider_meta": {"failure": verdict,
                              "error": f"{type(error).__name__}: {error}"[:2000]}}
