"""Experiment strategies: how many independent sample chains a cell runs,
with which parameters, and how many refinement rounds each chain gets.

  SingleShot          1 chain, 1 round
  KSamples            k chains (temperatures spread), 1 round each
  IterativeRefine     k chains, n rounds each, every round runs
  RetryUntilAccepted  k chains, up to n rounds, stop at the first Accepted

RunSpec picks one (strategy_for); a caller may pass its own subclass."""
from __future__ import annotations

from dataclasses import dataclass

from ..spec import ModelSpec, RunSpec


@dataclass(frozen=True)
class Chain:
    index: int                 # 1-based sample number
    params: dict


def temperature_schedule(base: float, k: int, spread: float) -> list[float]:
    """k temperatures evenly from base to min(1.0, base + spread), as
    submit_with_local_model.py spaces its samples."""
    if k == 1:
        return [base]
    hi = min(1.0, base + spread)
    step = (hi - base) / (k - 1)
    return [round(base + i * step, 3) for i in range(k)]


class ExperimentStrategy:
    name = "single_shot"

    def __init__(self, spec: RunSpec):
        self.spec = spec

    def chains(self, model: ModelSpec) -> list[Chain]:
        return [Chain(1, dict(model.params))]

    @property
    def rounds(self) -> int:
        return 1

    def stop_after(self, round_idx: int, accepted: bool) -> bool:
        return round_idx + 1 >= self.rounds


class SingleShot(ExperimentStrategy):
    pass


class KSamples(ExperimentStrategy):
    name = "k_samples"

    def chains(self, model: ModelSpec) -> list[Chain]:
        base = float(model.params.get("temperature") or 0.0)
        temps = temperature_schedule(base, self.spec.sampling.k, self.spec.sampling.spread)
        return [Chain(i, {**model.params, "temperature": t})
                for i, t in enumerate(temps, start=1)]


class IterativeRefine(KSamples):
    name = "iterative_refine"

    @property
    def rounds(self) -> int:
        return self.spec.rounds.n


class RetryUntilAccepted(IterativeRefine):
    name = "retry_until_accepted"

    def stop_after(self, round_idx: int, accepted: bool) -> bool:
        return accepted or super().stop_after(round_idx, accepted)


def strategy_for(spec: RunSpec) -> ExperimentStrategy:
    if spec.rounds.n > 1:
        return (RetryUntilAccepted if spec.rounds.stop_on_accept else IterativeRefine)(spec)
    if spec.sampling.k > 1:
        return KSamples(spec)
    return SingleShot(spec)
