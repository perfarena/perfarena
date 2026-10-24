"""LLM provider protocol and registry.

A Provider does transport only: it sends the (system, user) prompt pair to a
model and returns the raw answer, the reasoning channel, and provider
metadata. Prompt building and code extraction happen once, above it
(perfarena.generate), instead of in every provider.

Providers register by name. Third-party packages add one through the
`perfarena.providers` entry-point group: the entry point loads a factory
`factory(model_spec) -> Provider`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Callable

from ..errors import ProviderError

if TYPE_CHECKING:
    from ..spec import ModelSpec


@dataclass(frozen=True)
class GenerationRequest:
    model: str
    system_prompt: str
    user_prompt: str
    params: dict
    timeout_s: float = 600.0


@dataclass
class Completion:
    content: str                     # the model's full, unedited answer
    reasoning: str | None            # thinking channel, if the model has one
    provider_meta: dict = field(default_factory=dict)
    wall_clock_ms: int = 0           # time spent in the provider call(s)
    endpoint: str | None = None


class Provider(ABC):
    name: str = ""
    # Local inference costs nothing (Ollama, the fake provider).
    local: bool = False

    @abstractmethod
    def complete(self, req: GenerationRequest) -> Completion: ...

    def model_digest(self, model: str) -> str | None:
        """Provider-namespaced model identity (e.g. `ollama:<sha256>`), or
        None to let the platform derive one."""
        return None


class ProviderConnectionError(ProviderError):
    """The provider could not be reached (connection refused/reset/dropped).
    The only error the retry middleware retries."""

    def __init__(self, message: str, *, provider: str):
        super().__init__(message, provider=provider, transient=True)


def http_error(provider: str, status: int, body: object) -> ProviderError:
    """A provider HTTP error; 408/425/429/431/529 and 5xx are transient."""
    transient = status in (408, 425, 429, 431, 529) or status >= 500
    return ProviderError(f"{provider} HTTP {status}: {body}", provider=provider,
                         status=status, transient=transient)


Factory = Callable[["ModelSpec"], Provider]
_FACTORIES: dict[str, Factory] = {}
_entry_points_loaded = False


def register_provider(name: str) -> Callable[[Factory], Factory]:
    def deco(factory: Factory) -> Factory:
        _FACTORIES[name] = factory
        return factory
    return deco


def _load_entry_points() -> None:
    global _entry_points_loaded
    if _entry_points_loaded:
        return
    _entry_points_loaded = True
    for ep in entry_points(group="perfarena.providers"):
        _FACTORIES.setdefault(ep.name, ep.load())


def provider_names() -> list[str]:
    _load_entry_points()
    return sorted(_FACTORIES)


def create_provider(spec: "ModelSpec") -> Provider:
    _load_entry_points()
    factory = _FACTORIES.get(spec.provider)
    if factory is None:
        raise ValueError(f"unknown provider {spec.provider!r}; "
                         f"available: {', '.join(provider_names())}")
    return factory(spec)
