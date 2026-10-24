"""HTTP gateway to the arena API: auth header, typed errors, a per-host
concurrency cap, and retries for idempotent calls only (a POST is never
retried, so a retry cannot create a duplicate submission)."""
from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import Settings
from ..errors import ArenaConnectionError, ArenaHTTPError

_IDEMPOTENT = {"GET", "PUT", "DELETE", "HEAD"}
_host_slots: dict[str, threading.BoundedSemaphore] = {}
_host_lock = threading.Lock()


def _slots(base_url: str, cap: int) -> threading.BoundedSemaphore:
    host = urlparse(base_url).netloc
    with _host_lock:
        if host not in _host_slots:
            _host_slots[host] = threading.BoundedSemaphore(cap)
        return _host_slots[host]


class ArenaClient:
    """One arena connection. `http` lets a test pass an in-process client
    (FastAPI's TestClient is an httpx.Client)."""

    def __init__(self, settings: Settings | None = None, *,
                 http: httpx.Client | None = None):
        self.settings = settings or Settings.resolve()
        self.base_url = self.settings.base_url
        self._http = http or httpx.Client(base_url=self.base_url,
                                          timeout=self.settings.timeout_s)
        self._slots = _slots(self.base_url, self.settings.host_cap)

    @property
    def has_key(self) -> bool:
        return bool(self.settings.api_key)

    def close(self) -> None:
        self._http.close()

    def request(self, method: str, path: str, *, json: Any = None,
                params: dict | None = None, auth: bool = True,
                timeout: float | None = None) -> Any:
        method = method.upper()
        headers = {"content-type": "application/json"}
        if auth and self.settings.api_key:
            headers["authorization"] = f"Bearer {self.settings.api_key}"
        url = path if path.startswith("http") else self.base_url + path
        tries = 1 + (self.settings.retries if method in _IDEMPOTENT else 0)
        delay = 1.0
        for i in range(tries):
            try:
                with self._slots:
                    extra = {"timeout": timeout} if timeout else {}
                    r = self._http.request(method, url, json=json, params=params,
                                           headers=headers, **extra)
            except httpx.TransportError as e:
                if i + 1 >= tries:
                    raise ArenaConnectionError(f"cannot reach {url}: {e}") from e
            else:
                if r.status_code < 400:
                    return r.json() if r.content else None
                err = ArenaHTTPError(r.status_code, method, url, r.text)
                if not err.transient or i + 1 >= tries:
                    raise err
            time.sleep(delay)
            delay = min(delay * 2, 8.0)
        raise AssertionError("unreachable")

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, json: Any = None, **kw) -> Any:
        return self.request("POST", path, json=json, **kw)

    def put(self, path: str, json: Any = None, **kw) -> Any:
        return self.request("PUT", path, json=json, **kw)

    def delete(self, path: str, **kw) -> Any:
        return self.request("DELETE", path, **kw)
