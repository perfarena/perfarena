"""A scripted provider for tests and dry runs: no network, answers chosen by
the caller. `perfarena run --provider fake` answers every request with a
fenced placeholder, which the language check then rejects."""
from __future__ import annotations

from typing import Callable

from ..languages import fence
from .base import Completion, GenerationRequest, Provider, register_provider

Answer = str | Callable[[GenerationRequest], str]


class FakeProvider(Provider):
    """Answers from a fixed string, a list (one per call, the last one
    repeats), or a callable. Every request is kept in `requests`."""
    name = "fake"
    local = True

    def __init__(self, answers: Answer | list[Answer] = "", *, reasoning: str | None = None,
                 meta: dict | None = None):
        self.answers = answers if isinstance(answers, list) else [answers]
        self.reasoning, self.meta = reasoning, meta or {}
        self.requests: list[GenerationRequest] = []

    def complete(self, req: GenerationRequest) -> Completion:
        i = min(len(self.requests), len(self.answers) - 1)
        self.requests.append(req)
        a = self.answers[i]
        content = a(req) if callable(a) else a
        return Completion(content=content, reasoning=self.reasoning,
                          provider_meta=dict(self.meta), wall_clock_ms=1,
                          endpoint="fake://")


@register_provider("fake")
def _factory(spec) -> FakeProvider:
    return FakeProvider(lambda req: f"```{fence('python3')}\n# placeholder\n```")
