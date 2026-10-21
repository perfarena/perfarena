"""Efficiency rankings with the method of the PerfArena paper.

Every (language, problem) cell that two entrants both solved is one duel per
metric: the lower value wins, two values within the measurement noise tie (half
a win each), and a failed cell gives no duel, so failing a problem neither helps
nor hurts an efficiency ranking (pass@1 reports it). A Bradley-Terry model
(the model of Chatbot Arena) turns the duels into one strength per entrant, and
the page reports each entrant's win probability: its mean chance of beating one
of the other entrants on a cell that both solved. A bootstrap with Holm's
correction tells which pairs differ, and the entrants that no other entrant
beats significantly are the tied leaders.

An entrant is one model under one workflow (agent and number of rounds), so
single-shot and agentic submissions rank side by side, and filters separate
them. Each entrant contributes one result per cell: the final attempt of one
sample chain (round 0 followed to its last round). The chain is the first
sample, or the latest re-run in a one-sample submission, as in the paper.

This module is pure computation over rows that the caller loads; the HTTP
layer is ranking_api.py.
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable

import numpy as np

METRICS = {"latency": "avg_runtime_ms", "energy": "avg_runtime_energy_j",
           "memory": "avg_memory_mb"}
ALPHA = 0.05


@dataclass
class Settings:
    tie_pct: dict[str, float]          # metric -> tie margin in percent
    bootstrap: int = 1000
    min_cells: int = 3                 # per-language minimum to get a rank
    min_problem_clusters: int = 30     # resample problems from this many on
    seed: int = 0


@dataclass
class Entrant:
    key: str
    model_name: str
    model_version: str
    agent_slug: str
    agent_name: str
    agent_kind: str
    n_rounds: int
    n_samples: int
    submission_ids: list[int] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    n_cells: int = 0                   # cells the competition holds for it
    n_attempted: int = 0               # cells with a result
    n_solved: int = 0
    cost_usd: float = 0.0
    n_costed: int = 0                  # attempts with a known cost

    @property
    def workflow(self) -> str:
        label = self.agent_name or self.agent_slug or "direct"
        return label + (f" · {self.n_rounds} rounds" if self.n_rounds > 1 else "")

    @property
    def pass_at_1(self) -> float | None:
        return self.n_solved / self.n_cells if self.n_cells else None

    @property
    def cost_per_attempt_usd(self) -> float | None:
        return self.cost_usd / self.n_attempted if self.n_attempted and self.n_costed else None


def entrant_key(model_name: str, agent_slug: str, n_rounds: int) -> str:
    return f"{model_name}|{agent_slug}|r{n_rounds}"


def final_attempts(attempts: list, latest: bool = False) -> dict[tuple[int, int], object]:
    """(submission_id, problem_id) -> the final attempt of one sample chain,
    followed through previous_attempt_id to its last round. The chain starts
    at the earliest round-0 attempt (the first sample) or, with `latest`, at
    the latest one: in a one-sample submission, a re-run of a problem replaces
    the earlier attempt."""
    by_cell: dict[tuple[int, int], list] = defaultdict(list)
    for a in attempts:
        by_cell[(a.submission_id, a.problem_id)].append(a)
    out = {}
    for cell, rows in by_cell.items():
        roots = sorted((a for a in rows if (a.round_index or 0) == 0), key=lambda a: a.id)
        if not roots:
            continue
        child = {a.previous_attempt_id: a for a in sorted(rows, key=lambda a: a.id)
                 if a.previous_attempt_id is not None}
        cur, seen = (roots[-1] if latest else roots[0]), set()
        while cur.id in child and cur.id not in seen:
            seen.add(cur.id)
            cur = child[cur.id]
        out[cell] = cur
    return out


# ---- duels and the Bradley-Terry fit --------------------------------------------

def duel_matrices(cells: dict, keys: list[str], margin_pct: float):
    """[(language, problem, W)] with W[i, j] = wins of keys[i] over keys[j]."""
    idx = {k: i for i, k in enumerate(keys)}
    band = math.log1p(margin_pct / 100.0)
    out = []
    for (lang, prob), values in cells.items():
        present = [(idx[k], v) for k, v in values.items() if k in idx and v and v > 0]
        if len(present) < 2:
            continue
        W = np.zeros((len(keys), len(keys)))
        for (i, vi), (j, vj) in combinations(present, 2):
            d = math.log(vi / vj)
            if abs(d) <= band:
                W[i, j] += 0.5
                W[j, i] += 0.5
            elif d < 0:
                W[i, j] += 1
            else:
                W[j, i] += 1
        out.append((lang, prob, W))
    return out


def fit(W: np.ndarray, pseudo: float = 0.5, start=None, tol: float = 1e-9,
        iters: int = 20000) -> np.ndarray:
    """Elo-scale Bradley-Terry ratings (MM algorithm, Hunter 2004), with a
    pseudo-count of half a tied duel per pair."""
    k = W.shape[0]
    W = W + pseudo / 2 * (1 - np.eye(k))
    N, w = W + W.T, W.sum(1)
    p = np.ones(k) if start is None else 10 ** ((np.asarray(start) - 1000) / 400)
    for _ in range(iters):
        new = w / (N / (p[:, None] + p[None, :])).sum(1)
        new /= math.exp(np.log(new).mean())
        done = np.max(np.abs(np.log(new / p))) < tol
        p = new
        if done:
            break
    return 1000 + 400 * np.log10(p)


def win_prob(ra: float, rb: float) -> float:
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def avg_win_prob(ratings: dict[str, float]) -> dict[str, float]:
    if len(ratings) < 2:
        return {k: 0.5 for k in ratings}
    return {k: sum(win_prob(r, ratings[o]) for o in ratings if o != k) / (len(ratings) - 1)
            for k, r in ratings.items()}


def holm(pvals: list[float]) -> list[bool]:
    m, reject = len(pvals), [False] * len(pvals)
    for i, j in enumerate(sorted(range(m), key=lambda j: pvals[j])):
        if pvals[j] > ALPHA / (m - i):
            break
        reject[j] = True
    return reject


def significance(keys: list[str], point: np.ndarray, units: list[np.ndarray],
                 n_boot: int, rng: np.random.Generator) -> tuple[dict, int]:
    """beats[a] = keys that a beats significantly (bootstrap Wald test per pair,
    Holm over all pairs); also the number of significant pairs."""
    beats = {k: [] for k in keys}
    if len(keys) < 2 or len(units) < 2 or n_boot < 2:
        return beats, 0
    U = np.stack(units)
    counts = rng.multinomial(len(U), np.full(len(U), 1 / len(U)), size=n_boot)
    samples = np.array([fit(np.tensordot(c, U, axes=1), start=point) for c in counts])
    pairs = list(combinations(range(len(keys)), 2))
    pv = []
    for i, j in pairs:
        se = float(np.std(samples[:, i] - samples[:, j], ddof=1))
        z = (point[i] - point[j]) / se if se > 0 else 0.0
        pv.append(math.erfc(abs(z) / math.sqrt(2)))
    rej = holm(pv)
    for (i, j), r in zip(pairs, rej):
        if r:
            win, lose = (i, j) if point[i] > point[j] else (j, i)
            beats[keys[win]].append(keys[lose])
    return beats, sum(rej)


def rated_keys(duels, keys: list[str], min_cells: int) -> list[str]:
    """Entrants with at least min_cells cells that hold a duel."""
    n = np.zeros(len(keys), dtype=int)
    for _, _, W in duels:
        n += ((W + W.T).sum(1) > 0).astype(int)
    return [k for i, k in enumerate(keys) if n[i] >= min_cells]


def restrict(duels, keys: list[str], sub: list[str]):
    idx = [keys.index(k) for k in sub]
    return [(l, p, W[np.ix_(idx, idx)]) for l, p, W in duels]


def analyse_metric(cells: dict, keys: list[str], margin_pct: float, settings: Settings,
                   n_problems: int, per_language_min: bool, rng: np.random.Generator) -> dict:
    """Ratings, win probabilities, and tied leaders of one metric."""
    duels = duel_matrices(cells, keys, margin_pct)
    sub = rated_keys(duels, keys, settings.min_cells) if per_language_min else \
        rated_keys(duels, keys, 1)
    if len(sub) < 2:
        return dict(rated=sub, ratings={}, win_prob={}, tied_leaders=list(sub),
                    beats={k: [] for k in sub}, sig_pairs=0, n_pairs=0, unit=None,
                    n_duel_cells=len(duels))
    duels = restrict(duels, keys, sub)
    point = fit(sum(W for _, _, W in duels))
    if n_problems >= settings.min_problem_clusters and not per_language_min:
        groups: dict = defaultdict(lambda: 0)
        for _, prob, W in duels:
            groups[prob] = groups[prob] + W
        units, unit = list(groups.values()), "problems"
    else:
        units, unit = [W for _, _, W in duels], "cells"
    beats, n_sig = significance(sub, point, units, settings.bootstrap, rng)
    ratings = {k: float(point[i]) for i, k in enumerate(sub)}
    leaders = [k for k in sub if not any(k in beats[o] for o in sub)]
    return dict(rated=sub, ratings=ratings, win_prob=avg_win_prob(ratings),
                tied_leaders=leaders, beats=beats, sig_pairs=n_sig,
                n_pairs=len(sub) * (len(sub) - 1) // 2, unit=unit, n_duel_cells=len(duels))


def pareto(keys: list[str], beats_by_metric: dict[str, dict]) -> dict:
    """Optimality levels where A dominates B when A is significantly better in
    at least one metric and significantly worse in none."""
    def dom(a, b):
        better = any(b in beats_by_metric[m].get(a, []) for m in beats_by_metric)
        worse = any(a in beats_by_metric[m].get(b, []) for m in beats_by_metric)
        return better and not worse
    level, left, lv = {}, set(keys), 1
    while left:
        top = {k for k in left if not any(dom(o, k) for o in left if o != k)}
        if not top:                       # a cycle: put the rest on one level
            top = set(left)
        for k in top:
            level[k] = lv
        left -= top
        lv += 1
    edges = [[a, b] for a in keys for b in keys if a != b and dom(a, b)
             and not any(dom(a, c) and dom(c, b) for c in keys if c not in (a, b))]
    return dict(level=level, edges=edges)


def head_to_head(cells: dict, a: str, b: str, margin_pct: float) -> dict:
    """a against b on the cells that both solved: geometric-mean ratio of a's
    value to b's, and the shares of cells that a wins, ties, and loses."""
    band = math.log1p(margin_pct / 100.0)
    logs = [math.log(v[a] / v[b]) for v in cells.values()
            if v.get(a) and v.get(b) and v[a] > 0 and v[b] > 0]
    if not logs:
        return dict(n_shared=0, ratio=None, a_wins=None, ties=None, b_wins=None)
    n = len(logs)
    return dict(n_shared=n, ratio=math.exp(sum(logs) / n),
                a_wins=sum(x < -band for x in logs) / n,
                ties=sum(abs(x) <= band for x in logs) / n,
                b_wins=sum(x > band for x in logs) / n)


def rank(cells_by_metric: dict[str, dict], keys: list[str], settings: Settings,
         n_problems: int, single_language: bool) -> dict:
    """The full analysis: one result per metric, plus the Pareto levels over
    the metrics that hold data (LeetCode measures no energy)."""
    out = {}
    for m, cells in cells_by_metric.items():
        rng = np.random.default_rng(np.random.SeedSequence([settings.seed, len(m), ord(m[0])]))
        out[m] = analyse_metric(cells, keys, settings.tie_pct[m], settings, n_problems,
                                single_language, rng)
    measured = [m for m in cells_by_metric if out[m]["rated"]]
    rated_all = [k for k in keys if measured and all(k in out[m]["rated"] for m in measured)]
    out["pareto"] = pareto(rated_all, {m: out[m]["beats"] for m in measured})
    out["pareto"]["metrics"] = measured
    return out


# ---- a small cache keyed by the request and the data version ----------------------

class Cache:
    def __init__(self, size: int = 64):
        self.size, self.data, self.lock = size, OrderedDict(), threading.Lock()

    def get_or_compute(self, key, compute: Callable[[], dict]) -> dict:
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        value = compute()
        with self.lock:
            self.data[key] = value
            while len(self.data) > self.size:
                self.data.popitem(last=False)
        return value


CACHE = Cache()
