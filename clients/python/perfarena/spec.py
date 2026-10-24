"""RunSpec: one declarative, validated description of a run.

A spec says WHAT to run: the models and their parameters, the prompt, the
competition and languages, the oracles (and the replica policy they must
have), sampling, rounds, and steering knobs. It is immutable (digest() is
its identity), loads from and dumps to YAML, and is built in code with
RunSpec.builder().
The CLI flags are generated from these fields (perfarena.cli), so the CLI
cannot drift from the SDK.

    model: {name: gpt-5.5, provider: openai, params: {temperature: 0.0}}
    prompt: experiment-system
    competition: leetcode-99-perf
    languages: [python3, cpp]
    oracles: {performance: arena-linux-local, correctness: arena-local}
    rounds: {n: 3, stop_on_accept: true}
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import SpecError
from .languages import LANGUAGE_DISPLAY

# Field docstrings become field descriptions: the CLI help and the docs use them.
_FROZEN = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class ModelSpec(BaseModel):
    """One model under test and how to reach it."""
    model_config = _FROZEN
    name: str
    """Model name as the provider knows it (e.g. gpt-5.5, qwen3:8b)."""
    provider: str = "ollama"
    """Provider name: ollama, openai, anthropic, fake, or a plugin's name."""
    version: str | None = None
    """Free-form provenance tag. Default: the provider name (the scripts'
    --model-version default)."""
    params: dict[str, Any] = Field(default_factory=lambda: {"temperature": 0.0})
    """Decoding parameters (temperature, top_p, ...). Each provider sends the
    ones its API takes. The trace records all of them."""
    endpoint: str | None = None
    """Provider base URL. Default: the provider's own (OLLAMA_URL,
    OPENAI_BASE_URL, ANTHROPIC_BASE_URL, or the public API)."""
    api_key_env: str | None = None
    """NAME of the env var that holds the provider's API key (never the key
    itself). Default: OPENAI_API_KEY or ANTHROPIC_API_KEY."""
    think: bool = True
    """Let a reasoning model think. False sends think=false (Ollama)."""
    digest: str | None = None
    """Explicit model identity digest (HF revision, fingerprint). Default:
    the provider's own, else the platform derives one."""

    @property
    def effective_version(self) -> str:
        return self.version or self.provider


class OracleSpec(BaseModel):
    model_config = _FROZEN
    performance: str
    """Performance oracle (harness) slug, e.g. arena-linux-local or leetcode."""
    correctness: str | None = None
    """Correctness oracle slug. None: the performance oracle's own verdict
    decides."""


class ReplicaSpec(BaseModel):
    """The replica policy each oracle must have. Check only: a run never
    changes an oracle's policy (oracles are shared); it refuses to start when
    the live policy differs."""
    model_config = _FROZEN
    performance: str | None = None
    """Required policy of the performance oracle (e.g. sticky, split_repeats)."""
    correctness: str | None = None
    """Required policy of the correctness oracle (e.g. least_busy,
    broadcast_merge)."""


class SamplingSpec(BaseModel):
    """k independent samples per problem. Their temperatures spread evenly
    from the model's temperature to +spread (capped at 1.0), as in
    submit_with_local_model.py."""
    model_config = _FROZEN
    k: int = Field(default=1, ge=1, le=200)
    """Independent samples per problem."""
    spread: float = Field(default=0.5, ge=0.0, le=1.0)
    """Temperature range over the k samples, above the model's temperature."""


class RoundsSpec(BaseModel):
    """Refinement rounds per sample. Round r>0 sees round r-1's code and
    the judge's verdict."""
    model_config = _FROZEN
    n: int = Field(default=1, ge=1, le=10)
    """Rounds per sample. 1: no refinement."""
    stop_on_accept: bool = True
    """Stop a sample at its first Accepted verdict."""


class RunSpec(BaseModel):
    """One declarative, validated description of a run (see the module
    docstring). Immutable: change it with with_changes()."""
    model_config = _FROZEN
    models: tuple[ModelSpec, ...]
    """Models under test. YAML also takes one `model:`."""
    oracles: OracleSpec
    """The performance oracle and the correctness oracle."""
    prompts: tuple[str, ...] = ()
    """Registry prompt names. Empty: the built-in inline prompt. YAML also
    takes one `prompt:`."""
    competition: str = "leetcode-99-perf"
    """Competition slug."""
    languages: tuple[str, ...] = ("python3",)
    """Target languages, as LeetCode language slugs (python3, cpp, ...)."""
    problems: tuple[str, ...] = ()
    """Problem slugs. Empty: every problem of the competition."""
    problem_order: Literal["interleave", "alpha", "as-is"] = "interleave"
    """interleave: Easy, Medium, Hard in turn. alpha: by slug. as-is: as given."""
    replicas: ReplicaSpec | None = None
    """The replica policy each oracle must have. Check only."""
    sampling: SamplingSpec = SamplingSpec()
    """Independent samples per problem."""
    rounds: RoundsSpec = RoundsSpec()
    """Refinement rounds per sample."""
    hints: bool = False
    """Add the problem's hints to the prompt."""
    target: Literal["latency", "memory"] | None = None
    """Ask the model to optimize this metric (fills {{optimization_directive}})."""
    compile_flags: str | dict[str, str] | None = None
    """One flag string for every language, or a {language: flags} map."""
    agent: str | None = None
    """Agent slug recorded on the submission. None: the server's `direct`."""
    concurrency: int = Field(default=1, ge=1, le=64)
    """Cells processed at once. Forced to 1 when the run touches LeetCode."""
    poll_timeout_s: int | None = None
    """Per-attempt verdict wait. None: 300 s when LeetCode judges, else 1800 s."""
    inference_timeout_s: int = 600
    """Wall-clock cap on one generation."""
    max_cells: int | None = None
    """Do at most this many cells in this run (for a trial run)."""
    record_generation_failures: bool = True
    """Submit a failed generation as a prefailed attempt, so pass@1 counts
    it. False: leave it in the ledger to retry."""
    new_submission: bool = False
    """Always create a new submission instead of reusing a matching one."""
    append_fully_accepted: bool = False
    """Also reuse a matching submission whose attempts are all Accepted."""
    sign_key: str | None = None
    """Ed25519 private key file that signs each trace (needs the `sign` extra)."""
    inter_cell_delay_s: float = Field(default=5.0, ge=0)
    """LeetCode pacing: the wait between cells on one problem and language."""
    inter_problem_delay_s: float = Field(default=10.0, ge=0)
    """LeetCode pacing: the wait when the problem or the language changes."""
    recovery_wait_s: float = Field(default=180.0, ge=0)
    """When the runner or the LeetCode account pool is down: one wait, then
    one redispatch, before the cell is recorded as dispatch_failed."""
    keep_cache: bool = False
    """Keep cached generations after a verdict (default: drop them)."""

    @model_validator(mode="before")
    @classmethod
    def _single_model_and_prompt(cls, data: Any) -> Any:
        """Accept `model:` / `prompt:` (one) as well as `models:` / `prompts:`."""
        if isinstance(data, dict):
            data = dict(data)
            if "model" in data:
                if "models" in data:
                    raise ValueError("give `model` or `models`, not both")
                data["models"] = [data.pop("model")]
            if "prompt" in data:
                if "prompts" in data:
                    raise ValueError("give `prompt` or `prompts`, not both")
                p = data.pop("prompt")
                data["prompts"] = [] if p is None else [p]
        return data

    @field_validator("languages")
    @classmethod
    def _known_languages(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        bad = [x for x in v if x not in LANGUAGE_DISPLAY]
        if bad:
            raise ValueError(f"unsupported languages {bad}; "
                             f"supported: {sorted(LANGUAGE_DISPLAY)}")
        if not v:
            raise ValueError("at least one language")
        return v

    @field_validator("models")
    @classmethod
    def _some_model(cls, v):
        if not v:
            raise ValueError("at least one model")
        return v

    # ---- derived ----------------------------------------------------------

    @property
    def touches_leetcode(self) -> bool:
        """LeetCode judges this run (perf or correctness), so it must be
        paced and run one cell at a time."""
        return (self.oracles.correctness == "leetcode"
                or "leetcode" in self.oracles.performance.lower())

    @property
    def effective_poll_timeout_s(self) -> int:
        if self.poll_timeout_s is not None:
            return self.poll_timeout_s
        return 300 if self.touches_leetcode else 1800

    def compile_flags_for(self, language: str) -> str:
        """The operator flags for one language (scripts'
        _resolve_compile_flags, without its string-map syntax)."""
        if not self.compile_flags:
            return ""
        if isinstance(self.compile_flags, dict):
            return self.compile_flags.get(language, "").strip()
        return self.compile_flags.strip()

    def digest(self) -> str:
        """Stable hash of the spec (for job ids and ledgers)."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True,
                             separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    # ---- I/O ---------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "RunSpec":
        try:
            return cls.model_validate(data)
        except ValueError as e:
            raise SpecError(str(e)) from e

    @classmethod
    def from_yaml(cls, text: str) -> "RunSpec":
        return cls.from_dict(yaml.safe_load(text) or {})

    @classmethod
    def load(cls, path: str | Path) -> "RunSpec":
        return cls.from_yaml(Path(path).read_text())

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json", exclude_defaults=True),
                              sort_keys=False)

    def with_changes(self, **changes: Any) -> "RunSpec":
        """A copy with top-level fields replaced (re-validated)."""
        return RunSpec.from_dict({**self.model_dump(mode="json"), **changes})

    @staticmethod
    def builder() -> "RunSpecBuilder":
        return RunSpecBuilder()


class RunSpecBuilder:
    """Fluent construction of a RunSpec; build() validates."""

    def __init__(self) -> None:
        self._d: dict[str, Any] = {"models": []}

    def model(self, name: str, *, provider: str = "ollama", version: str | None = None,
              endpoint: str | None = None, api_key_env: str | None = None,
              think: bool = True, digest: str | None = None,
              **params: Any) -> "RunSpecBuilder":
        self._d["models"].append({
            "name": name, "provider": provider, "version": version,
            "endpoint": endpoint, "api_key_env": api_key_env, "think": think,
            "digest": digest, "params": params or {"temperature": 0.0}})
        return self

    def prompt(self, *names: str) -> "RunSpecBuilder":
        self._d["prompts"] = list(names)
        return self

    def competition(self, slug: str) -> "RunSpecBuilder":
        self._d["competition"] = slug
        return self

    def languages(self, *langs: str) -> "RunSpecBuilder":
        self._d["languages"] = list(langs)
        return self

    def problems(self, *slugs: str, order: str | None = None) -> "RunSpecBuilder":
        self._d["problems"] = list(slugs)
        if order:
            self._d["problem_order"] = order
        return self

    def oracles(self, performance: str, correctness: str | None = None) -> "RunSpecBuilder":
        self._d["oracles"] = {"performance": performance, "correctness": correctness}
        return self

    def replicas(self, performance: str | None = None,
                 correctness: str | None = None) -> "RunSpecBuilder":
        self._d["replicas"] = {"performance": performance, "correctness": correctness}
        return self

    def samples(self, k: int, spread: float = 0.5) -> "RunSpecBuilder":
        self._d["sampling"] = {"k": k, "spread": spread}
        return self

    def rounds(self, n: int, stop_on_accept: bool = True) -> "RunSpecBuilder":
        self._d["rounds"] = {"n": n, "stop_on_accept": stop_on_accept}
        return self

    def steer(self, *, hints: bool | None = None, target: str | None = None,
              compile_flags: str | dict | None = None) -> "RunSpecBuilder":
        for k, v in (("hints", hints), ("target", target), ("compile_flags", compile_flags)):
            if v is not None:
                self._d[k] = v
        return self

    def set(self, **fields: Any) -> "RunSpecBuilder":
        """Any other top-level field (agent, concurrency, max_cells, ...)."""
        self._d.update(fields)
        return self

    def build(self) -> RunSpec:
        return RunSpec.from_dict(self._d)
