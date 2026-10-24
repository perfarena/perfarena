"""PerfArena SDK: run and analyze LLM code-efficiency experiments.

    from perfarena import Arena, RunSpec
    spec = (RunSpec.builder().model("gpt-5.5", provider="openai")
            .languages("python3").oracles("arena-linux-local", "arena-local").build())
    Arena.from_profile("prod").run(spec)
"""
from .arena import Arena
from .config import Settings
from .errors import (ArenaConnectionError, ArenaHTTPError, PerfArenaError,
                     PolicyMismatch, ProviderError, SpecError)
from .runs import EventBus, Ledger, RunResult
from .spec import ModelSpec, RunSpec

__version__ = "0.1.0"

__all__ = [
    "Arena", "Settings", "RunSpec", "ModelSpec", "RunResult", "EventBus", "Ledger",
    "PerfArenaError", "ArenaHTTPError", "ArenaConnectionError", "ProviderError",
    "SpecError", "PolicyMismatch",
]
