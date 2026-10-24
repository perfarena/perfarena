"""Connection settings and profiles.

Resolution order, lowest first: defaults < profile < environment < explicit
keyword arguments, except that an explicitly NAMED profile wins over the
environment (so a local ARENA_API_KEY in the shell is never sent to the
`prod` profile's host). A profile file (TOML) holds named connections and never
the key itself, only where to read it:

    # ~/.config/perfarena/profiles.toml   (or $PERFARENA_PROFILES)
    [default]
    base_url = "http://localhost:8000"
    api_key_env = "ARENA_API_KEY"

    [prod]
    base_url = "https://perfarena.ngrok.app"
    api_key_file = "~/.perfarena_admin_key"
    max_concurrency = 2

Environment: PERFARENA_BASE_URL / PERFARENA_API_KEY, and the scripts' names
ARENA_BASE_URL / ARENA_API_KEY as a fallback.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from .errors import SpecError

DEFAULT_BASE_URL = "http://localhost:8000"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


def profiles_path() -> Path:
    return Path(os.environ.get("PERFARENA_PROFILES")
                or Path.home() / ".config" / "perfarena" / "profiles.toml")


def load_profiles(path: Path | None = None) -> dict[str, dict]:
    path = path or profiles_path()
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class Settings:
    """How to reach one arena. `max_concurrency` caps parallel requests to
    the host: a public deployment gets 2 by default, so no client can flood
    it; a local one gets 8."""
    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = field(default=None, repr=False)
    timeout_s: float = 60.0
    max_concurrency: int | None = None
    retries: int = 3

    @property
    def host_cap(self) -> int:
        if self.max_concurrency:
            return self.max_concurrency
        host = urlparse(self.base_url).hostname or ""
        return 8 if host in _LOCAL_HOSTS else 2

    @classmethod
    def resolve(cls, profile: str | None = None, *, profiles: dict | None = None,
                **overrides) -> "Settings":
        s = cls()
        profiles = load_profiles() if profiles is None else profiles
        name = profile or os.environ.get("PERFARENA_PROFILE")
        if name and name not in profiles:
            raise SpecError(f"profile {name!r} not found in {profiles_path()}")
        p = profiles.get(name or "default", {})
        key = _key_from(p)
        s = replace(s, **{k: v for k, v in {
            "base_url": p.get("base_url"), "api_key": key,
            "timeout_s": p.get("timeout_s"), "max_concurrency": p.get("max_concurrency"),
            "retries": p.get("retries")}.items() if v is not None})
        if not name:
            env_url = (os.environ.get("PERFARENA_BASE_URL")
                       or os.environ.get("ARENA_BASE_URL"))
            env_key = (os.environ.get("PERFARENA_API_KEY")
                       or os.environ.get("ARENA_API_KEY"))
            if env_url:
                s = replace(s, base_url=env_url)
            if env_key:
                s = replace(s, api_key=env_key)
        s = replace(s, **{k: v for k, v in overrides.items() if v is not None})
        return replace(s, base_url=s.base_url.rstrip("/"))


def _key_from(p: dict) -> str | None:
    if p.get("api_key_env"):
        return os.environ.get(p["api_key_env"]) or None
    if p.get("api_key_file"):
        path = Path(p["api_key_file"]).expanduser()
        return path.read_text().strip() if path.exists() else None
    return None
