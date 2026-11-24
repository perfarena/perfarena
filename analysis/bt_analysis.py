#!/usr/bin/env python3
"""Bradley-Terry analysis of the campaign: the rankings, tests, and numbers of
the evaluation (RQ1-RQ3). make_bt_tables.py, make_rank_bump.py --bt, and
make_pareto_levels.py --bt render its output for the paper.

    python3 analysis/bt_analysis.py        # writes figs/data/bt_results.json

Duels. Every (language, problem) cell that two LLMs both solved is one duel per
metric. The lower value wins. Two values within the measurement noise tie, and
a tie gives half a win to each LLM. The noise of a difference of two values is
sqrt(2) times the noise of one value (TIE): the 95% confidence interval of a
7-run mean of the metric (latency 1.2% on LeetCode and 2.6% on CLBG, energy 2.6%
and 2.5%, from the setup section) for latency and energy, and, for memory, the 95th percentile (nearest rank) of the
single-reading deviation in the memory pilot of the same language (MEM_NOISE,
from bicho-20261023/pilot_mem5.jsonl). A failed cell gives no duel, so
failing a problem neither helps nor hurts a performance ranking (pass@1
reports it).

Model. Bradley-Terry, as in Chatbot Arena (Chiang et al., ICML 2024): each LLM
has a strength p_i, and P(i beats j) = p_i / (p_i + p_j). The fit uses the
MM algorithm (Hunter 2004) with a pseudo-count of half a tied duel per pair,
which only matters when a pair has almost no duels. Ratings use the Elo scale
(1000 = average LLM, 400 points = 10:1 odds). The paper reports the win
probability: an LLM's mean chance of beating one of the other LLMs on a cell.

Uncertainty. B bootstrap samples give 95% intervals of the ratings and ranks
and a Wald test per pair (bootstrap standard error), Holm-corrected over the
78 pairs. The cells of one problem in different languages are correlated, so
both competitions resample whole problems (99 clusters on LeetCode, 10 on
CLBG). The resampling of cells is stored too, for comparison. Every
analysis draws from its own random stream (SeedSequence keyed by its name), so
changing one analysis does not change the others.

Per language. An LLM with fewer than MIN_CELLS solved cells (cells with at
least one duel) in a language gets no rating there.

Sensitivity. The rankings with strict comparisons (no tie margin).
"""
from __future__ import annotations

import json
import math
import sys
import zlib
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy.stats import norm, spearmanr

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_paper_data as gpd  # noqa: E402

ROOT = HERE / "out"
COMPS = ["leetcode-99-perf", "clbg-10"]
METRICS = ["runtime_ms", "runtime_energy_j", "memory_mb"]
NAME = {"runtime_ms": "latency", "runtime_energy_j": "energy", "memory_mb": "memory"}
_S2 = math.sqrt(2)
# 95th percentile (nearest rank) of |reading - mean| / mean over the 5 readings of
# each of 20 gpt-6-astra solutions per language in the memory pilot.
MEM_NOISE = {"cpp": 0.0197, "csharp": 0.0695, "java": 0.0574, "javascript": 0.0109,
             "php": 0.0070, "python3": 0.0167, "typescript": 0.0052}
MEM_TIE = {lang: noise * _S2 for lang, noise in MEM_NOISE.items()}
# A margin is one number for every language, or a {language: margin} dict.
TIE = {"leetcode-99-perf": {"runtime_ms": 0.012 * _S2, "runtime_energy_j": 0.026 * _S2,
                            "memory_mb": MEM_TIE},
       "clbg-10": {"runtime_ms": 0.026 * _S2, "runtime_energy_j": 0.025 * _S2,
                   "memory_mb": MEM_TIE}}


def band(margin, lang):
    """Log-ratio half-width within which two values of `lang` tie."""
    return math.log1p(margin[lang] if isinstance(margin, dict) else margin)
UNIT = {"leetcode-99-perf": "problems", "clbg-10": "problems"}   # significance resampling
N_CELLS = {"leetcode-99-perf": 693, "clbg-10": 70}           # cells per LLM (pass@1 base)
ALPHA, B, N_PERM, SEED, MIN_CELLS = 0.05, 10_000, 1000, 0, 3
ASTRA = "gpt-6-astra"


def short(m):
    return m.split("/")[-1]


def value(row, m, mk):
    v = (row.get(m) or {}).get(mk)
    return v if v is not None and v > 0 else None


def stream(*key):
    """An independent random generator per named analysis."""
    return np.random.default_rng(np.random.SeedSequence(
        [SEED] + [zlib.crc32(str(k).encode()) for k in key]))


def holm(pvals):
    m, reject = len(pvals), [False] * len(pvals)
    for i, j in enumerate(sorted(range(m), key=lambda j: pvals[j])):
        if pvals[j] > ALPHA / (m - i):
            break
        reject[j] = True
    return reject


# ---- duels and the Bradley-Terry fit --------------------------------------------

def duel_matrices(cells, models, mk, margin):
    """Per cell: (language, problem, W) with W[i, j] = wins of i over j."""
    k = len(models)
    out = []
    for lang, probs in cells.items():
        b = band(margin, lang)
        for prob, row in probs.items():
            v = [value(row, m, mk) for m in models]
            W = np.zeros((k, k))
            for i, j in combinations(range(k), 2):
                if v[i] is None or v[j] is None:
                    continue
                d = math.log(v[i] / v[j])
                if abs(d) <= b:
                    W[i, j] += 0.5
                    W[j, i] += 0.5
                elif d < 0:
                    W[i, j] += 1
                else:
                    W[j, i] += 1
            out.append((lang, prob, W))
    return out


def fit(W, pseudo=0.5, start=None, tol=1e-9, iters=20000):
    """Elo-scale Bradley-Terry ratings from a win matrix (MM algorithm). `start`
    (ratings) warm-starts the iteration; the fixed point does not depend on it."""
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


def win_prob(ra, rb):
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def avg_win_prob(rating):
    """Each LLM's mean chance of beating one of the other rated LLMs on a cell."""
    rated = {m: r for m, r in rating.items() if r is not None}
    return {m: (sum(win_prob(rated[m], rated[o]) for o in rated if o != m) / (len(rated) - 1)
                if m in rated else None) for m in rating}


def bootstrap(units, rng, start):
    """Ratings of B resamples of `units` (k x k win matrices)."""
    U = np.stack(units)
    counts = rng.multinomial(len(U), np.full(len(U), 1 / len(U)), size=B)
    return np.array([fit(np.tensordot(c, U, axes=1), start=start) for c in counts])


def pair_tests(models, point, samples):
    pairs = list(combinations(range(len(models)), 2))
    pv = []
    for i, j in pairs:
        se = np.std(samples[:, i] - samples[:, j], ddof=1)
        z = (point[i] - point[j]) / se if se > 0 else 0.0
        pv.append(float(2 * norm.sf(abs(z))))
    beats = {m: [] for m in models}
    for (i, j), r in zip(pairs, holm(pv)):
        if r:
            win, lose = (i, j) if point[i] > point[j] else (j, i)
            beats[models[win]].append(models[lose])
    undefeated = [m for m in models if not any(m in beats[o] for o in models)]
    return beats, sum(len(v) for v in beats.values()), undefeated


def boot_summary(models, point, samples):
    beats, n_sig, undefeated = pair_tests(models, point, samples)
    lo, hi = np.percentile(samples, [2.5, 97.5], axis=0)
    ranks = (-samples).argsort(1).argsort(1) + 1
    rlo = np.percentile(ranks, 2.5, axis=0, method="lower")
    rhi = np.percentile(ranks, 97.5, axis=0, method="higher")
    return dict(beats=beats, sig_pairs=n_sig, undefeated=undefeated,
                ci={m: [float(lo[i]), float(hi[i])] for i, m in enumerate(models)},
                rank_ci={m: [int(rlo[i]), int(rhi[i])] for i, m in enumerate(models)})


def omnibus(duels, rng, n_perm=N_PERM):
    """Do the LLMs differ at all? The spread (standard deviation) of the
    ratings, against the same statistic after shuffling, inside each cell, which
    LLM produced which solution (the LLMs that solved the cell stay the same)."""
    W0 = sum(d[2] for d in duels)
    point = fit(W0)
    obs = float(np.std(point))
    units = []
    for _, _, Wc in duels:
        idx = np.flatnonzero((Wc + Wc.T).sum(1) > 0)
        units.append((idx, Wc[np.ix_(idx, idx)]))
    k = W0.shape[0]
    null = []
    for _ in range(n_perm):
        W = np.zeros((k, k))
        for idx, sub in units:
            perm = rng.permutation(idx)
            W[np.ix_(perm, perm)] += sub
        null.append(np.std(fit(W)))
    return dict(sd=obs, p=float((np.sum(np.array(null) >= obs) + 1) / (n_perm + 1)))


def analyse(cells, models, mk, comp):
    duels = duel_matrices(cells, models, mk, TIE[comp][mk])
    W = sum(d[2] for d in duels)
    point = fit(W)
    by_prob = {}
    for _, prob, Wc in duels:
        by_prob[prob] = by_prob.get(prob, 0) + Wc
    units = {"cells": [d[2] for d in duels], "problems": list(by_prob.values())}
    boot = {u: boot_summary(models, point, bootstrap(U, stream(comp, mk, "boot", u), point))
            for u, U in units.items()}
    prim = boot[UNIT[comp]]
    rating = {m: float(point[i]) for i, m in enumerate(models)}
    out = dict(rating=rating, win_prob=avg_win_prob(rating), unit=UNIT[comp],
               beats=prim["beats"], sig_pairs=prim["sig_pairs"], undefeated=prim["undefeated"],
               ci=prim["ci"], rank_ci=prim["rank_ci"],
               boot={u: dict(sig_pairs=b["sig_pairs"], undefeated=b["undefeated"])
                     for u, b in boot.items()},
               duels_per_pair=int(np.median([(W + W.T)[i, j] for i, j in
                                             combinations(range(len(models)), 2)])),
               omnibus=omnibus(duels, stream(comp, mk, "omnibus")))
    strict = fit(sum(d[2] for d in duel_matrices(cells, models, mk, 0.0)))
    out["strict_rating"] = {m: float(strict[i]) for i, m in enumerate(models)}
    return out, duels


# ---- per-language rankings ---------------------------------------------------------

def eligible(units, k):
    """Indices of the LLMs with at least MIN_CELLS cells that have a duel."""
    n = sum(((U + U.T).sum(1) > 0).astype(int) for U in units)
    return np.flatnonzero(n >= MIN_CELLS), n


def fit_group(units, k):
    """Ratings of the eligible LLMs of a group of cells (NaN for the others)."""
    idx, _ = eligible(units, k)
    r = np.full(k, np.nan)
    if len(idx) >= 2:
        r[idx] = fit(sum(U[np.ix_(idx, idx)] for U in units))
    return r


def per_language(duels, models, comp, mk):
    """Inside each language: ratings, average win probabilities, and the
    leading group (no other rated LLM beats it significantly, Holm over the
    pairs of rated LLMs). The cells of one language are its problems, so this
    bootstrap resamples problems."""
    out, k = {}, len(models)
    for lang in dict.fromkeys(d[0] for d in duels):
        units = [Wc for l, _, Wc in duels if l == lang]
        idx, n = eligible(units, k)
        assert len(idx) >= 2, f"{comp} {mk} {lang}: fewer than two rated LLMs"
        sub_models = [models[i] for i in idx]
        sub = [U[np.ix_(idx, idx)] for U in units]
        point = fit(sum(sub))
        beats, n_sig, lead = pair_tests(sub_models, point,
                                        bootstrap(sub, stream(comp, mk, "lang", lang), point))
        rating = {m: None for m in models}
        rating.update({m: float(point[i]) for i, m in enumerate(sub_models)})
        wp = avg_win_prob(rating)
        out[lang] = dict(win_prob=wp, leader=max(sub_models, key=lambda m: wp[m]),
                         lead_group=lead, sig_pairs=n_sig, n_pairs=len(idx) * (len(idx) - 1) // 2,
                         beats=beats, n_cells={m: int(n[i]) for i, m in enumerate(models)})
    return out


def mean_tau(scores, models):
    taus = []
    for a, b in combinations(list(scores), 2):
        common = [m for m in models if not math.isnan(scores[a][m])
                  and not math.isnan(scores[b][m])]
        if len(common) >= 3:
            taus.append(gpd.kendall_tau_b([scores[a][m] for m in common],
                                          [scores[b][m] for m in common]))
    return sum(taus) / len(taus) if taus else float("nan")


def language_ratings(duels, models, assign=None):
    """{language: {model: rating or NaN}} with each cell credited to
    assign[(lang, prob)]."""
    groups = {}
    for lang, prob, Wc in duels:
        groups.setdefault(assign[(lang, prob)] if assign else lang, []).append(Wc)
    return {g: dict(zip(models, fit_group(U, len(models)))) for g, U in groups.items()}


def language_permutation(duels, models, rng):
    obs = mean_tau(language_ratings(duels, models), models)
    problems = {}
    for lang, prob, _ in duels:
        problems.setdefault(prob, []).append(lang)
    null = []
    for _ in range(N_PERM):
        assign = {}
        for prob, ls in problems.items():
            for s, d in zip(ls, rng.permutation(ls)):
                assign[(s, prob)] = str(d)
        null.append(mean_tau(language_ratings(duels, models, assign), models))
    null = np.array(null)
    return dict(tau=float(obs), tau_chance=float(null.mean()),
                p=float((np.sum(null <= obs) + 1) / (N_PERM + 1)))


# ---- sizes and comparisons ----------------------------------------------------------

def current_scores(comp, models):
    """Geometric-mean ratio to the golden baseline over each LLM's solved cells
    (figs/data/<comp>_ratios.json), lower is better; for comparison only."""
    r = json.loads((ROOT / "figs" / "data" / f"{comp}_ratios.json").read_text())
    by_short = {short(m): m for m in models}
    return {mk: {by_short[s]: v for s, v in zip(r["models"], r[key])}
            for mk, key in (("runtime_ms", "mean_runtime"), ("runtime_energy_j", "mean_energy"),
                            ("memory_mb", "mean_memory"))}


def vs_astra(cells, models, mk, margin):
    """For each LLM: the geometric-mean ratio LLM/astra over the cells that both
    solved, and astra's wins, ties, and losses there under the tie margin."""
    out = {}
    for m in models:
        if m == ASTRA:
            continue
        logs = [(math.log(value(r, m, mk) / value(r, ASTRA, mk)), band(margin, lang))
                for lang, probs in cells.items() for r in probs.values()
                if value(r, m, mk) and value(r, ASTRA, mk)]
        out[m] = dict(n=len(logs), ratio=math.exp(np.mean([x for x, _ in logs])),
                      astra_wins=float(np.mean([x > b for x, b in logs])),
                      ties=float(np.mean([abs(x) <= b for x, b in logs])),
                      astra_loses=float(np.mean([x < -b for x, b in logs])))
    return out


def pareto(models, better):
    """better(mk, a, b) -> -1 (a better), 0 (tie), +1 (a worse)."""
    def dom(a, b):
        c = [better(mk, a, b) for mk in METRICS]
        return all(x <= 0 for x in c) and any(x < 0 for x in c)
    level, left, lv = {}, set(models), 1
    while left:
        top = {m for m in left if not any(dom(o, m) for o in left if o != m)}
        assert top, "dominance has a cycle"
        for m in top:
            level[m] = lv
        left -= top
        lv += 1
    return dict(levels=max(level.values()), level=level,
                level1=sorted(m for m in models if level[m] == 1),
                astra_dominates=sum(dom(ASTRA, m) for m in models if m != ASTRA))


# ---- cost and correctness ------------------------------------------------------------

def cost_rows(comp, solved):
    """{model: {pass1, tok_att, usd_att, tok_sol, usd_sol}} from the raw campaign
    totals (figs/data/<comp>_cost.json), not the rounded LaTeX table."""
    pm = json.loads((ROOT / "figs" / "data" / f"{comp}_cost.json").read_text())["per_model"]
    return {m: dict(pass1=100 * solved[m] / N_CELLS[comp],
                    tok_att=v["tokens"] / v["n_sols"], usd_att=v["cost"] / v["n_sols"],
                    tok_sol=v["tokens"] / solved[m], usd_sol=v["cost"] / solved[m])
            for m, v in pm.items()}


def correlations(results, models):
    """Spearman tests with Holm over each family of 4 tests."""
    fams = {
        "correctness": [(c, mk, "pass1") for c in COMPS for mk in METRICS[:2]],
        "cost_per_attempt": [(c, "runtime_ms", f) for c in COMPS for f in ("tok_att", "usd_att")],
        "cost_per_correct": [(c, "runtime_ms", f) for c in COMPS for f in ("tok_sol", "usd_sol")],
        "memory_cost_per_attempt": [(c, "memory_mb", f) for c in COMPS for f in ("tok_att", "usd_att")],
    }
    out = {}
    for fam, tests in fams.items():
        rows = []
        for comp, mk, f in tests:
            x = [results[comp]["cost"][m][f] for m in models]
            y = [results[comp]["primary"][mk]["rating"][m] for m in models]
            rho, p = spearmanr(x, y)
            rows.append(dict(comp=comp, metric=NAME[mk], x=f, rho=float(rho), p=float(p)))
        for r, rej in zip(rows, holm([r["p"] for r in rows])):
            r["holm"] = rej
        out[fam] = rows
    return out


# ---- main -----------------------------------------------------------------------------

def main():
    models = [m for m in gpd.DEFAULT_MODELS if m not in gpd.EXCLUDE_MODELS]
    results = {}
    for comp in COMPS:
        cells = json.loads((ROOT / "figs" / "data" / f"{comp}_cells.json").read_text())
        solved = {m: sum(1 for probs in cells.values() for row in probs.values() if m in row)
                  for m in models}
        res = {"primary": {}, "language": {}, "per_language": {}, "vs_astra": {},
               "current": current_scores(comp, models), "cost": cost_rows(comp, solved),
               "solved": solved}
        for mk in METRICS:
            a, duels = analyse(cells, models, mk, comp)
            res["primary"][mk] = a
            res["language"][mk] = language_permutation(duels, models, stream(comp, mk, "langperm"))
            res["per_language"][mk] = per_language(duels, models, comp, mk)
            res["vs_astra"][mk] = vs_astra(cells, models, mk, TIE[comp][mk])
        for mk, rej in zip(METRICS, holm([res["primary"][mk]["omnibus"]["p"] for mk in METRICS])):
            res["primary"][mk]["omnibus"]["holm"] = rej
        P = res["primary"]
        res["pareto"] = pareto(models, lambda mk, a, b: (
            -1 if b in P[mk]["beats"][a] else 1 if a in P[mk]["beats"][b] else 0))
        rl, re_ = P["runtime_ms"]["rating"], P["runtime_energy_j"]["rating"]
        res["energy_latency_rho"] = float(spearmanr([rl[m] for m in models],
                                                    [re_[m] for m in models])[0])
        results[comp] = res
    results["correlations"] = correlations(results, models)
    results["settings"] = dict(tie=TIE, unit=UNIT, bootstrap=B, permutations=N_PERM,
                               seed=SEED, pseudo_count=0.5, min_cells=MIN_CELLS)
    out = ROOT / "figs" / "data" / "bt_results.json"
    out.write_text(json.dumps(results, indent=1, default=float))
    report(results, models)
    print(f"\nwrote {out.relative_to(ROOT)}")


def report(R, models):
    for comp in COMPS:
        res = R[comp]
        print(f"\n=== {comp} (significance resamples {UNIT[comp]}) ===")
        for mk in METRICS:
            a, cur = res["primary"][mk], res["current"][mk]
            wp = a["win_prob"]
            order = sorted(models, key=lambda m: -wp[m])
            cur_rank = {m: i + 1 for i, m in enumerate(sorted(models, key=lambda m: cur[m]))}
            t = TIE[comp][mk]
            t = (f"{t:.2%}" if not isinstance(t, dict)
                 else ", ".join(f"{l} {v:.2%}" for l, v in sorted(t.items())))
            print(f"\n{NAME[mk]}  (tie margin {t}, median duels/pair "
                  f"{a['duels_per_pair']}, omnibus p={a['omnibus']['p']:.4f} "
                  f"holm={'yes' if a['omnibus']['holm'] else 'no'})")
            for i, m in enumerate(order, 1):
                lo, hi = a["ci"][m]
                print(f"  {i:2} {short(m):20} win {100 * wp[m]:5.1f}%  Elo {a['rating'][m]:6.0f} "
                      f"[{lo:5.0f},{hi:5.0f}] rank {a['rank_ci'][m][0]}-{a['rank_ci'][m][1]}"
                      f"  | geomean rank {cur_rank[m]}{'  *lead' if m in a['undefeated'] else ''}")
            print(f"  sig pairs: cells {a['boot']['cells']['sig_pairs']}/78, problems "
                  f"{a['boot']['problems']['sig_pairs']}/78 (primary: {a['unit']})")
            for u in ("cells", "problems"):
                print(f"  undefeated ({u}): {[short(m) for m in a['boot'][u]['undefeated']]}")
            tau_s = gpd.kendall_tau_b([a["rating"][m] for m in models],
                                      [a["strict_rating"][m] for m in models])
            print(f"  Kendall tau with strict ties: {tau_s:.3f}")
            L = res["language"][mk]
            print(f"  language agreement: tau={L['tau']:.2f} vs chance {L['tau_chance']:.2f}, "
                  f"p={L['p']:.3f}")
            PL = res["per_language"][mk]
            in_lead = {}
            for lang, d in PL.items():
                for m in d["lead_group"]:
                    in_lead[short(m)] = in_lead.get(short(m), 0) + 1
                hidden = [short(m) for m in models if d["win_prob"][m] is None]
                print(f"    {lang:11} leader {short(d['leader']):19} "
                      f"{100 * d['win_prob'][d['leader']]:.0f}% | lead {len(d['lead_group']):2} "
                      f"| sig {d['sig_pairs']:2}/{d['n_pairs']}" + (f" | hidden {hidden}" if hidden else ""))
            print(f"  languages in the lead group: "
                  f"{dict(sorted(in_lead.items(), key=lambda x: -x[1]))}")
        print(f"\n  energy~latency rho: {res['energy_latency_rho']:.3f}")
        P = res["pareto"]
        print(f"  Pareto: levels={P['levels']}, astra dominates {P['astra_dominates']}/12, "
              f"Level 1: {[short(m) for m in P['level1']]}")
        lv = {}
        for m, l in P["level"].items():
            lv.setdefault(l, []).append(short(m))
        print("   levels:", {k: sorted(v) for k, v in sorted(lv.items())})
        for mk in METRICS:
            va = res["vs_astra"][mk]
            print(f"  {NAME[mk]} vs astra (ratio, astra wins/ties/loses, n): " + ", ".join(
                f"{short(m)} {v['ratio']:.2f}x {100 * v['astra_wins']:.0f}/{100 * v['ties']:.0f}/"
                f"{100 * v['astra_loses']:.0f} {v['n']}"
                for m, v in sorted(va.items(), key=lambda x: x[1]["ratio"])))
    print("\n=== correlations (Spearman, Holm within each family of 4) ===")
    for fam, rows in R["correlations"].items():
        print(f"  {fam}:")
        for r in rows:
            print(f"    {r['comp']:17} {r['metric']:8} ~ {r['x']:8} rho={r['rho']:+.3f} "
                  f"p={r['p']:.4f} holm={'yes' if r['holm'] else 'no'}")


if __name__ == "__main__":
    main()
