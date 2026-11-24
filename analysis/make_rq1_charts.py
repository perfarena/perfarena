#!/usr/bin/env python3
"""Generate the two RQ1 figures from figs/data/rq1_ratios.json.

    python3 analysis/make_rq1_charts.py

Outputs (vector PDF):
    figs/res/models_by_language.pdf   heatmap: model x language runtime ratio
    figs/res/metric_ranking.pdf       grouped bars: mean runtime/energy/memory

Both use the leetcode-99-perf ratios (1.00 = fastest correct solution on a
problem; larger is worse). Colors are colorblind-safe (Okabe-Ito for the three
metrics; a single-hue sequential ramp for the heatmap)."""
import argparse
import json
import pathlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

ap = argparse.ArgumentParser()
ap.add_argument("--competition", default=None,
                help="slug; reads data/<slug>_ratios.json and suffixes the output "
                     "PDFs with _<slug>. Default: legacy data/rq1_ratios.json "
                     "(leetcode-99-perf), unsuffixed outputs.")
args = ap.parse_args()

ROOT = pathlib.Path(__file__).resolve().parent / "out" / "figs"
_data_path = (ROOT / "data" / f"{args.competition}_ratios.json") if args.competition \
    else (ROOT / "data" / "rq1_ratios.json")
_suffix = f"_{args.competition}" if args.competition else ""
data = json.load(open(_data_path))
models = data["models"]
langs = data["languages"]
H = np.array([[np.nan if v is None else v for v in row] for row in data["heatmap_runtime"]])

plt.rcParams.update({
    "font.size": 7, "axes.titlesize": 8, "axes.labelsize": 7.5,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 6.5,
    "figure.dpi": 200, "savefig.bbox": "tight", "pdf.fonttype": 42,
})

# ---- Figure 1: heatmap, model x language (runtime ratio) ----
fig, ax = plt.subplots(figsize=(3.35, 3.9))
cmap = plt.get_cmap("Blues").copy()
cmap.set_bad("#e8e8e8")                                    # missing cell = light gray
vmax = np.nanmax(H)
im = ax.imshow(H, cmap=cmap, vmin=1.0, vmax=vmax, aspect="auto")
ax.set_xticks(range(len(langs)), labels=langs)
ax.set_yticks(range(len(models)), labels=models)
for i in range(len(models)):
    for j in range(len(langs)):
        v = H[i, j]
        if np.isnan(v):
            continue
        # dark text on light cells, light text on dark cells
        frac = (v - 1.0) / (vmax - 1.0) if vmax > 1.0 else 0
        ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                color="white" if frac > 0.6 else "#20232a", fontsize=6)
ax.set_xlabel("Language")
ax.spines[:].set_visible(False)
ax.tick_params(length=0)
cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
cbar.set_label("latency ratio (1.00 = fastest)", fontsize=6.5)
cbar.ax.tick_params(labelsize=6, length=0)
(ROOT / "res").mkdir(parents=True, exist_ok=True)
fig.savefig(ROOT / "res" / f"models_by_language{_suffix}.pdf")
plt.close(fig)

# ---- Figure 2: grouped horizontal bars, mean runtime/energy/memory ----
OKABE = {"runtime": "#0072B2", "energy": "#E69F00", "memory": "#009E73"}
rt = data["mean_runtime"]; en = data["mean_energy"]; mem = data["mean_memory"]
y = np.arange(len(models))[::-1]                           # best model at top
h = 0.26
fig, ax = plt.subplots(figsize=(3.35, 4.6))
ax.barh(y + h, rt, h, color=OKABE["runtime"], label="Latency")
ax.barh(y,      en, h, color=OKABE["energy"],  label="Energy")
ax.barh(y - h, mem, h, color=OKABE["memory"], label="Memory")
for yy, vals in zip(y, zip(rt, en, mem)):
    for k, v in zip((h, 0, -h), vals):
        ax.text(v + 0.01, yy + k, f"{v:.2f}", va="center", ha="left", fontsize=5.3,
                color="#20232a")
ax.set_yticks(y, labels=models)
ax.set_xlim(1.0, max(rt) * 1.14)
ax.set_xlabel("mean ratio across languages (1.00 = best, lower is better)")
ax.spines[["top", "right"]].set_visible(False)
ax.tick_params(length=0)
ax.xaxis.grid(True, color="#e6e6e6", linewidth=0.6)
ax.set_axisbelow(True)
ax.legend(loc="upper right", frameon=False, ncol=1)
fig.savefig(ROOT / "res" / f"metric_ranking{_suffix}.pdf")
plt.close(fig)
print(f"wrote figs/res/models_by_language{_suffix}.pdf and figs/res/metric_ranking{_suffix}.pdf")
