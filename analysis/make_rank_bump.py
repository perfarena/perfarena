#!/usr/bin/env python3
"""Generate the per-language rank bump charts for latency, energy, and peak memory.

    python3 analysis/make_rank_bump.py --competition <slug> --bt   # the paper's charts
    python3 analysis/make_rank_bump.py --competition <slug>        # geometric-mean ratios
    python3 analysis/make_rank_bump.py                             # legacy appendix parse

With --bt, ranks follow the Bradley-Terry win probability per language
(data/bt_results.json): rank 1 has the highest win probability, and an LLM with
too few solved cells in a language has no point there (a gap in its line).

Outputs (vector PDF):
    figs/res/rank_bump_latency.pdf   model latency rank (1 = fastest) per language
    figs/res/rank_bump_energy.pdf    model energy rank (1 = least energy) per language
    figs/res/rank_bump_memory.pdf    model peak-memory rank (1 = least memory) per language

Ranks are the row order of the per-language ranking tables in
sections/appendix.tex (tab:app-<lang>), which rank the models by the unrounded
leetcode-99-perf median ratios. Languages are ordered by their rank in GitHub
Octoverse 2025 (TypeScript 1, Python 2, JavaScript 3, Java 4, C# 5, PHP 6, C++ 8). The 12
models use six colorblind-safe hues (Paul Tol's vibrant palette). Each hue is
shared by two models that sit six ranks apart, the second one dashed with a
square marker, so identity rests on the hue, the line style, and the direct
end-label together, never on color alone. Each model keeps its style in all
three charts."""
import argparse
import json
import pathlib
import re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("--competition", default=None,
                help="slug; build the ranks from data/<slug>_ratios.json "
                     "(the generator's per-language ratios) and suffix outputs "
                     "with _<slug>. Default: parse the leetcode appendix tables "
                     "in sections/appendix.tex (unsuffixed outputs).")
ap.add_argument("--bt", action="store_true",
                help="with --competition: rank by the Bradley-Terry win probability "
                     "per language (data/bt_results.json, from bt_analysis.py) "
                     "instead of the geometric-mean ratio")
args = ap.parse_args()
if args.bt and not args.competition:
    ap.error("--bt needs --competition")

ROOT = pathlib.Path(__file__).resolve().parent / "out" / "figs"
APPENDIX = ROOT.parent / "sections" / "appendix.tex"

# Octoverse 2025 order; slugs match the tab:app-<slug> labels
SLUGS = ["typescript", "python3", "javascript", "java", "csharp", "php", "cpp"]
LANG_NAMES = ["TS", "Py", "JS", "Java", "C#", "PHP", "C++"]   # short names, as in the tables
METRICS = [("latency", "Latency rank (1 = fastest)"),
           ("energy", "Energy rank (1 = least energy)"),
           ("memory", "Peak-memory rank (1 = least memory)")]

# Paul Tol vibrant, a colorblind-safe qualitative palette validated for lines on
# a light surface (dataviz validate_palette.js: lightness, chroma, and CVD checks
# pass; the CVD/contrast WARNs sit in the legal band because a second encoding
# carries identity). Six hues cover the twelve models: each hue is used twice,
# once solid with a round marker and once dashed with a square marker, and the
# two models that share a hue sit six ranks apart, so color never carries
# identity alone (the line style and the direct end-label do too).
BLUE, ORANGE, PURPLE = "#0077bb", "#ee7733", "#aa4499"
TEAL, RED, CYAN = "#009988", "#cc3311", "#33bbee"
MAGENTA = "#ee3377"   # Paul Tol vibrant magenta, the 7th hue for glm-5.3-flash
# model -> (color, dashed); each of the six paired hues appears once solid and
# once dashed, and glm-5.3-flash takes the 7th hue solid (added 2026-10-07).
STYLE = {
    "gpt-6-astra": (BLUE, False),       "claude-sonnet-5-5": (BLUE, True),
    "gpt-5.6-sol": (ORANGE, False),     "gpt-5.6-luna": (ORANGE, True),
    "claude-opus-5-5": (PURPLE, False), "gpt-5.4-mini": (PURPLE, True),
    "gemini-3.8-flash": (TEAL, False),  "claude-opus-4-8": (TEAL, True),
    "claude-fable-5-1": (RED, False),   "deepseek-v4.1-flash": (RED, True),
    "gpt-5.5": (CYAN, False),           "grok-4.6": (CYAN, True),
    "glm-5.3-flash": (MAGENTA, False),
}
INK, MUTED, GRID = "#0b0b0b", "#898781", "#e1e0d9"

# ---- per-language ranks + model styles ------------------------------------
# Legacy (no --competition): parse the leetcode appendix ranking tables.
# --competition <slug>: read data/<slug>_ratios.json (the generator's
# per-(model,language) latency/energy/memory ratios) and rank within each
# language. The JSON path needs no appendix tables, so it works for any comp.
_FALLBACK = [BLUE, ORANGE, PURPLE, TEAL, RED, CYAN]

def _effective_style(model_list):
    """Keep the tuned hues for known models; cycle the palette (dashed) for the
    rest so a new competition's extra models still get distinct, legible lines."""
    st, i = {}, 0
    for m in model_list:
        if m in STYLE:
            st[m] = STYLE[m]
        else:
            st[m] = (_FALLBACK[i % len(_FALLBACK)], True)
            i += 1
    return st

if args.competition and args.bt:
    bt = json.load(open(ROOT / "data" / "bt_results.json"))[args.competition]["per_language"]
    full = list(bt["runtime_ms"]["cpp"]["win_prob"])
    dmodels = [m.split("/")[-1] for m in full]
    style = _effective_style(dmodels)
    ranks = {metric: {m: {} for m in dmodels} for metric, _ in METRICS}
    _key = {"latency": "runtime_ms", "energy": "runtime_energy_j", "memory": "memory_mb"}
    for x, slug in enumerate(SLUGS):
        for metric, _ in METRICS:
            wp = bt[_key[metric]][slug]["win_prob"]
            rated = [m for m in full if wp[m] is not None]
            for r, m in enumerate(sorted(rated, key=lambda m: -wp[m]), start=1):
                ranks[metric][m.split("/")[-1]][x] = r
elif args.competition:
    jd = json.load(open(ROOT / "data" / f"{args.competition}_ratios.json"))
    dmodels = jd["models"]
    jslugs = jd["lang_slugs"]
    # chart only the languages this competition has (in Octoverse order)
    LANG_NAMES = [n for s, n in zip(SLUGS, LANG_NAMES) if s in jslugs]
    SLUGS = [s for s in SLUGS if s in jslugs]
    style = _effective_style(dmodels)
    ranks = {metric: {m: {} for m in dmodels} for metric, _ in METRICS}
    _mat = {"latency": "heatmap_runtime", "energy": "heatmap_energy", "memory": "heatmap_memory"}
    for x, slug in enumerate(SLUGS):
        if slug not in jslugs:
            continue
        c = jslugs.index(slug)
        for metric, _ in METRICS:
            H = jd[_mat[metric]]
            pairs = [(dmodels[i], H[i][c]) for i in range(len(dmodels)) if H[i][c] is not None]
            pairs.sort(key=lambda p: p[1])
            for r, (m, _v) in enumerate(pairs, start=1):
                ranks[metric][m][x] = r
else:
    style = dict(STYLE)
    # one table row = "val & \code{model}" three times (latency, energy, memory)
    ROW = re.compile(r"^[\d.]+ & \\code\{(.+?)\} & [\d.]+ & \\code\{(.+?)\} & "
                     r"[\d.]+ & \\code\{(.+?)\} \\\\", re.M)
    tex = APPENDIX.read_text()
    ranks = {metric: {m: {} for m in style} for metric, _ in METRICS}
    for x, slug in enumerate(SLUGS):
        block = tex.split(r"\label{tab:app-%s}" % slug)[1].split(r"\end{tabular}")[0]
        for r, row in enumerate(ROW.findall(block), start=1):
            for (metric, _), m in zip(METRICS, row):
                ranks[metric][m][x] = r

_nmodels = len(style)
_suffix = f"_{args.competition}" if args.competition else ""

# Each chart is exactly FIG_W wide, 0.495 of the paper's 517.9 pt text width:
# the six charts form one 3x2 figure, two per row 0.01 text widths apart, and
# print at their nominal size with no outer padding.
FIG_W, FIG_H = 0.495 * 517.935 / 72, 1.8


def short(m):
    """Shorter model label so the plot area gets the width: cd- for claude-,
    ds- for deepseek- (the figure caption spells them out)."""
    return m.replace("claude-", "cd-").replace("deepseek-", "ds-")


plt.rcParams.update({
    "font.size": 5.5, "axes.labelsize": 5.5, "xtick.labelsize": 5.8,
    "ytick.labelsize": 5.0, "figure.dpi": 200, "savefig.bbox": "standard",
    "savefig.pad_inches": 0.01,
    "pdf.fonttype": 42,
})
t = np.linspace(0, 1, 60)
ease = (1 - np.cos(np.pi * t)) / 2                         # flat at each column

for metric, title in METRICS:
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    for x in range(len(SLUGS)):
        ax.axvline(x, color=GRID, lw=0.6, zorder=0)

    for m, (color, dashed) in style.items():
        pts = ranks[metric][m]
        for x in range(len(SLUGS) - 1):
            if x in pts and x + 1 in pts:                  # no line across a gap
                ax.plot(x + t, pts[x] + (pts[x + 1] - pts[x]) * ease, color=color,
                        lw=1.0, ls=(0, (2.0, 1.0)) if dashed else "-",
                        solid_capstyle="round", dash_capstyle="round", zorder=2)
        xs = sorted(pts)
        ax.plot(xs, [pts[x] for x in xs], linestyle="none",
                marker="s" if dashed else "o", ms=2.9 if dashed else 3.2,
                mfc=color, mec="white", mew=0.5, zorder=3)
        # direct labels at both ends, in ink, beside the rank column
        # left labels (y-axis side, no rank numbers) are larger and tucked
        # close to the chart; right labels sit beside the rank numbers.
        for x, ha, dx, fs in ((xs[0], "right", -2, 5.5), (xs[-1], "left", 11, 5.5)):
            edge = 0 if ha == "right" else 1
            ax.annotate(short(m), xy=(edge, pts[x]), xycoords=("axes fraction", "data"),
                        xytext=(dx, 0), textcoords="offset points", ha=ha,
                        va="center", color=INK, fontsize=fs)

    ax.set_xlim(-0.2, len(SLUGS) - 0.8)
    ax.set_ylim(_nmodels + 0.6, 0.4)                       # rank 1 on top
    ax.set_yticks(range(1, _nmodels + 1))
    ax.tick_params(axis="y", length=0, colors=MUTED, labelright=True, labelleft=False, pad=2)
    ax.xaxis.tick_top()
    ax.set_xticks(range(len(SLUGS)), labels=LANG_NAMES)
    ax.tick_params(axis="x", length=0, colors=INK, pad=2)
    ax.spines[:].set_visible(False)
    ax.set_title(title, loc="left", color=INK, fontsize=6.0, pad=12)
    # Grow the axes until the labels, title, and ticks just fit the figure.
    # Their sizes are in points, so they do not change as the axes grow.
    for _ in range(2):
        fig.canvas.draw()
        rnd = fig.canvas.get_renderer()
        tb, ab = ax.get_tightbbox(rnd), ax.get_window_extent(rnd)
        W, H = fig.bbox.width, fig.bbox.height
        ax.set_position([(ab.x0 - tb.x0) / W, (ab.y0 - tb.y0) / H,
                         1 - ((ab.x0 - tb.x0) + (tb.x1 - ab.x1)) / W,
                         1 - ((ab.y0 - tb.y0) + (tb.y1 - ab.y1)) / H])
    (ROOT / "res").mkdir(parents=True, exist_ok=True)
    fig.savefig(ROOT / "res" / f"rank_bump_{metric}{_suffix}.pdf")
    plt.close(fig)
    print(f"wrote figs/res/rank_bump_{metric}{_suffix}.pdf")
