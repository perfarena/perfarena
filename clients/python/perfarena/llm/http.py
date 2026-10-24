"""One POST helper for every provider: errors become ProviderError with
`timeout` / `transient` set from the failure, never SystemExit."""
from __future__ import annotations

import json

import httpx

from ..errors import ProviderError
from .base import ProviderConnectionError


def post_json(provider: str, url: str, body: dict, *, headers: dict | None = None,
              timeout: float = 600.0) -> tuple[int, object]:
    """POST and return (status, parsed JSON or text). HTTP errors are
    returned, not raised, so the caller can react (e.g. Ollama's 'does not
    support thinking'). Transport failures raise."""
    try:
        r = httpx.post(url, json=body, headers=headers or {}, timeout=timeout)
    except httpx.TimeoutException as e:
        raise ProviderError(f"{provider} call to {url} timed out after {timeout:.0f}s: {e}",
                            provider=provider, timeout=True) from e
    except httpx.TransportError as e:
        raise ProviderConnectionError(f"cannot reach {url}: {e}", provider=provider) from e
    try:
        return r.status_code, r.json()
    except json.JSONDecodeError:
        return r.status_code, r.text


def get_json(url: str, *, headers: dict | None = None, timeout: float = 10.0) -> object | None:
    """GET for metadata (digests); None on any failure."""
    try:
        r = httpx.get(url, headers=headers or {}, timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, json.JSONDecodeError):
        return None
