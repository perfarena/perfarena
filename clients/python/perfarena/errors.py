"""Typed errors. Nothing below the CLI raises SystemExit, so a caller (a
script, the MCP server) can tell a transient failure from a permanent one
without parsing messages."""
from __future__ import annotations


class PerfArenaError(Exception):
    """Base class of every SDK error."""

    transient = False


class ArenaHTTPError(PerfArenaError):
    """The arena API answered with an error status."""

    def __init__(self, status: int, method: str, url: str, body: str):
        self.status, self.method, self.url, self.body = status, method, url, body
        # Same first line as the scripts' http_json, so logs read the same.
        super().__init__(f"HTTP {status} {method} {url}\n{body}")

    @property
    def transient(self) -> bool:            # type: ignore[override]
        """True for 408, 425, 429, and 5xx: a later retry may succeed."""
        return self.status in (408, 425, 429) or self.status >= 500


class ArenaConnectionError(PerfArenaError):
    """The arena API could not be reached."""

    transient = True


class ProviderError(PerfArenaError):
    """An LLM provider call failed.

    `timeout`: the model did not answer in time (recorded as a
    generation_timeout attempt). `transient`: infrastructure trouble
    (rate limit, gateway, dropped connection) that a later retry may clear.
    Anything else is a permanent generation error."""

    def __init__(self, message: str, *, provider: str, status: int | None = None,
                 transient: bool = False, timeout: bool = False):
        super().__init__(message)
        self.provider, self.status = provider, status
        self.transient, self.timeout = transient, timeout


class SpecError(PerfArenaError):
    """A RunSpec is invalid or does not fit the server."""


class PolicyMismatch(SpecError):
    """An oracle's replica policy differs from the one the spec requires."""


def generation_failure_verdict(err: BaseException) -> str | None:
    """Verdict to record for a failed generation, or None to retry later.

    Same rule as matrix_sweep._generation_failure_verdict, on typed fields:
    a timeout is the model failing to answer (`generation_timeout`), an
    infrastructure error is not the model's fault (retry), and anything else
    is `generation_error`."""
    if isinstance(err, ProviderError):
        if err.timeout:
            return "generation_timeout"
        if err.transient:
            return None
        return "generation_error"
    if isinstance(err, TimeoutError):
        return "generation_timeout"
    return "generation_error"
