#!/usr/bin/env python3
"""Pareto optimality levels of the models over latency, energy, and peak memory.

With --bt (the paper's figure), dominance comes from data/bt_results.json: A
dominates B when A is significantly better in at least one metric and
significantly worse in none (Holm-corrected bootstrap tests of the Bradley-Terry
ratings). Without --bt, the script reads figs/data/<comp>_rq2.json (written by
gen_paper_data.py) and compares the geometric-mean ratios as they are. It draws one
layered dominance graph per competition, in the style of a non-dominated
sorting: level 1 holds the models that no other model dominates, level 2 the
models that only level-1 models dominate, and so on. An arrow goes from a model
to each model that it dominates directly (the transitive reduction of the
dominance relation). Model A dominates model B when A is at least as good as B
on all three metrics and strictly better on at least one; the comparison uses
the measured geometric-mean ratios as they are, without a significance test.

The layout follows Karim's version of the figure (commit b78a49b): the two panels
side by side, one row per level, the name macros in the nodes, and straight arrows.
Each panel is laid out in cm at the real node size and is as wide as its widest
row. Both panels are then scaled by one factor (TikZ transform shape) so that
together they fill the text width. The order of the models in each level is the
one with the fewest arrow crossings (every order is tried), then the least
horizontal skew. Each model sits between its evenly spaced slot and the mean
position of the models it connects to. An arrow that skips a level crosses it
through a gap only where a straight line would touch a name.

    python3 analysis/make_pareto_levels.py
    python3 analysis/make_pareto_levels.py --bt
writes the graph as TikZ to figs/tikz/pareto_levels_<comp>.tex, which
figs/pareto_levels.tex includes, and the levels and arrows to
figs/data/<comp>_pareto_levels.json
"""
import argparse
import itertools
import json
import math
from pathlib import Path

import gen_paper_data as gpd
from make_bt_tables import MACRO

ROOT = Path(__file__).resolve().parent / "out" / "figs"
CRIT = ("lat", "en", "mem")          # geometric-mean ratios, lower is better
COMPS = ("leetcode-99-perf", "clbg-10")
BLUE, RED = "#4472C4", "#E03C31"
FONT = 5.3                           # pt: node and label font; \code in a node prints at 5 pt

# Sizes in cm. The panels stand side by side in a figure* (\textwidth of the
# IEEEtran compsoc journal class, 517.935 pt), HGAP apart (\hfill).
PT = 2.54 / 72.27
TEXTWIDTH = 517.935 * PT
HGAP = 0.35
HEIGHT = 4.82                        # cm per panel after scaling, the same for both panels
GAP = 0.2                            # least space between two names of a row
LABEL_W = 1.25                       # right of the node area: the level labels
X0 = -0.1                            # left end of the level lines
HALF_H = 0.14                        # half the height of a node
ALPHA = 0.5                          # weight of a node's evenly spaced slot against its neighbours
MAX_ORDERS = 200_000                 # above this, order the levels by barycentre instead


BEATS = None   # --bt: {metric: {model: set of models it beats significantly}}


def dominates(q, a, b):
    if BEATS is not None:
        # Bradley-Terry: a difference counts only when the pairwise test finds
        # it significant (Holm); any other difference is a tie.
        return (all(a not in BEATS[k][b] for k in BEATS)
                and any(b in BEATS[k][a] for k in BEATS))
    return (all(q[a][k] <= q[b][k] for k in CRIT)
            and any(q[a][k] < q[b][k] for k in CRIT))


def levels_and_edges(q):
    models = list(q)
    rest, level, k = set(models), {}, 1
    while rest:
        front = [m for m in rest if not any(dominates(q, o, m) for o in rest if o != m)]
        assert front, "dominance has a cycle"
        for m in front:
            level[m] = k
        rest -= set(front)
        k += 1
    # The transitive reduction below assumes a transitive relation.
    assert not any(dominates(q, a, c) and dominates(q, c, b) and not dominates(q, a, b)
                   for a in models for b in models for c in models if len({a, b, c}) == 3), \
        "dominance is not transitive"
    edges = [(a, b) for a in models for b in models
             if a != b and dominates(q, a, b)
             and not any(dominates(q, a, c) and dominates(q, c, b)
                         for c in models if c not in (a, b))]
    return level, edges


def half_width(m):
    """Half the node width in cm: \\code prints bold Courier at 5 pt (3.0 pt per
    character), with an inner sep of 1.5 pt on each side and 0.5 pt for the
    rounded corners."""
    return (3.0 * len(gpd.disp(m)) + 3.5) * PT / 2


def isotonic(d):
    """Nondecreasing least-squares fit to d (pool adjacent violators)."""
    blocks = []
    for v in d:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, n2 = blocks.pop()
            v1, n1 = blocks.pop()
            blocks.append([(v1 * n1 + v2 * n2) / (n1 + n2), n1 + n2])
    return [v for v, n in blocks for _ in range(n)]


def place(row, desired, half, area):
    """x of the ordered row closest to desired (least squares), with GAP between
    names and every name inside [0, area]."""
    off, acc = [], 0.0
    for i, m in enumerate(row):
        if i:
            acc += half[row[i - 1]] + half[m] + GAP
        off.append(acc)
    y = isotonic([desired[m] - o for m, o in zip(row, off)])
    lo, hi = half[row[0]], area - off[-1] - half[row[-1]]
    if hi < lo:
        lo = hi = (lo + hi) / 2
    return {m: min(max(v, lo), hi) + o for m, v, o in zip(row, y, off)}


def coords(rows, edges, half, area, iters=12):
    """Each row starts on evenly spaced slots. Each node then moves toward a mix of
    its slot (ALPHA) and the mean x of the nodes it connects to."""
    nb = {m: [] for r in rows.values() for m in r}
    for a, b in edges:
        nb[a].append(b)
        nb[b].append(a)
    slot = {m: (i + 0.5) * area / len(r) for r in rows.values() for i, m in enumerate(r)}
    x = {}
    for r in rows.values():
        x.update(place(r, slot, half, area))
    ks = sorted(rows)
    for _ in range(iters):
        for k in ks + ks[::-1]:
            d = {m: ALPHA * slot[m] + (1 - ALPHA) * (sum(x[n] for n in nb[m]) / len(nb[m]) if nb[m] else slot[m])
                 for m in rows[k]}
            x.update(place(rows[k], d, half, area))
    return x


def crossings(x, level, edges):
    """Number of pairs of straight arrows that cross."""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    n = 0
    for (a, b), (c, d) in itertools.combinations(edges, 2):
        if len({a, b, c, d}) == 4:
            p1, p2, p3, p4 = ((x[m], -level[m]) for m in (a, b, c, d))
            n += (ccw(p1, p3, p4) * ccw(p2, p3, p4) < 0) and (ccw(p1, p2, p3) * ccw(p1, p2, p4) < 0)
    return n


def layout(q, level, edges):
    """Rows (one per level, in drawing order), x per model, and the node-area width,
    all in natural cm."""
    nlev = max(level.values())
    half = {m: half_width(m) for m in q}
    base = {k: [m for m in q if level[m] == k] for k in range(1, nlev + 1)}
    area = max(sum(2 * half[m] for m in r) + GAP * (len(r) - 1) for r in base.values())
    if math.prod(math.factorial(len(r)) for r in base.values()) > MAX_ORDERS:
        # Too many orders to try: level 1 by latency, each later level by the
        # mean position of its direct dominators (barycentre).
        rows = {1: sorted(base[1], key=lambda m: q[m]["lat"])}
        for k in range(2, nlev + 1):
            xe = {m: i for r in rows.values() for i, m in enumerate(r)}
            def bary(m):
                ps = [xe[a] for a, b in edges if b == m and a in xe]
                return sum(ps) / len(ps) if ps else 0.0
            rows[k] = sorted(base[k], key=bary)
        return rows, coords(rows, edges, half, area), half, area
    best = None
    for combo in itertools.product(*(itertools.permutations(base[k]) for k in sorted(base))):
        rows = {k + 1: list(r) for k, r in enumerate(combo)}
        xe = {m: (i + 0.5) / len(r) * area for r in rows.values() for i, m in enumerate(r)}
        if best is not None and crossings(xe, level, edges) > best[0][1] + 2:
            continue                 # cheap filter on evenly spaced rows
        x = coords(rows, edges, half, area, iters=6)
        c = crossings(x, level, edges)
        skew = sum(abs(x[a] - x[b]) / (level[b] - level[a]) for a, b in edges) / area
        score = (c + 0.5 * skew, c)
        if best is None or score < best[0]:
            best = (score, rows)
    rows = best[1]
    return rows, coords(rows, edges, half, area), half, area


def route(rows, x, half, level, edges, area):
    """Waypoints of each arrow that skips levels. The arrow stays straight unless the
    straight line would touch a name of a skipped level. There, it crosses the level
    vertically through the nearest gap, and the arrows that share a gap are spread
    evenly across it."""
    def gaps(k):
        sp = sorted((x[m] - half[m], x[m] + half[m]) for m in rows[k])
        out = [(max(X0, sp[0][0] - 0.9), sp[0][0])]
        out += [(sp[i][1], sp[i + 1][0]) for i in range(len(sp) - 1)]
        out.append((sp[-1][1], min(area + 0.3, sp[-1][1] + 0.9)))
        return [g for g in out if g[1] - g[0] > 0.12]
    use = {}                                   # (level, gap) -> arrows through it
    for a, b in edges:
        for k in range(level[a] + 1, level[b]):
            x0 = x[a] + (k - level[a]) / (level[b] - level[a]) * (x[b] - x[a])
            if any(abs(x0 - x[n]) < half[n] + 0.12 for n in rows[k]):
                g = min(gaps(k), key=lambda g: max(g[0] - x0, x0 - g[1], 0))
                use.setdefault((k, g), []).append((x0, (a, b)))
    xk = {}
    for (k, (lo, hi)), arrows in use.items():
        arrows.sort()
        for i, (_, e) in enumerate(arrows):
            xk[e, k] = lo + (i + 1) * (hi - lo) / (len(arrows) + 1)
    return xk


def emit_tikz(comp, q, level, edges, rows, x, xk, area, nlev, s):
    """The graph as TikZ, in natural cm, scaled by s (transform shape scales the
    names and labels with the drawing)."""
    band = HEIGHT / nlev / s
    y = {m: -(level[m] - 1) * band for m in q}
    P = lambda px, py: f"({px:.3f},{py:.3f})"
    name = {m: f"m{i}" for i, m in enumerate(q)}
    font = r"\sffamily\fontsize{%.1f}{%.1f}\selectfont" % (FONT, FONT * 1.1)
    src = "bt_results.json (Bradley-Terry, significant differences only)" if BEATS else f"{comp}_rq2.json"
    L = [f"% Generated by figs/scripts/make_pareto_levels.py from figs/data/{src}.",
         r"\definecolor{paretoblue}{HTML}{%s}\definecolor{paretored}{HTML}{%s}" % (BLUE[1:], RED[1:]),
         r"\begin{tikzpicture}[scale=%.4f, transform shape," % s,
         r"  pnode/.style={fill=black, text=white, rounded corners=2pt, inner sep=1.5pt, align=center, font=" + font + r"\bfseries},",
         r"  pedge/.style={-{Stealth[length=3pt, width=2.4pt]}, draw=paretoblue, line width=0.45pt, rounded corners=1.5pt},",
         r"  plevel/.style={draw=paretored, dash pattern=on 3pt off 2pt, line width=0.6pt},",
         r"  plabel/.style={anchor=south west, align=left, inner sep=0.8pt, font=" + font + "}]"]
    # A node shows the LLM's name macro (macros.tex), as the tables do.
    for m in q:
        L.append(r"\node[pnode] (%s) at %s {%s};" % (name[m], P(x[m], y[m]), MACRO[gpd.disp(m)]))
    hb = HALF_H + 0.06
    for a, b in edges:
        mid = ""
        for k in range(level[a] + 1, level[b]):
            if ((a, b), k) in xk:
                yk = -(k - 1) * band
                mid += " -- " + P(xk[(a, b), k], yk + hb) + " -- " + P(xk[(a, b), k], yk - hb)
        L.append(r"\draw[pedge] (%s)%s -- (%s);" % (name[a], mid, name[b]))
    for k in range(1, nlev + 1):
        yl = -(k - 0.5) * band
        L.append(r"\draw[plevel] %s -- %s;" % (P(X0, yl), P(area + LABEL_W - 0.05, yl)))
        L.append(r"\node[plabel] at %s {Optimality\\Level %d};" % (P(area + 0.12, yl), k))
    L.append(r"\end{tikzpicture}")
    out = ROOT / "tikz" / f"pareto_levels_{comp}.tex"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(L) + "\n")
    return out


def bt_inputs(comp):
    """q (negated ratings, so lower is better, for the layout) and the
    significant wins, from data/bt_results.json."""
    p = json.loads((ROOT / "data" / "bt_results.json").read_text())[comp]["primary"]
    key = {"lat": "runtime_ms", "en": "runtime_energy_j", "mem": "memory_mb"}
    q = {m: {c: -p[k]["rating"][m] for c, k in key.items()} for m in p["runtime_ms"]["rating"]}
    beats = {c: {m: set(v) for m, v in p[k]["beats"].items()} for c, k in key.items()}
    return q, beats


def build(bt=False):
    global BEATS
    panels = []
    for comp in COMPS:
        BEATS = None
        if bt:
            q, BEATS = bt_inputs(comp)
        else:
            q = json.loads((ROOT / "data" / f"{comp}_rq2.json").read_text())
        level, edges = levels_and_edges(q)
        rows, x, half, area = layout(q, level, edges)
        panels.append(dict(comp=comp, q=q, beats=BEATS, level=level, edges=edges, rows=rows,
                           x=x, area=area, xk=route(rows, x, half, level, edges, area)))
    # one scale for both panels, so that names print at the same size in both
    s = (TEXTWIDTH - HGAP) / sum(p["area"] + LABEL_W - X0 for p in panels)
    for p in panels:
        BEATS = p["beats"]
        nlev = max(p["level"].values())
        tikz = emit_tikz(p["comp"], p["q"], p["level"], p["edges"], p["rows"], p["x"], p["xk"],
                         p["area"], nlev, s)
        (ROOT / "data" / f"{p['comp']}_pareto_levels.json").write_text(json.dumps(
            {"levels": {gpd.disp(m): p["level"][m] for m in p["q"]},
             "edges": [[gpd.disp(a), gpd.disp(b)] for a, b in p["edges"]]}, indent=1))
        print(f"wrote {tikz} ({nlev} levels, {len(p['edges'])} edges)")
    print(f"scale {s:.3f}: names print at {5.0 * s:.2f} pt")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", action="store_true",
                    help="dominance from the Bradley-Terry ratings, counting only "
                         "significant differences (data/bt_results.json)")
    args = ap.parse_args()
    build(bt=args.bt)
