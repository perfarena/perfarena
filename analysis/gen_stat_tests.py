#!/usr/bin/env python3
"""Statistical tests over the per-problem efficiency of the evaluated models.

    # fetch the per-cell measurements, cache them, and run the tests
    python3 analysis/gen_stat_tests.py --api-key "$ARENA_KEY"
    # re-run the tests from the cached measurements only
    python3 analysis/gen_stat_tests.py

For each competition and each metric (latency, energy, peak memory), every
(language, problem) cell is a block and every model is a treatment.
  * Friedman test over the cells that every model solved (H0: every model has
    the same distribution of within-cell ranks), with Kendall's W as its effect
    size (0 = no agreement across cells, 1 = the same ranking in every cell).
  * Pairwise Wilcoxon signed-rank tests over the cells that both models of a
    pair solved, on the log of their value ratio (scale-free across cells),
    with Holm's step-down correction over all pairs (Benavoli et al. 2016
    recommend this over mean-rank post-hoc tests; it also uses every cell a
    pair shares, not only the cells that all models solved).
  * Permutation test on the agreement between languages: the mean Kendall tau
    between the per-language rankings, against the same statistic after the
    language of each problem's measurements is shuffled.
Ranks and ratios inside a cell do not depend on the golden baseline, because
all models in a cell share the same denominator.

Outputs:
  figs/data/<comp>_cells.json   cached per-cell, per-model correct values
  figs/data/stat_tests.json     test results
  tables/stat_tests.tex         summary table for the paper
"""
from __future__ import annotations
import argparse, json, math, os, sys
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy.stats import friedmanchisquare, rankdata, wilcoxon

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_paper_data as gpd  # noqa: E402  (reuse LANGS, roster, API client)

ROOT = HERE / "out"
COMPS = [("leetcode-99-perf", r"\leetcodeComp"), ("clbg-10", r"\clbgComp")]
METRICS = [("runtime_ms", "Latency"), ("runtime_energy_j", "Energy"),
           ("memory_mb", "Peak memory")]
ALPHA = 0.05
MIN_PAIRED = 6          # fewer non-zero paired differences -> no test


def fetch_cells(base, key, comp, models):
    """{lang: {problem: {model: {metric: value}}}} for correct solutions only."""
    cells = {}
    for slug, _ in gpd.competition_languages(base, key, comp, models):
        data = gpd.fetch_compare(base, key, comp, slug, models)
        for p in data.get("problems", []):
            pm = p.get("per_model", {})
            row = {m: {mk: pm[m].get(mk) for mk, _ in METRICS}
                   for m in models if pm.get(m) and pm[m].get("accepted")}
            cells.setdefault(slug, {})[p["problem_slug"]] = row
    return cells


def _value(row, m, mk):
    v = (row.get(m) or {}).get(mk)
    return v if v is not None and v > 0 else None


def friedman(cells, models, mk):
    X = np.array([[_value(row, m, mk) for m in models]
                  for slug in cells for row in cells[slug].values()
                  if all(_value(row, m, mk) for m in models)], dtype=float)
    n, k = X.shape
    chi2, p = friedmanchisquare(*X.T)
    mean_rank = np.apply_along_axis(rankdata, 1, X).mean(axis=0)
    return dict(n_cells=int(n), chi2=float(chi2), p=float(p),
                W=float(chi2 / (n * (k - 1))),
                mean_rank={models[i]: round(float(mean_rank[i]), 3)
                           for i in np.argsort(mean_rank)})


def pairwise_wilcoxon(cells, models, mk):
    tests = []
    for a, b in combinations(models, 2):
        d = [math.log(_value(row, a, mk) / _value(row, b, mk))
             for slug in cells for row in cells[slug].values()
             if _value(row, a, mk) and _value(row, b, mk)]
        nz = [x for x in d if x != 0]
        p = float(wilcoxon(nz).pvalue) if len(nz) >= MIN_PAIRED else 1.0
        tests.append(dict(a=a, b=b, n=len(d), p=p,
                          median_log_ratio=float(np.median(d)) if d else 0.0))
    # Holm: walk the p-values in ascending order, reject while p <= alpha/(m-i)
    m = len(tests)
    for i, t in enumerate(sorted(tests, key=lambda t: t["p"])):
        if t["p"] > ALPHA / (m - i):
            break
        t["reject"] = True
    beats = {mo: [] for mo in models}
    for t in tests:
        t.setdefault("reject", False)
        if t["reject"]:   # the model with the lower value wins the pair
            win, lose = (t["a"], t["b"]) if t["median_log_ratio"] < 0 else (t["b"], t["a"])
            beats[win].append(lose)
    beaten_by = {mo: sorted(o for o in models if mo in beats[o]) for mo in models}
    return dict(sig_pairs=sum(t["reject"] for t in tests), n_pairs=m,
                median_common=int(np.median([t["n"] for t in tests])),
                min_common=int(min(t["n"] for t in tests)),
                undefeated=[mo for mo in models if not beaten_by[mo]],
                beats={mo: sorted(v) for mo, v in beats.items()},
                beaten_by=beaten_by, tests=tests)


def holm(pvals):
    """Holm step-down: return a reject flag per p-value at ALPHA."""
    m, reject = len(pvals), [False] * len(pvals)
    for i, j in enumerate(sorted(range(m), key=lambda j: pvals[j])):
        if pvals[j] > ALPHA / (m - i):
            break
        reject[j] = True
    return reject


def per_language_friedman(cells, models, mk, min_cells=5):
    """Friedman test inside each language, over the problems that every model
    solved in that language. Languages with fewer than min_cells such
    problems are not tested."""
    out = {}
    for slug in cells:
        rows = [row for row in cells[slug].values()
                if all(_value(row, m, mk) for m in models)]
        if len(rows) < min_cells:
            out[slug] = dict(n_cells=len(rows), tested=False)
            continue
        X = np.array([[_value(r, m, mk) for m in models] for r in rows])
        chi2, p = friedmanchisquare(*X.T)
        mr = np.apply_along_axis(rankdata, 1, X).mean(axis=0)
        out[slug] = dict(n_cells=len(rows), tested=True, chi2=float(chi2), p=float(p),
                         W=float(chi2 / (len(rows) * (len(models) - 1))),
                         leader=models[int(np.argmin(mr))])
    return out


def _language_scores(cells, models, mk, assign):
    """Per-language geometric-mean ratio of every model. `assign[(lang, p)]`
    is the language that the measurements of cell (lang, p) are credited to."""
    logs = {s: {m: [] for m in models} for s in cells}
    for s, probs in cells.items():
        for p, row in probs.items():
            vals = {m: _value(row, m, mk) for m in models if _value(row, m, mk)}
            if not vals:
                continue
            best = min(vals.values())
            for m, v in vals.items():
                logs[assign[(s, p)]][m].append(math.log(v / best))
    return {s: {m: (sum(v) / len(v) if v else None) for m, v in d.items()}
            for s, d in logs.items()}


def _mean_tau(scores, models):
    taus = []
    for a, b in combinations(list(scores), 2):
        common = [m for m in models if scores[a][m] is not None and scores[b][m] is not None]
        if len(common) >= 3:
            taus.append(gpd.kendall_tau_b([scores[a][m] for m in common],
                                          [scores[b][m] for m in common]))
    return sum(taus) / len(taus)


def language_permutation(cells, models, mk, n_perm=1000, seed=0):
    """Do the differences between models depend on the language?
    Observed: mean Kendall tau between the per-language rankings. Null: the
    same statistic after shuffling, inside each problem, which language each
    cell's measurements belong to, so the language cannot matter. A one-sided
    p-value below ALPHA means two languages agree LESS than chance allows."""
    rng = np.random.default_rng(seed)
    langs = list(cells)
    ident = {(s, p): s for s in langs for p in cells[s]}
    obs = _mean_tau(_language_scores(cells, models, mk, ident), models)
    problems = {}
    for s in langs:
        for p in cells[s]:
            problems.setdefault(p, []).append(s)
    null = []
    for _ in range(n_perm):
        assign = {}
        for p, ls in problems.items():
            for s, d in zip(ls, rng.permutation(ls)):
                assign[(s, p)] = str(d)
        null.append(_mean_tau(_language_scores(cells, models, mk, assign), models))
    null = np.array(null)
    return dict(tau=float(obs), tau_chance=float(null.mean()),
                tau_chance_95=[float(np.percentile(null, 2.5)), float(np.percentile(null, 97.5))],
                p=float((np.sum(null <= obs) + 1) / (n_perm + 1)), n_perm=n_perm)


def fmt_p(p):
    if p < 1e-6:
        return r"$<10^{-6}$"
    return r"$<0.001$" if p < 0.001 else f"${p:.3f}$"


def emit_table(results):
    lines = [r"\begin{table}[H]", r"\centering", r"\footnotesize",
             r"\setlength{\tabcolsep}{4pt}",
             r"\caption{Statistical tests of the differences between \llms. "
             r"Friedman test ($\chi^2_F$, $p$) and Kendall's~$W$ over the cells "
             r"that every \llm solved, \llm pairs that differ in Holm-corrected "
             r"Wilcoxon signed-rank tests (Sig.\ pairs), at $\alpha = 0.05$.}",
             r"\label{tab:stat-tests}",
             r"\begin{tabular}{@{}l r r r r r@{}}", r"\toprule",
             r"\textbf{Metric} & \textbf{Cells} & "
             r"$\boldsymbol{\chi^2_F}$ & $\boldsymbol{p}$ & $\boldsymbol{W}$ & "
             r"\textbf{Sig.\ pairs} \\"]
    for comp, macro in COMPS:
        lines += [r"\midrule", rf"\multicolumn{{6}}{{@{{}}l}}{{{macro}}} \\"]
        for mk, name in METRICS:
            f, w = results[comp][mk]["friedman"], results[comp][mk]["wilcoxon"]
            lines.append(
                f"{name} & {f['n_cells']} & "
                f"{f['chi2']:.1f} & {fmt_p(f['p'])} & {f['W']:.2f} & "
                f"{w['sig_pairs']}/{w['n_pairs']} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    (ROOT / "tables" / "stat_tests.tex").write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("ARENA_BASE", "http://offline.invalid"))
    ap.add_argument("--api-key", default=os.environ.get("ARENA_KEY", ""))
    ap.add_argument("--refresh", action="store_true",
                    help="rebuild the cell cache from the campaign submissions")
    args = ap.parse_args()
    models = [m for m in gpd.DEFAULT_MODELS if m not in gpd.EXCLUDE_MODELS]
    (ROOT / "figs" / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "tables").mkdir(parents=True, exist_ok=True)

    results = {}
    for comp, _ in COMPS:
        cache = ROOT / "figs" / "data" / f"{comp}_cells.json"
        if args.refresh or args.api_key:
            cells = fetch_cells(args.base_url, args.api_key, comp, models)
            cache.write_text(json.dumps(cells, indent=1, sort_keys=True))
        elif cache.exists():
            cells = json.loads(cache.read_text())
        else:
            sys.exit(f"no cache {cache}; pass --refresh")
        results[comp] = {}
        for mk, name in METRICS:
            f = friedman(cells, models, mk)
            w = pairwise_wilcoxon(cells, models, mk)
            pl = per_language_friedman(cells, models, mk)
            it = language_permutation(cells, models, mk)
            results[comp][mk] = dict(friedman=f, wilcoxon=w, per_language=pl,
                                     language=it)
            print(f"[{comp}] {name:12s} cells={f['n_cells']:4d} chi2={f['chi2']:7.1f} "
                  f"p={f['p']:.1e} W={f['W']:.3f} | pairs={w['sig_pairs']}/{w['n_pairs']} "
                  f"(shared cells median {w['median_common']}, min {w['min_common']})")
            print(f"    undefeated: {[gpd.disp(m) for m in w['undefeated']]}")
            ranked = sorted(models, key=lambda m: (-len(w['beats'][m]), len(w['beaten_by'][m])))
            print("    beats/beaten-by: " + ", ".join(
                f"{gpd.disp(m)} {len(w['beats'][m])}/{len(w['beaten_by'][m])}" for m in ranked))
            print(f"    language: mean tau={it['tau']:.2f}, chance={it['tau_chance']:.2f} "
                  f"(95% {it['tau_chance_95'][0]:.2f}..{it['tau_chance_95'][1]:.2f}), p={it['p']:.3f}")
            print("    per-language: " + ", ".join(
                f"{s}:n={v['n_cells']}" + (f",p={v['p']:.1e},W={v['W']:.2f},lead={gpd.disp(v['leader'])}"
                                         if v['tested'] else ",untested")
                for s, v in pl.items()))
        # Holm over every within-language test of this competition
        keys = [(mk, s) for mk, _ in METRICS
                for s, v in results[comp][mk]["per_language"].items() if v["tested"]]
        rej = holm([results[comp][mk]["per_language"][s]["p"] for mk, s in keys])
        for (mk, s), r in zip(keys, rej):
            results[comp][mk]["per_language"][s]["holm_reject"] = r
        print(f"[{comp}] within-language Friedman significant after Holm: "
              f"{sum(rej)}/{len(rej)} " + str({mk: [s for (m2, s), r in zip(keys, rej) if m2 == mk and r]
                                              for mk, _ in METRICS}))
    (ROOT / "figs" / "data" / "stat_tests.json").write_text(json.dumps(results, indent=1))
    emit_table(results)
    print("wrote figs/data/stat_tests.json, tables/stat_tests.tex")


if __name__ == "__main__":
    main()
