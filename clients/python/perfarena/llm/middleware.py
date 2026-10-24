"""Provider decorators: each concern is written once and wraps any provider.

    provider = HardTimeout(RetryOnConnectionError(OpenAIProvider(...)), 600)
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from ..errors import ProviderError
from .base import Completion, GenerationRequest, Provider, ProviderConnectionError


class ProviderDecorator(Provider):
    def __init__(self, inner: Provider):
        self.inner = inner
        self.name = inner.name
        self.local = inner.local

    def complete(self, req: GenerationRequest) -> Completion:
        return self.inner.complete(req)

    def model_digest(self, model: str) -> str | None:
        return self.inner.model_digest(model)


class RetryOnConnectionError(ProviderDecorator):
    """Retry a call whose connection failed (refused, reset, dropped
    tunnel), with exponential back-off (5 s, 10 s, ... capped at 60 s).
    HTTP errors and timeouts are not retried: an HTTP error is the server's
    answer, and a timed-out generation is a result (generation_timeout)."""

    def __init__(self, inner: Provider, *, retries: int = 5, initial_delay_s: float = 5.0,
                 cap_s: float = 60.0, sleep: Callable[[float], None] = time.sleep):
        super().__init__(inner)
        self.retries, self.initial, self.cap, self.sleep = retries, initial_delay_s, cap_s, sleep

    def complete(self, req: GenerationRequest) -> Completion:
        delay = self.initial
        for attempt in range(self.retries + 1):
            try:
                return self.inner.complete(req)
            except ProviderConnectionError:
                if attempt >= self.retries:
                    raise
                self.sleep(delay)
                delay = min(delay * 2, self.cap)
        raise AssertionError("unreachable")


class HardTimeout(ProviderDecorator):
    """A wall-clock cap on the whole call. A socket timeout alone is not one:
    an endpoint that streams keep-alive whitespace resets it forever. The
    abandoned call finishes in a daemon thread; its result is dropped."""

    def __init__(self, inner: Provider, timeout_s: float):
        super().__init__(inner)
        self.timeout_s = timeout_s

    def complete(self, req: GenerationRequest) -> Completion:
        box: dict = {}

        def run() -> None:
            try:
                box["value"] = self.inner.complete(req)
            except BaseException as e:            # noqa: BLE001 — re-raised below
                box["error"] = e

        t = threading.Thread(target=run, daemon=True, name=f"gen-{req.model}")
        t.start()
        t.join(self.timeout_s)
        if t.is_alive():
            raise ProviderError(f"exceeded {self.timeout_s:.0f}s wall-clock cap",
                                provider=self.name, timeout=True)
        if "error" in box:
            raise box["error"]
        return box["value"]


def standard_chain(provider: Provider, *, timeout_s: float) -> Provider:
    return HardTimeout(RetryOnConnectionError(provider), timeout_s)
