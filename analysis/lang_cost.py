#!/usr/bin/env python3
"""Generation cost per language (RQ3): for each competition and language, the
cost of a correct solution in USD and in response tokens, as the median over
the LLMs and pooled over them, plus a Friedman test over the LLMs (blocks) of
whether they order the languages consistently by cost.

    PERFARENA_CACHE=<cache> python3 analysis/lang_cost.py

Uses the same campaign submissions and languages as gen_paper_data.py.
"""
import os
import statistics
import sys

from scipy.stats import friedmanchisquare

sys.path.insert(0, os.path.dirname(__file__))
import gen_paper_data as g  # noqa: E402

BASE = os.environ.get("ARENA_BASE", "http://offline.invalid")
models = [m for m in g.DEFAULT_MODELS if m not in g.EXCLUDE_MODELS]

for comp in ("leetcode-99-perf", "clbg-10"):
    langs = [s for s, _ in g.competition_languages(BASE, "", comp, models)]
    sel = g.campaign_submissions(BASE, "", comp, models)
    per = {}   # (model, lang) -> (usd, tokens, correct solutions)
    for (m, l), (s, atts) in sel.items():
        if l in langs:
            t = g.campaign_totals(BASE, "", s)
            per[m, l] = (t["cost"], t["tok"],
                         sum(1 for a in atts.values() if a.get("accepted")))
    print("==", comp, langs)
    rows = []
    for l in langs:
        usd, tok, cor = (sum(per[m, l][i] for m in models) for i in range(3))
        solved = [m for m in models if per[m, l][2]]
        rows.append((l, usd / cor, tok / cor,
                     statistics.median(per[m, l][0] / per[m, l][2] for m in solved),
                     statistics.median(per[m, l][1] / per[m, l][2] for m in solved)))
    for l, pu, pt, mu, mt in sorted(rows, key=lambda r: r[3]):
        print(f"  {l:11s} median $/sol={mu:.4f} tok/sol={mt:7.0f} | pooled $/sol={pu:.4f} tok/sol={pt:7.0f}")
    for i, unit in ((0, "USD"), (1, "tokens")):
        mat = [[per[m, l][i] / per[m, l][2] for l in langs] for m in models
               if all(per[m, l][2] for l in langs)]
        stat, p = friedmanchisquare(*zip(*mat))
        cheap, dear = {}, {}
        for r in mat:
            lo, hi = langs[r.index(min(r))], langs[r.index(max(r))]
            cheap[lo] = cheap.get(lo, 0) + 1
            dear[hi] = dear.get(hi, 0) + 1
        print(f"  {unit}: Friedman over {len(mat)} LLMs chi2={stat:.1f} p={p:.2g}; "
              f"cheapest {cheap}; most expensive {dear}")
