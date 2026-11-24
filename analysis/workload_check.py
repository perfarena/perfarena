#!/usr/bin/env python3
"""Does the reduced measurement workload keep the order of the solutions in a
cell (Section 3)? Compares, per cell, the ranking of the LLMs from the kept
test cases (weighted, as PerfArena measures) with the ranking from all
profiled test cases (unweighted mean), both from the same runs.

    python3 analysis/workload_check.py workload_sample.jsonl

Input: the JSON lines of workload_sample.py (bicho, 0.3 s per test case).
For each cell, only the test cases that every solution of the cell completed
enter both scores. Statistics per cell and metric: Kendall's tau between the
two rankings, and the share of LLM pairs whose duel outcome (win, tie, loss,
with the paper's tie margin) is the same under both workloads, also pooled
over all cells, with the share of duels that reverse (a win becomes a loss).

Output: figs/data/leetcode_workload_check.json
"""
import itertools, json, math, statistics, sys
from collections import defaultdict
from pathlib import Path

from scipy.stats import kendalltau

from bt_analysis import TIE as BT_TIE, band

ROOT = Path(__file__).resolve().parent / "out"
RAM_ESTIMATE_W = 10.0                    # CodeCarbon's constant RAM estimate
# The tie margins and the tie rule of the rankings (bt_analysis, LeetCode).
TIE = {"latency": BT_TIE["leetcode-99-perf"]["runtime_ms"],
       "energy": BT_TIE["leetcode-99-perf"]["runtime_energy_j"]}


def per_op(case, metric):
    t = case.get("time_us_per_op")
    if metric == "latency":
        return t
    e = case.get("energy_j_per_op")
    if e is None or t is None:
        return None
    e -= RAM_ESTIMATE_W * t * 1e-6       # package energy only, as the paper
    return e if e > 0 else None


def duel(a, b, tie):
    d = math.log(a / b)
    return 0 if abs(d) <= band(tie, None) else (1 if d < 0 else -1)


def main(path):
    cells = defaultdict(list)
    for line in open(path):
        r = json.loads(line)
        if r.get("per_case") and not r.get("error") and not r.get("build_failed"):
            cells[(r["language"], r["title_slug"])].append(r)
    out = {"source": Path(path).name, "budget_s": None, "cells": [], "summary": {}}
    for (lang, slug), recs in sorted(cells.items()):
        out["budget_s"] = recs[0]["budget_s"]
        kept = {k["idx"]: k["weight"] for k in recs[0]["kept"]}
        row = {"language": lang, "problem": slug, "n_llms": len(recs)}
        for metric in ("latency", "energy"):
            vals = {r["model"]: {c["idx"]: per_op(c, metric) for c in r["per_case"]} for r in recs}
            common = set.intersection(*({i for i, v in d.items() if v} for d in vals.values()))
            kc = [i for i in kept if i in common]
            if len(common) < 2 or not kc:
                row[metric] = None
                continue
            wsum = sum(kept[i] for i in kc)
            full = {m: statistics.mean(d[i] for i in common) for m, d in vals.items()}
            red = {m: sum(kept[i] * d[i] for i in kc) / wsum for m, d in vals.items()}
            ms = sorted(vals)
            tau = kendalltau([full[m] for m in ms], [red[m] for m in ms]).statistic
            pairs = list(itertools.combinations(ms, 2))
            out_f = [duel(full[a], full[b], TIE[metric]) for a, b in pairs]
            out_r = [duel(red[a], red[b], TIE[metric]) for a, b in pairs]
            same = sum(f == r for f, r in zip(out_f, out_r)) / len(pairs)
            bias = {m: red[m] / full[m] for m in ms}
            row[metric] = {"tau": tau, "same_duel_share": same, "n_pairs": len(pairs),
                           "n_same": sum(f == r for f, r in zip(out_f, out_r)),
                           "n_reversed": sum(f * r == -1 for f, r in zip(out_f, out_r)),
                           "n_cases_full": len(common),
                           "n_cases_kept": len(kc), "kept_weight_share": wsum,
                           "reduced_over_full": bias}
        out["cells"].append(row)
    for metric in ("latency", "energy"):
        rs = [c[metric] for c in out["cells"] if c.get(metric)]
        if not rs:
            continue
        taus = [r["tau"] for r in rs]
        same = [r["same_duel_share"] for r in rs]
        spread = [max(r["reduced_over_full"].values()) / min(r["reduced_over_full"].values())
                  for r in rs]
        out["summary"][metric] = {
            "n_cells": len(rs), "tau_median": statistics.median(taus), "tau_min": min(taus),
            "tau_mean": statistics.mean(taus), "same_duel_median": statistics.median(same),
            "same_duel_min": min(same),
            "n_duels": sum(r["n_pairs"] for r in rs),
            "same_duel_pooled": sum(r["n_same"] for r in rs) / sum(r["n_pairs"] for r in rs),
            "reversed_pooled": sum(r["n_reversed"] for r in rs) / sum(r["n_pairs"] for r in rs),
            "bias_spread_median": statistics.median(spread)}
    dst = ROOT / "figs" / "data" / "leetcode_workload_check.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["summary"], indent=1))
    for c in out["cells"]:
        lat = c.get("latency") or {}
        print(f"{c['language']:8s} {c['problem']:62s} tau={lat.get('tau', float('nan')):.2f} "
              f"same={lat.get('same_duel_share', float('nan')):.2f} "
              f"cases={lat.get('n_cases_kept')}/{lat.get('n_cases_full')}")
    tab = ROOT / "tables" / "workload_check.tex"
    tab.parent.mkdir(parents=True, exist_ok=True)
    tab.write_text(emit_table(out))
    print(f"wrote {dst.relative_to(ROOT)}, {tab.relative_to(ROOT)}")


LANG_NAME = {"python3": "Python", "cpp": "C++"}


def emit_table(out):
    """Appendix table: per language and pooled, the duels with the same
    outcome, the reversed duels, and Kendall's tau (median and minimum)."""
    rows = []
    langs = sorted({c["language"] for c in out["cells"]}, key=list(LANG_NAME).index)
    for lang in [*langs, None]:                       # None: both languages pooled
        if lang is None:
            rows.append("\\midrule")
        for metric in ("latency", "energy"):
            rs = [c[metric] for c in out["cells"]
                  if c.get(metric) and (lang is None or c["language"] == lang)]
            n = sum(r["n_pairs"] for r in rs)
            taus = [r["tau"] for r in rs]
            rows.append(f"{LANG_NAME.get(lang, 'Both')} & {metric.capitalize()} & {n:,} & "
                        f"{100 * sum(r['n_same'] for r in rs) / n:.1f}\\% & "
                        f"{100 * sum(r['n_reversed'] for r in rs) / n:.1f}\\% & "
                        f"{statistics.median(taus):.2f} ({min(taus):.2f}) \\\\")
    n_cells = len({c["problem"] for c in out["cells"]})
    return "\n".join([
        r"\begin{table}[H]", r"\centering", r"\footnotesize",
        r"\caption{Agreement between the two averages of the workload check, per language, "
        rf"on {n_cells}~random \leetcodeComp problems with the solutions of all "
        r"\totalExecutedModels~\llms. Same: duels with the same outcome under the weighted "
        r"average over the kept test cases and under the average over all profiled test cases, "
        r"with the tie margins of our rankings. Reversed: duels in which a win becomes a loss. "
        r"$\tau$: Kendall's~$\tau$ between the two orders of the \llms in a cell, median and "
        rf"minimum (in parentheses) over the cells.}}",
        r"\label{tab:workload-check}",
        r"\begin{tabular}{@{}l l r r r r@{}}", r"\toprule",
        r"\textbf{Language} & \textbf{Metric} & \textbf{Duels} & \textbf{Same} & "
        r"\textbf{Reversed} & $\boldsymbol{\tau}$ \\", r"\midrule",
        *rows, r"\bottomrule", r"\end{tabular}", r"\end{table}", ""])


if __name__ == "__main__":
    main(sys.argv[1])
