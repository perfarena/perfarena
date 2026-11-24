#!/usr/bin/env python3
"""Cross-language correctness consistency on LeetCodeComp (appendix table
tab:xlang-consistency and the correctness paragraph of the evaluation).

For every LLM-problem pair, the script classifies the verdicts across the
competition's languages as accepted in every language, mixed, or rejected in
every language, and it reports the rejection rate of each language. A cell
without an attempt counts as rejected, as in pass@1.

    PERFARENA_CACHE=<cache> python3 analysis/correctness_consistency.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import gen_paper_data as g  # noqa: E402

BASE = os.environ.get("ARENA_BASE", "http://offline.invalid")
COMP = "leetcode-99-perf"
models = [m for m in g.DEFAULT_MODELS if m not in g.EXCLUDE_MODELS]
sel = g.campaign_submissions(BASE, "", COMP, models)
langs = [s for s, _ in g.competition_languages(BASE, "", COMP, models)]
problems = sorted({p for (_m, _l), (_s, atts) in sel.items() for p in atts})


def accepted(m, lang, p):
    a = sel[m, lang][1].get(p)
    return bool(a and a.get("accepted"))


n_pairs = len(models) * len(problems)
all_acc = all_rej = 0
all_rej_pairs = []
for m in models:
    for p in problems:
        k = sum(accepted(m, lang, p) for lang in langs)
        if k == len(langs):
            all_acc += 1
        elif k == 0:
            all_rej += 1
            all_rej_pairs.append((g.disp(m), p))
mixed = n_pairs - all_acc - all_rej
print(f"{n_pairs} LLM-problem pairs over {len(langs)} languages "
      f"(a cell without an attempt counts as rejected)")
for name, n in (("accepted in every language", all_acc), ("mixed", mixed),
                ("rejected in every language", all_rej)):
    print(f"  {name:28s} {n:5d}  {100 * n / n_pairs:.1f}%")
print(f"  same verdict in every language: {all_acc + all_rej} "
      f"({100 * (all_acc + all_rej) / n_pairs:.1f}%)")
print("  rejected everywhere:", all_rej_pairs)
print("rejection rate per language:")
for lang in langs:
    rej = sum(not accepted(m, lang, p) for m in models for p in problems)
    missing = sum(p not in sel[m, lang][1] for m in models for p in problems)
    print(f"  {lang:11s} {100 * rej / n_pairs:.1f}%  ({rej} of {n_pairs}; "
          f"{missing} without an attempt)")
