#!/usr/bin/env python3
"""LaTeX tables of the Bradley-Terry analysis (figs/data/bt_results.json, from
bt_analysis.py).

    python3 analysis/make_bt_tables.py

Outputs:
  tables/rq1_overall.tex          win probability of each LLM per metric, and its
                                  latency ratio to the reference set (RQ1)
  tables/rq1_languages.tex        latency win probability per language (appendix)
  tables/rq1_languages_energy.tex energy win probability per language (appendix)
  tables/rq1_languages_memory.tex memory win probability per language (appendix)
  tables/bt_rankings.tex          per-language rankings, every metric, one table per
                                  language with a subtable per competition (appendix)
  tables/bt_stats.tex             statistical tests (appendix)

A win probability is an LLM's mean chance of beating one of the other LLMs on a
cell that both solved, from the Bradley-Terry ratings (a tie counts as half a
win). A dash marks an LLM with too few solved cells in a language to rate.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "out"
R = json.loads((ROOT / "figs" / "data" / "bt_results.json").read_text())
MIN_CELLS = R["settings"]["min_cells"]
COMPS = [("leetcode-99-perf", "LC", r"\leetcodeComp"), ("clbg-10", "CLBG", r"\clbgComp")]
METRICS = [("runtime_ms", "Latency"), ("runtime_energy_j", "Energy"), ("memory_mb", "Peak memory")]
# Octoverse 2025 order, as in the rank-bump figure
LANGS = [("typescript", "TS"), ("python3", "Py"), ("javascript", "JS"), ("java", "Java"),
         ("csharp", r"C\#"), ("php", "PHP"), ("cpp", "C++")]
LANG_FULL = {"cpp": "C++", "csharp": r"C\#", "java": "Java", "javascript": "JavaScript",
             "php": "PHP", "python3": "Python", "typescript": "TypeScript"}
MACRO = {"gpt-6-astra": r"\gptastra", "gpt-5.6-sol": r"\gptsol", "gpt-5.6-luna": r"\gptluna",
         "gpt-5.5": r"\gptfivefive", "gpt-5.4-mini": r"\gptfivemini",
         "claude-opus-5-5": r"\opusfive", "claude-opus-4-8": r"\opusfour",
         "claude-sonnet-5-5": r"\sonnet", "claude-fable-5-1": r"\fable",
         "deepseek-v4.1-flash": r"\deepseek", "gemini-3.8-flash": r"\geminiflash",
         "grok-4.6": r"\grok", "glm-5.3-flash": r"\glm"}


def short(m):
    return m.split("/")[-1]


def pct(v):
    return "--" if v is None else f"{100 * v:.0f}"


def bold(s, on):
    return r"\textbf{%s}" % s if on else s


LC = R["leetcode-99-perf"]["primary"]
ORDER = sorted(LC["runtime_ms"]["win_prob"], key=lambda m: -LC["runtime_ms"]["win_prob"][m])


def rq1_overall():
    L = [r"\begin{table}", r"\centering",
         r"\caption{\rr{Win probability (\%) on \leetcodeComp (LC) and \clbgComp (CLBG), sorted by "
         r"\latency win probability on LC. Bold marks the tied leaders. The appendix shows that "
         r"the \latency ratio (\Cref{sec:reference-set}) favors \llms that fail hard problems.}}",
         r"\label{tab:rq1-overall}", r"\resizebox{\columnwidth}{!}{",
         r"\begin{tabular}{l rr rr rr rr}", r"\toprule",
         r" & \multicolumn{2}{c}{\textbf{Latency}} & \multicolumn{2}{c}{\textbf{Energy}} "
         r"& \multicolumn{2}{c}{\textbf{Peak memory}} & \multicolumn{2}{c}{\textbf{Latency ratio}} \\",
         r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(l){8-9}",
         r"\textbf{\llm} & \textbf{LC} & \textbf{CLBG} & \textbf{LC} & \textbf{CLBG} "
         r"& \textbf{LC} & \textbf{CLBG} & \textbf{LC} & \textbf{CLBG} \\", r"\midrule"]
    for m in ORDER:
        cells = []
        for mk, _ in METRICS:
            for comp, _, _ in COMPS:
                p = R[comp]["primary"][mk]
                cells.append(bold(pct(p["win_prob"][m]), m in p["undefeated"]))
        cells += [f"{R[comp]['current']['runtime_ms'][m]:.2f}" for comp, _, _ in COMPS]
        L.append(r"%s & %s \\" % (MACRO[short(m)], " & ".join(cells)))
    L += [r"\bottomrule", r"\end{tabular}", r"}", r"\end{table}", ""]
    return "\n".join(L)


def rq1_languages(mk, label):
    """Win probability per language in one metric (appendix, one-column pages)."""
    name = {"runtime_ms": r"\Latency", "runtime_energy_j": r"\Energy", "memory_mb": r"\Memory"}[mk]
    cols = " ".join("rr" for _ in LANGS)
    L = [r"\begin{table}[H]", r"\centering", r"\footnotesize",
         r"\caption{%s win probability (\%%) on \leetcodeComp (LC) and \clbgComp (CLBG), "
         r"sorted in a descending order of \latency win probability on LC. The highest win "
         r"probability in each column is in bold. A dash marks an \llm with fewer than %d~solved "
         r"problems in the corresponding language.}" % (name, MIN_CELLS),
         r"\label{%s}" % label, r"\begin{tabular}{l %s}" % cols, r"\toprule",
         " & " + " & ".join(r"\multicolumn{2}{c}{\textbf{%s}}" % n for _, n in LANGS) + r" \\",
         "".join(r"\cmidrule(lr){%d-%d}" % (2 + 2 * i, 3 + 2 * i) for i in range(len(LANGS))),
         r"\textbf{\llm} & " + " & ".join(r"\textbf{LC} & \textbf{CLBG}" for _ in LANGS) + r" \\",
         r"\midrule"]
    best = {(comp, s): max(v for v in R[comp]["per_language"][mk][s]["win_prob"].values()
                           if v is not None)
            for comp, _, _ in COMPS for s, _ in LANGS}
    for m in ORDER:
        cells = []
        for s, _ in LANGS:
            for comp, _, _ in COMPS:
                v = R[comp]["per_language"][mk][s]["win_prob"][m]
                # Bold every value that prints as the column maximum, so that
                # two equal printed values are both bold.
                cells.append(bold(pct(v), v is not None and pct(v) == pct(best[comp, s])))
        L.append(r"\code{%s} & %s \\" % (short(m), " & ".join(cells)))
    L += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    return "\n".join(L)


def ranking_tabular(comp, s):
    """The rankings of one competition and language, every metric."""
    L = [r"\begin{tabular}{@{}rl rl rl@{}}", r"\toprule",
         r"\multicolumn{2}{c}{\textbf{Latency}} & \multicolumn{2}{c}{\textbf{Energy}} & "
         r"\multicolumn{2}{c}{\textbf{Peak memory}} \\",
         r"\cmidrule(lr){1-2}\cmidrule(lr){3-4}\cmidrule(l){5-6}"]
    lists = []
    for mk, _ in METRICS:
        d = R[comp]["per_language"][mk][s]
        wp = d["win_prob"]
        rated = sorted((m for m in wp if wp[m] is not None), key=lambda m: -wp[m])
        hidden = sorted((m for m in wp if wp[m] is None), key=short)
        lists.append([(wp[m], m, m in d["lead_group"]) for m in rated]
                     + [(None, m, False) for m in hidden])
    for row in zip(*lists):
        L.append(" & ".join(r"%s & %s" % (bold(pct(v), lead), bold(r"\code{%s}" % short(m), lead))
                            for v, m, lead in row) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(L)


def rankings():
    """One table per language, with the rankings of each competition in a subtable,
    side by side (appendix, one-column pages)."""
    out = []
    for s, _ in LANGS:
        L = [r"\begin{table}[H]", r"\centering", r"\scriptsize", r"\setlength{\tabcolsep}{3pt}",
             r"\caption{\llms on %s, ranked by win probability (\%%) in \latency, \energy, "
             r"and \memory.}" % LANG_FULL[s],
             r"\label{tab:bt-%s}" % s,
             "\\hfil\n".join(r"\subfloat[%s\label{tab:bt-%s-%s}]{%s}" % (macro, comp, s, ranking_tabular(comp, s))
                             for comp, _, macro in COMPS),
             r"\end{table}", ""]
        out.append("\n".join(L))
    return "\n".join(out)


def fmt_p(p):
    return r"$<0.001$" if p < 0.001 else f"${p:.3f}$"


def stats():
    L = [r"\begin{table}[H]", r"\centering", r"\footnotesize", r"\setlength{\tabcolsep}{4pt}",
         r"\caption{Statistical tests of the Bradley--Terry rankings. Duels: median number "
         r"of duels per \llm pair. $p$: \llm shuffle test of whether the \llms differ at all. "
         r"Sig.\ pairs: pairs that differ after Holm's correction, when the bootstrap "
         r"resamples cells or whole problems. Leaders: \llms that no other \llm beats "
         r"significantly, from the problem bootstrap on both competitions. Language: mean Kendall's~$\tau$ between the per-language rankings, "
         r"its chance level, and the $p$ of the language shuffle test.}",
         r"\label{tab:stat-tests}", r"\begin{tabular}{@{}l r r r r r r r r@{}}", r"\toprule",
         r" & & & \multicolumn{2}{c}{\textbf{Sig.\ pairs}} & & \multicolumn{3}{c}{\textbf{Language}} \\",
         r"\cmidrule(lr){4-5}\cmidrule(l){7-9}",
         r"\textbf{Metric} & \textbf{Duels} & $\boldsymbol{p}$ & \textbf{Cells} & "
         r"\textbf{Problems} & \textbf{Leaders} & $\boldsymbol{\tau}$ & \textbf{Chance} & "
         r"$\boldsymbol{p}$ \\"]
    for comp, _, macro in COMPS:
        L += [r"\midrule", r"\multicolumn{9}{@{}l}{%s} \\" % macro]
        for mk, name in METRICS:
            a, g = R[comp]["primary"][mk], R[comp]["language"][mk]
            L.append(f"{name} & {a['duels_per_pair']} & {fmt_p(a['omnibus']['p'])} & "
                     f"{a['boot']['cells']['sig_pairs']}/78 & {a['boot']['problems']['sig_pairs']}/78 & "
                     f"{len(a['undefeated'])} & ${g['tau']:.2f}$ & ${g['tau_chance']:.2f}$ & "
                     f"{fmt_p(g['p'])} \\\\")
    L += [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""]
    return "\n".join(L)


if __name__ == "__main__":
    T = ROOT / "tables"
    T.mkdir(parents=True, exist_ok=True)
    (T / "rq1_overall.tex").write_text(rq1_overall())
    (T / "rq1_languages.tex").write_text(rq1_languages("runtime_ms", "tab:rq1-languages"))
    (T / "rq1_languages_energy.tex").write_text(
        rq1_languages("runtime_energy_j", "tab:rq1-languages-energy"))
    (T / "rq1_languages_memory.tex").write_text(
        rq1_languages("memory_mb", "tab:rq1-languages-memory"))
    (T / "bt_rankings.tex").write_text(rankings())
    (T / "bt_stats.tex").write_text(stats())
    print("wrote tables/rq1_overall.tex, tables/rq1_languages[_energy|_memory].tex, "
          "tables/bt_rankings.tex, tables/bt_stats.tex")
