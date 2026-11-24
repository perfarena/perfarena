#!/usr/bin/env python3
"""Language popularity vs performance, correctness, and cost (RQ3 paragraph).

For each competition and language, the script computes four measures and
correlates each with the language's rank in GitHub Octoverse 2025 (Spearman):
  1. the median over the LLMs of the per-language geometric-mean ratio to the
     best solution in that language (latency, energy, peak memory);
  2. the slowdown of the language: geometric mean, over the problems that every
     language solved, of its median latency over the fastest language's;
  3. the mean pass rate of the LLMs;
  4. the median cost per correct solution over the LLMs, in USD and tokens.

    PERFARENA_CACHE=<cache> ARENA_BASE=<url> python3 analysis/lang_popularity.py
"""
import json
import math
import os
import statistics
import sys
from pathlib import Path

from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(__file__))
import gen_paper_data as g  # noqa: E402

DATA = Path(__file__).resolve().parent / "out" / "figs" / "data"
BASE = os.environ.get("ARENA_BASE", "http://offline.invalid")
models = [m for m in g.DEFAULT_MODELS if m not in g.EXCLUDE_MODELS]
# rank in GitHub Octoverse 2025 (Shell, the 7th, is not evaluated)
OCTO = {"typescript": 1, "python3": 2, "javascript": 3, "java": 4, "csharp": 5, "php": 6, "cpp": 8}


def gmean(xs):
    return math.exp(statistics.mean(math.log(x) for x in xs))


def corr(name, vals):
    langs = sorted(vals, key=lambda s: OCTO[s])
    rho, p = spearmanr([OCTO[s] for s in langs], [vals[s] for s in langs])
    print(f"  {name:48s} rho={rho:+.2f} p={p:.3f}  "
          + "  ".join(f"{s}={vals[s]:.3g}" for s in langs))


for comp in ("leetcode-99-perf", "clbg-10"):
    print(f"== {comp} (rho > 0: less popular languages have higher values)")
    r = json.loads((DATA / f"{comp}_ratios.json").read_text())
    slugs = r["lang_slugs"]
    for key, name in (("heatmap_runtime", "latency"), ("heatmap_energy", "energy"),
                      ("heatmap_memory", "memory")):
        corr(f"{name} ratio to the best solution in the language",
             {s: statistics.median(row[j] for row in r[key] if row[j] is not None)
              for j, s in enumerate(slugs)})
    cells = json.loads((DATA / f"{comp}_cells.json").read_text())
    probs = [p for p in cells[slugs[0]] if all(cells[s].get(p) for s in slugs)]
    med = {s: {p: statistics.median(v["runtime_ms"] for v in cells[s][p].values()
                                    if v["runtime_ms"] is not None)
               for p in probs} for s in slugs}
    corr(f"slowdown against the fastest language ({len(probs)} problems)",
         {s: gmean(med[s][p] / min(med[t][p] for t in slugs) for p in probs) for s in slugs})
    sel = g.campaign_submissions(BASE, "", comp, models)
    # A problem without an attempt counts as a failure, as in pass@1.
    n_prob = len({p for (_m, _l), (_s, atts) in sel.items() for p in atts})
    corr("pass rate (%)",
         {s: 100 * statistics.mean(sum(1 for a in sel[m, s][1].values() if a.get("accepted"))
                                   / n_prob for m in models) for s in slugs})
    totals = {k: g.campaign_totals(BASE, "", sub) for k, (sub, _a) in sel.items()}
    for field, unit in (("cost", "USD"), ("tok", "tokens")):
        cost = {}
        for s in slugs:
            per = []
            for m in models:
                solved = sum(1 for a in sel[m, s][1].values() if a.get("accepted"))
                if solved:
                    per.append(totals[m, s][field] / solved)
            cost[s] = statistics.median(per)
        corr(f"median {unit} per correct solution", cost)
