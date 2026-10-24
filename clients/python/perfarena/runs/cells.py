"""Cells and the planner. A cell is one (model, prompt, language, problem)
combination; the cells of one (model, prompt, language) share a submission.

Order: problem (outer) → language → prompt → model (inner), the sweep's
order: consecutive cells stay on one problem, which LeetCode's anti-bot
heuristic tolerates, and any prefix of the run mixes difficulties when
problems are interleaved by level."""
from __future__ import annotations

from dataclasses import dataclass

from ..spec import ModelSpec, RunSpec


@dataclass(frozen=True)
class Cell:
    model: ModelSpec
    prompt_name: str | None          # None = the inline default prompt
    language: str
    slug: str

    @property
    def prompt_label(self) -> str:
        return self.prompt_name or "inline"

    @property
    def tuple_key(self) -> str:
        """matrix_sweep's tuple key (cache file names derive from it)."""
        return f"{self.model.name}|{self.prompt_label}|{self.language}|{self.slug}"

    def group(self) -> tuple[str, str, str, str]:
        """The submission this cell belongs to."""
        return (self.model.name, self.model.effective_version, self.prompt_label,
                self.language)

    def ledger_key(self, spec: RunSpec) -> str:
        """Unique across competitions and oracles, so a cell done under one
        never counts as done under another."""
        return "|".join([f"{self.model.name}@{self.model.effective_version}",
                         self.prompt_label, self.language, self.slug, spec.competition,
                         spec.oracles.performance, spec.oracles.correctness or "-"])


def interleave_by_level(problems: list[dict]) -> list[str]:
    """Round-robin Easy → Medium → Hard, alphabetical within each level;
    problems with no level last."""
    buckets: dict[str, list[str]] = {"Easy": [], "Medium": [], "Hard": []}
    other: list[str] = []
    for p in problems:
        (buckets.get((p.get("level") or "").strip(), other)).append(p["title_slug"])
    for b in buckets.values():
        b.sort()
    other.sort()
    out, i = [], 0
    while any(i < len(b) for b in buckets.values()):
        out += [b[i] for b in buckets.values() if i < len(b)]
        i += 1
    return out + other


def order_problems(problems: list[dict], order: str) -> list[str]:
    if order == "alpha":
        return sorted(p["title_slug"] for p in problems)
    if order == "as-is":
        return [p["title_slug"] for p in problems]
    return interleave_by_level(problems)


def plan(spec: RunSpec, slugs: list[str]) -> list[Cell]:
    prompt_names: list[str | None] = list(spec.prompts) or [None]
    return [Cell(model=m, prompt_name=p, language=lang, slug=s)
            for s in slugs for lang in spec.languages
            for p in prompt_names for m in spec.models]
