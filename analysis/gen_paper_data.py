#!/usr/bin/env python3
"""Generate the paper's RQ1/RQ2 analysis artifacts for ONE competition straight
from the deployed PerfArena data, so the tables and figure inputs are
reproducible with a single command instead of hand-transcribed.

Methodology (mirrors sections/evaluation.tex):
  * For each (language, problem) we take each model's BEST correct attempt and
    divide its latency / energy / peak-memory by the best correct solution's
    value on that same problem -> a unitless ratio (1.00 = best). This best is
    the live pool best by default, or a FROZEN snapshot (--baseline), so a
    model's ratio only moves when its own code changes.
  * A model's score for a metric in a language is the MEDIAN of its ratios over
    the problems it solved. Its "Mean" column is the mean of those per-language
    medians.
  * Cross-language stability = Kendall's tau-b over every language pair's
    latency (and energy) ranking.
  * RQ2 adds pass@1, $/solution, response tokens, a Pareto frontier over
    {correctness, cost, latency}, and Spearman rho correlations.

Usage:
  # live best-in-pool baseline (default)
  python gen_paper_data.py --competition leetcode-99-perf \
      --base-url <arena-url> --api-key "$ARENA_KEY"

  # freeze today's per-cell bests as version v1 (writes the baseline file)
  python gen_paper_data.py --competition leetcode-99-perf --freeze-baseline v1 \
      --api-key "$ARENA_KEY"

  # later, normalize every table/figure against the frozen v1 baseline
  python gen_paper_data.py --competition leetcode-99-perf --baseline v1 \
      --api-key "$ARENA_KEY"

Writes (under --out-dir, default = analysis/out):
  figs/data/<comp>_ratios.json           consumed by make_rq1_charts.py / make_rank_bump.py
  figs/data/<comp>_stats.json            kendall tau + spearman rho (+ baseline provenance)
  figs/data/<comp>_baseline_<ver>.json   frozen per-cell bests (only with --freeze-baseline)
  tables/rq1_efficiency.tex             RQ1 table, both competitions
  tables/cost.tex                       correctness and cost table, both competitions
  tables/rq2_tradeoffs_<comp>.tex        \\input-able RQ2 table
"""
from __future__ import annotations
import argparse, json, math, os, statistics, sys, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---- the 6 "main" languages, in the paper's column order -------------------
LANGS = [("cpp", "C++"), ("csharp", "C\\#"), ("java", "Java"), ("javascript", "JS"),
         ("php", "PHP"), ("python3", "Py"), ("typescript", "TS")]
LANG_SLUGS = [s for s, _ in LANGS]
ALL_LANGS = list(LANGS)   # LANGS narrows to the languages of one competition in main()
LANG_FULL = {"cpp": "C++", "csharp": "C\\#", "java": "Java", "javascript": "JavaScript",
             "php": "PHP", "python3": "Python", "typescript": "TypeScript"}

# ---- model display + provider mapping (extend as the roster grows) ---------
# key = backend model_name; value = (display label, provider label)
MODEL_META = {
    "gpt-6-astra":        ("gpt-6-astra",        "OpenAI"),
    "gpt-5.6-sol":        ("gpt-5.6-sol",        "OpenAI"),
    "gpt-5.6-luna":       ("gpt-5.6-luna",       "OpenAI"),
    "gpt-5.5":            ("gpt-5.5",            "OpenAI"),
    "gpt-5.4-mini":       ("gpt-5.4-mini",       "OpenAI"),
    "gpt-4.1-mini":       ("gpt-4.1-mini",       "OpenAI"),
    "claude-opus-5-5":    ("claude-opus-5-5",    "Anthropic"),
    "claude-opus-4-8":    ("claude-opus-4-8",    "Anthropic"),
    "claude-sonnet-5-5":  ("claude-sonnet-5-5",  "Anthropic"),
    "claude-fable-5-1":   ("claude-fable-5-1",   "Anthropic"),
    "deepseek/deepseek-v4.1-flash": ("deepseek-v4.1-flash", "DeepSeek"),
    "google/gemini-3.8-flash":      ("gemini-3.8-flash",    "Google"),
    "z-ai/glm-5.3-flash":           ("glm-5.3-flash",       "Zhipu"),
    "x-ai/grok-4.6":                ("grok-4.6",            "xAI"),
    "gemma4:e4b":         ("gemma4:e4b",         "Google (local)"),
}
def disp(m):     return MODEL_META.get(m, (m, "?"))[0]
def provider(m): return MODEL_META.get(m, (m, "?"))[1]

METRICS = [("runtime_ms", "Lat"), ("runtime_energy_j", "En"), ("memory_mb", "Mem")]


# --------------------------------------------------------------------------- API
def _get(base, path, key):
    headers = {"ngrok-skip-browser-warning": "1"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    r = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    with urllib.request.urlopen(r, timeout=300) as f:
        return json.load(f)


# --------------------------------------------------------------------------- data
# The campaign behind the paper: for each (model, language) of a competition,
# the submission measured on the LOCAL harness that covers the most problems
# (ties -> the latest). We build every table from that submission's attempts.
# /api/compare is NOT used: it merges every submission of a model and language,
# including legacy runs on the external LeetCode judge, whose values are on a
# different scale (whole milliseconds, some 0 ms, no energy).
CAMPAIGN_HARNESS = "arena-linux-local"
CACHE_DIR = Path(os.environ.get("PERFARENA_CACHE",
                                Path.home() / ".cache" / "perfarena-paper"))
_SUBS_CACHE = {}

def _submissions(base, key):
    if base not in _SUBS_CACHE:
        # A cache filled from a database snapshot (snapshot_to_cache.py) also
        # holds the submission list, so the scripts then run fully offline.
        snap = CACHE_DIR / "submissions.json"
        subs = json.loads(snap.read_text()) if snap.exists() else _get(base, "/api/submissions", key)
        _SUBS_CACHE[base] = subs if isinstance(subs, list) else (
            subs.get("items") or subs.get("submissions") or [])
    return _SUBS_CACHE[base]

def _attempts(base, key, sid):
    """All attempts of one submission, cached on disk (they never change once
    the submission is judged)."""
    path = CACHE_DIR / f"attempts_{sid}.json"
    if path.exists():
        data = json.loads(path.read_text())
    else:
        data = _get(base, f"/api/submissions/{sid}/attempts", key)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return data if isinstance(data, list) else data.get("items") or data.get("attempts") or []

def _by_problem(atts):
    """One attempt per problem: the latest one, when a problem was re-run."""
    out = {}
    for a in sorted(atts, key=lambda a: a.get("submitted_at") or ""):
        out[a["problem_slug"]] = a
    return out

def campaign_submissions(base, key, comp, models):
    """-> {(model, lang): (submission, {problem_slug: attempt})}"""
    groups = {}
    for s in _submissions(base, key):
        if (s.get("competition_slug") == comp and s.get("model_name") in models
                and s.get("language") in LANG_SLUGS
                and s.get("harness_slug") == CAMPAIGN_HARNESS):
            groups.setdefault((s["model_name"], s["language"]), []).append(s)
    sel = {}
    for k, subs in groups.items():
        cand = [(s, _by_problem(_attempts(base, key, s["id"]))) for s in subs]
        primary, per = max(cand, key=lambda c: (len(c[1]), c[0].get("created_at") or ""))
        per, fill = dict(per), {}
        # A re-run that covers problems the main submission lacks (e.g. a sweep
        # that stopped early) fills them in, from the latest such submission
        # with the same prompt on the same machine.
        for s, atts in sorted(cand, key=lambda c: c[0].get("created_at") or ""):
            if (s is primary or s.get("prompt_name") != primary.get("prompt_name")
                    or s.get("machine_id") != primary.get("machine_id")):
                continue
            for p, a in atts.items():
                if p not in per or p in fill:
                    per[p], fill[p] = a, s["id"]
        sel[k] = (dict(primary, _fill_from=fill), per)
    return sel

def campaign_attempts(base, key, sub):
    """Every attempt behind one campaign (model, language) cell set: all
    attempts of the main submission (re-runs of a problem included, since they
    were paid for) plus all attempts of a filled-in problem in its re-run
    submission."""
    atts = list(_attempts(base, key, sub["id"]))
    for p, sid in (sub.get("_fill_from") or {}).items():
        atts += [a for a in _attempts(base, key, sid) if a.get("problem_slug") == p]
    return atts

def campaign_totals(base, key, sub):
    """Generation totals of one campaign (model, language) cell set, summed over
    campaign_attempts. -> dict(cost, tok, n, gen_s)"""
    atts = campaign_attempts(base, key, sub)
    # cost_usd_live: the cost at the current list prices, which the submission
    # totals also use (cost_usd is the client's snapshot, often empty).
    return dict(cost=sum((a.get("cost_usd_live") if a.get("cost_usd_live") is not None
                          else a.get("cost_usd")) or 0.0 for a in atts),
                tok=sum(a.get("response_tokens") or 0 for a in atts),
                n=len(atts),
                gen_s=sum((a.get("wall_clock_ms") or 0) for a in atts) / 1000)

def competition_languages(base, key, comp, models):
    """The languages of LANGS in which EVERY model has a campaign submission on
    this competition. A language joins a competition only once all models ran
    it, so no model's pass@1 or scores average over fewer languages."""
    sel = campaign_submissions(base, key, comp, models)
    return [(s, d) for s, d in LANGS if all((m, s) in sel for m in models)]

# The harness stores CodeCarbon's total energy, which adds an UNMEASURED RAM
# estimate to the RAPL package energy. In CodeCarbon's default "machine" mode the
# estimate is a constant power set by the host's RAM size (16 GB -> 2 DIMMs x 5 W
# on the measurement host), accrued over the whole tracker window. We report the
# package energy only, so we subtract that power times the measured time (per
# call on LeetCode, per run on CLBG). CodeCarbon also skips its final reading
# when a window is shorter than 50 ms, so such a CLBG run has no valid energy.
RAM_ESTIMATE_W = 10.0
MIN_ENERGY_WINDOW_MS = 50.0

def package_energy(comp, energy_j, runtime_ms):
    if energy_j is None or runtime_ms is None:
        return None
    if comp == "clbg-10" and runtime_ms < MIN_ENERGY_WINDOW_MS:
        return None
    e = energy_j - RAM_ESTIMATE_W * runtime_ms / 1000.0
    return e if e > 0 else None

# Peak memory re-measured without the compiler in the measured process
# (scripts/remeasure_memory.py on the platform): analysis/data/<comp>_memory_remeasured.jsonl.
# When the file exists, every memory value comes from it (mean of its readings),
# never mixed with the earlier values; an attempt it lacks has no memory value.
_MEM_REMEASURED = {}

def remeasured_memory(comp):
    if comp not in _MEM_REMEASURED:
        p = Path(__file__).resolve().parent / "data" / f"{comp}_memory_remeasured.jsonl"
        d = None
        if p.exists():
            d = {}
            for line in p.open():
                r = json.loads(line)
                if r.get("memory_mb"):
                    d[r["attempt_id"]] = statistics.mean(r["memory_mb"])
        _MEM_REMEASURED[comp] = d
    return _MEM_REMEASURED[comp]

def one_memory_reading(a):
    """The paper's memory value: one reading per solution (setup section). An
    attempt with several readings (the LeetCode re-measure of JavaScript,
    TypeScript, C++, and Java took three) gives its first one."""
    for r in sorted(a.get("runs") or [], key=lambda r: r.get("run_index") or 0):
        if r.get("status_msg") == "Accepted" and r.get("memory_mb") is not None:
            return r["memory_mb"]
    return a.get("avg_memory_mb")

def fetch_compare(base, key, comp, lang, models):
    """Same shape as the old /api/compare response, built from the campaign
    submissions: {"problems": [{problem_slug, level, per_model: {model:
    {accepted, runtime_ms, runtime_energy_j, memory_mb}}}], "by_tag": [...]}."""
    sel = campaign_submissions(base, key, comp, models)
    mem_new = remeasured_memory(comp)
    invalid = invalid_solutions(comp)
    rows, levels, tags = {}, {}, {}
    for (m, l), (_s, per) in sel.items():
        if l != lang:
            continue
        for p, a in per.items():
            levels.setdefault(p, a.get("level"))
            tags.setdefault(p, [t.strip() for t in (a.get("tags") or "").split(",") if t.strip()])
            rows.setdefault(p, {})[m] = {
                "accepted": bool(a.get("accepted")),
                "runtime_ms": a.get("avg_runtime_ms"),
                "runtime_energy_j": package_energy(comp, a.get("avg_runtime_energy_j"),
                                                   a.get("avg_runtime_ms")),
                "memory_mb": (one_memory_reading(a) if mem_new is None
                              else mem_new.get(a.get("attempt_id")))}
            if (l, p, m) in invalid:
                rows[p][m].update(runtime_ms=None, runtime_energy_j=None, memory_mb=None)
    by_tag = {}
    for p, pm in rows.items():
        for m, e in pm.items():
            for t in tags.get(p, []):
                d = by_tag.setdefault((m, t), [0, 0])
                d[1] += 1
                d[0] += 1 if e["accepted"] else 0
    return {"problems": [{"problem_slug": p, "level": levels.get(p), "per_model": rows[p]}
                         for p in sorted(rows)],
            "by_tag": [{"model_name": m, "tag": t, "n_accepted": a, "n_attempted": n}
                       for (m, t), (a, n) in sorted(by_tag.items())]}


# --------------------------------------------------------------------------- stats
def kendall_tau_b(x, y):
    """tau-b over paired value vectors (handles ties)."""
    n = len(x); C = D = Tx = Ty = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = x[i] - x[j], y[i] - y[j]
            s = dx * dy
            if s > 0: C += 1
            elif s < 0: D += 1
            else:
                if dx == 0 and dy != 0: Tx += 1
                elif dy == 0 and dx != 0: Ty += 1
    denom = math.sqrt((C + D + Tx) * (C + D + Ty))
    return (C - D) / denom if denom else 0.0

def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v); i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]: j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1): r[order[k]] = avg
        i = j + 1
    return r

def spearman_rho(x, y):
    rx, ry = _ranks(x), _ranks(y)
    n = len(x); mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


# --------------------------------------------------------------------------- RQ1
# Accepted solutions that never set a golden value, because they skip the work of
# the benchmark or do it outside their language (C3 of the paper). They still
# enter every duel and every ratio: analysis/data/<comp>_baseline_exclude.json.
def baseline_exclusions(comp):
    p = Path(__file__).resolve().parent / "data" / f"{comp}_baseline_exclude.json"
    if not p.exists():
        return set()
    return {(e["language"], e["problem"], e["model"]) for e in json.loads(p.read_text())}


# Accepted solutions that leave every performance ranking, ratio, and golden value,
# because they do their work in another language (C3 of the paper). They keep their
# verdict, so they still count for pass@1, like a solution that an admin labels
# invalid on the platform: analysis/data/<comp>_invalid.json.
def invalid_solutions(comp):
    p = Path(__file__).resolve().parent / "data" / f"{comp}_invalid.json"
    if not p.exists():
        return set()
    return {(e["language"], e["problem"], e["model"]) for e in json.loads(p.read_text())}


def compute_rq1(base, key, comp, models, baseline=None):
    """-> dict with per-(model,lang,metric) median ratio, mean ratios, kendall.

    baseline: None  -> each cell's denominator is the LIVE best in the pool
              dict  -> {lang: {problem_slug: {metric: value}}} frozen bests; the
                       denominator is the frozen value, so a model's ratio only
                       moves when its own code changes (SPEC-style fixed reference).
                       Cells absent from the frozen baseline are skipped.
    """
    # score[lang][model][metric] = median ratio; raw per-problem for debugging dropped
    score = {s: {m: {} for m in models} for s in LANG_SLUGS}
    bests = {}               # live per-cell mins, always recorded for --freeze-baseline
    winners = {}             # which model produced each live per-cell best (same keys)
    missing_baseline = 0     # (problem,lang,metric) cells absent from a frozen baseline
    n_problems = {}
    by_level = {m: {} for m in models}   # model -> level -> [accepted, total] over all langs
    by_tag = {m: {} for m in models}     # model -> tag -> [accepted, attempted] over all langs
    excluded = baseline_exclusions(comp)
    pooled = {m: {mk: [] for mk, _ in METRICS} for m in models}   # every cell's ratio
    for slug, _disp in LANGS:
        data = fetch_compare(base, key, comp, slug, models)
        problems = data.get("problems", [])
        n_problems[slug] = len(problems)
        for row in data.get("by_tag", []):
            m = row.get("model_name")
            if m in by_tag:
                d = by_tag[m].setdefault(row["tag"], [0, 0])
                d[0] += row.get("n_accepted") or 0
                d[1] += row.get("n_attempted") or 0
        # per-problem best (min) correct value for each metric
        ratios = {m: {mk: [] for mk, _ in METRICS} for m in models}
        for p in problems:
            pm = p.get("per_model", {})
            pslug = p.get("problem_slug")
            lvl = p.get("level") or "?"
            for m in models:
                d = by_level[m].setdefault(lvl, [0, 0])
                d[1] += 1
                if (pm.get(m) or {}).get("accepted"):
                    d[0] += 1
            for mk, _ in METRICS:
                # (value, model) per correct candidate; min() breaks ties by model
                # name, so the recorded winner is deterministic.
                cand = [(pm[m][mk], m) for m in models
                        if m in pm and pm[m].get("accepted") and pm[m].get(mk) is not None
                        and (slug, pslug, m) not in excluded]
                if not cand:
                    continue
                live_best, winner = min(cand)
                if live_best <= 0:
                    continue
                # Always record the live best and its producer so --freeze-baseline
                # can snapshot both.
                bests.setdefault(slug, {}).setdefault(pslug, {})[mk] = live_best
                winners.setdefault(slug, {}).setdefault(pslug, {})[mk] = winner
                # Pick the denominator: the frozen best when a baseline is given,
                # else the live best. A cell missing from the frozen baseline is
                # not part of that frozen suite version, so skip it.
                if baseline is not None:
                    fb = (baseline.get(slug, {}).get(pslug, {}) or {}).get(mk)
                    if fb is None or fb <= 0:
                        missing_baseline += 1
                        continue
                    best = fb
                else:
                    best = live_best
                for m in models:
                    e = pm.get(m)
                    if e and e.get("accepted") and e.get(mk) is not None:
                        ratios[m][mk].append(e[mk] / best)
        for m in models:
            for mk, _ in METRICS:
                rs = ratios[m][mk]
                # Geometric mean within a language, over the per-problem ratios.
                score[slug][m][mk] = statistics.geometric_mean(rs) if rs else None
                pooled[m][mk] += rs
    # One number per LLM and metric: the geometric mean of its ratios over all the
    # cells it solves, in every language (as the evaluation section defines it, and
    # as the pairwise comparisons pool cells), so each solution counts once. Ratios
    # are multiplicative, so we combine them with the GEOMETRIC mean, not the
    # arithmetic mean: the arithmetic mean of ratios is reference-dependent and
    # biased, while the geometric mean is the standard baseline-independent
    # summary for normalized results.
    mean_ratio = {m: {} for m in models}
    for m in models:
        for mk, _ in METRICS:
            vals = pooled[m][mk]
            mean_ratio[m][mk] = statistics.geometric_mean(vals) if vals else None
    # Kendall tau-b across language pairs, for latency and energy. Keep the mean
    # plus the least- and most-agreeing pair (the prose cites the range).
    disp_of = dict(LANGS)
    kendall = {}
    for mk, _ in [("runtime_ms", "Lat"), ("runtime_energy_j", "En")]:
        pairs = []
        for i in range(len(LANG_SLUGS)):
            for j in range(i + 1, len(LANG_SLUGS)):
                a, b = LANG_SLUGS[i], LANG_SLUGS[j]
                common = [m for m in models
                          if score[a][m][mk] is not None and score[b][m][mk] is not None]
                if len(common) >= 3:
                    t = kendall_tau_b([score[a][m][mk] for m in common],
                                      [score[b][m][mk] for m in common])
                    pairs.append((round(t, 3), disp_of[a], disp_of[b]))
        if pairs:
            kendall[mk] = {"mean": round(statistics.mean(p[0] for p in pairs), 3),
                           "min": min(pairs), "max": max(pairs)}
        else:
            kendall[mk] = None
    return dict(score=score, mean_ratio=mean_ratio, n_problems=n_problems,
                kendall=kendall, by_level=by_level, by_tag=by_tag,
                bests=bests, winners=winners, missing_baseline=missing_baseline)


# --------------------------------------------------------------------------- RQ2
def compute_rq2(base, key, comp, models, rq1):
    """pass@1, $/sol, response tokens per model (aggregated over the 6 langs)."""
    # The campaign submission of each (model, language); a problem counts as
    # solved when its (latest) attempt in that submission is accepted.
    sel = campaign_submissions(base, key, comp, models)
    agg = {m: dict(cost=0.0, solved=0, tok=0, natt=0) for m in models}
    for (m, lang), (s, per) in sel.items():
        a, t = agg[m], campaign_totals(base, key, s)
        a["cost"] += t["cost"]
        a["solved"] += sum(1 for at in per.values() if at.get("accepted"))
        a["tok"] += t["tok"]
        a["natt"] += t["n"]
    out = {}
    for m in models:
        a = agg[m]
        total_slots = sum(rq1["n_problems"].get(s, 0) for s, _ in LANGS)
        bl = rq1["by_level"].get(m, {})
        out[m] = dict(
            pass1=100.0 * a["solved"] / total_slots if total_slots else None,
            dollar_per_sol=(a["cost"] / a["solved"]) if a["solved"] else None,
            tok=round(a["tok"] / a["natt"]) if a["natt"] else None,
            # response tokens per correct solution, the token analogue of $/sol
            tok_per_sol=(a["tok"] / a["solved"]) if a["solved"] else None,
            lat=rq1["mean_ratio"][m]["runtime_ms"],
            en=rq1["mean_ratio"][m]["runtime_energy_j"],
            mem=rq1["mean_ratio"][m]["memory_mb"],
            # pass@1 within each difficulty level (accept / slots at that level)
            bylevel={lvl: (100.0 * acc / tot if tot else None) for lvl, (acc, tot) in bl.items()},
        )
    # Pareto over {pass1 max, cost min, lat min}; None cost = free = 0
    def dominates(x, y):
        cx = 0.0 if out[x]["dollar_per_sol"] is None else out[x]["dollar_per_sol"]
        cy = 0.0 if out[y]["dollar_per_sol"] is None else out[y]["dollar_per_sol"]
        ge = (out[x]["pass1"] >= out[y]["pass1"] and cx <= cy and out[x]["lat"] <= out[y]["lat"])
        gt = (out[x]["pass1"] > out[y]["pass1"] or cx < cy or out[x]["lat"] < out[y]["lat"])
        return ge and gt
    have = [m for m in models if out[m]["pass1"] is not None and out[m]["lat"] is not None]
    for m in models:
        out[m]["pareto"] = (m in have and not any(dominates(o, m) for o in have if o != m))
    # Spearman correlations across models (for the prose). Energy is absent for
    # some models on judge-only competitions, so correlate pairwise-complete:
    # keep only the models where BOTH quantities exist, and report None when
    # fewer than 3 such models remain (no spurious rho from a tiny overlap).
    def vec(f): return [out[m][f] for m in have]
    cost_v = [0.0 if out[m]["dollar_per_sol"] is None else out[m]["dollar_per_sol"] for m in have]
    def _sp(xs, ys):
        pairs = [(a, b) for a, b in zip(xs, ys) if a is not None and b is not None]
        if len(pairs) < 3:
            return None
        return round(spearman_rho([a for a, _ in pairs], [b for _, b in pairs]), 3)
    spear = {}
    if len(have) >= 3:
        spear = {
            "cost_latency": _sp(cost_v, vec("lat")),
            "cost_energy": _sp(cost_v, vec("en")),
            "pass1_latency": _sp(vec("pass1"), vec("lat")),
            "pass1_energy": _sp(vec("pass1"), vec("en")),
            "energy_latency": _sp(vec("en"), vec("lat")),
            "tok_latency": _sp(vec("tok"), vec("lat")),
            "tok_energy": _sp(vec("tok"), vec("en")),
            "tok_per_sol_latency": _sp(vec("tok_per_sol"), vec("lat")),
            "tok_per_sol_energy": _sp(vec("tok_per_sol"), vec("en")),
            "tok_per_sol_cost": _sp(vec("tok_per_sol"), cost_v),
        }
    return out, spear


# --------------------------------------------------------------------------- emit
def _fmt(x, nd=2):
    return "--" if x is None else f"{x:.{nd}f}"

def emit_rq1_integrated(ratios):
    """Table 5: per-language latency and geometric-mean ratios of both
    competitions side by side, in the layout of emit_cost_table, sorted by
    LeetCodeComp geometric-mean latency, with the best value of each column in
    bold. Reads the <comp>_ratios.json written by emit_ratios_json."""
    comps = ("leetcode-99-perf", "clbg-10")
    rows = {c: {m: i for i, m in enumerate(ratios[c]["model_backends"])} for c in comps}
    order = ratios["leetcode-99-perf"]["model_backends"]   # already sorted by latency
    # Columns are the languages of either competition; a competition may not
    # have a language yet (competition_languages), so look values up by slug.
    langs = [(s, d) for s, d in ALL_LANGS if any(s in ratios[c]["lang_slugs"] for c in comps)]
    absent = [(c, s) for c in comps for s, _ in langs if s not in ratios[c]["lang_slugs"]]
    def val(c, m, key, slug=None):
        i = rows[c].get(m)
        if i is None:
            return None
        if slug is None:
            return ratios[c][key][i]
        if slug not in ratios[c]["lang_slugs"]:
            return None
        return ratios[c][key][i][ratios[c]["lang_slugs"].index(slug)]
    cols = ([("heatmap_runtime", s) for s, _ in langs]
            + [("mean_runtime", None), ("mean_energy", None), ("mean_memory", None)])
    best = {(k, j, c): min((round(v, 2) for m in order
                            if (v := val(c, m, k, j)) is not None), default=None)
            for k, j in cols for c in comps}
    n = 2 * len(cols)
    L = [r"\begin{table*}[t]", r"\centering", r"\scriptsize", r"\setlength{\tabcolsep}{2pt}",
         r"\caption{Latency, energy, and peak memory of correct programs by \llm on \leetcodeComp (LC)",
         r"and \clbgComp (CLBG), sorted by geometric-mean latency on LC (lower is better). Bold marks",
         r"the best value in each column, and a dash marks a language where the \llm has no correct program."
         + "".join(r" %s has no %s measurements yet." % (COMP_MACRO[c], LANG_FULL[s]) for c, s in absent)
         + "}",
         r"\label{tab:rq1-efficiency}",
         r"\begin{tabular}{@{}l " + " ".join(["rr"] * len(cols)) + r"@{}}", r"\toprule",
         r" & \multicolumn{%d}{c}{\textbf{Latency ratio by language}} & "
         r"\multicolumn{6}{c}{\textbf{Geometric-mean ratio}} \\" % (2 * len(langs)),
         r"\cmidrule(lr){2-%d}\cmidrule(l){%d-%d}" % (2 * len(langs) + 1, 2 * len(langs) + 2, n + 1),
         r" & " + " & ".join(r"\multicolumn{2}{c}{\textbf{%s}}" % h for h in
                             [d for _, d in langs] + ["Latency", "Energy", "Peak memory"]) + r" \\",
         "".join(r"\cmidrule(lr){%d-%d}" % (2 + 2 * i, 3 + 2 * i) for i in range(len(cols))),
         r"\textbf{\llm} & " + " & ".join([r"\textbf{LC} & \textbf{CLBG}"] * len(cols)) + r" \\",
         r"\midrule"]
    for m in order:
        cells = []
        for k, j in cols:
            for c in comps:
                v = val(c, m, k, j)
                s = _fmt(v)
                if v is not None and round(v, 2) == best[(k, j, c)]:
                    s = r"\textbf{%s}" % s
                cells.append(s)
        L.append(r"\code{%s} & " % disp(m) + " & ".join(cells) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    return "\n".join(L) + "\n"

_LVL_ORDER = ["Easy", "Medium", "Hard"]
_LVL_DISP = {"Easy": "Easy", "Medium": "Med.", "Hard": "Hard"}

def emit_rq2_table(comp, comp_macro, rq2, models):
    order = sorted([m for m in models if rq2[m]["pass1"] is not None],
                   key=lambda m: rq2[m]["pass1"], reverse=True)
    # Difficulty block only when the competition spans more than one level
    # (LeetCode has Easy/Medium/Hard; CLBG is all one level, so it is dropped).
    present = {lvl for m in order for lvl, v in rq2[m]["bylevel"].items() if v is not None}
    levels = [l for l in _LVL_ORDER if l in present] + sorted(present - set(_LVL_ORDER))
    show_diff = len(levels) > 1
    nd = len(levels)
    L = []
    L.append(r"\begin{table*}[t]")
    L.append(r"\centering"); L.append(r"\footnotesize")
    L.append(r"\setlength{\tabcolsep}{4pt}")
    L.append(r"\caption{Correctness, generation cost, and performance (latency, energy, peak")
    L.append(r"memory) by \llm on " + comp_macro + r", sorted by pass@1. Each performance value is")
    L.append(r"the geometric-mean ratio to the best correct solution (lower is better). $\bullet$ marks the")
    L.append(r"Pareto frontier over correctness, cost, and latency.}")
    L.append(r"\label{tab:rq2-tradeoffs-" + comp + "}")
    if show_diff:
        L.append(r"\begin{tabular}{@{}l l r " + ("r" * nd) + r" r r rrr c@{}}")
        L.append(r"\toprule")
        L.append((r" & & & \multicolumn{%d}{c}{\textbf{By difficulty (\%%)}} & & & "
                  r"\multicolumn{3}{c}{\textbf{Performance}} & \\") % nd)
        L.append(r"\cmidrule(lr){4-%d}\cmidrule(lr){%d-%d}" % (3 + nd, 6 + nd, 8 + nd))
        L.append(r"\textbf{\llm} & \textbf{Provider} & \textbf{pass@1 (\%)} & "
                 + " & ".join(r"\textbf{%s}" % _LVL_DISP.get(l, l) for l in levels)
                 + r" & \textbf{\$/sol} & \textbf{Tok} & \textbf{Lat} & \textbf{En} & "
                 r"\textbf{Mem} & \textbf{Par.} \\")
    else:
        L.append(r"\begin{tabular}{@{}l l r r r rrr c@{}}")
        L.append(r"\toprule")
        L.append(r" & & & & & \multicolumn{3}{c}{\textbf{Performance}} & \\")
        L.append(r"\cmidrule(lr){6-8}")
        L.append(r"\textbf{\llm} & \textbf{Provider} & \textbf{pass@1 (\%)} & \textbf{\$/sol} & "
                 r"\textbf{Tok} & \textbf{Lat} & \textbf{En} & \textbf{Mem} & \textbf{Par.} \\")
    L.append(r"\midrule")
    for m in order:
        r = rq2[m]
        dps = "--" if r["dollar_per_sol"] is None else f"{r['dollar_per_sol']:.3f}"
        tok = "--" if r["tok"] is None else f"{r['tok']:,}"
        tail = r"%s & %s & %s & %s & %s & %s \\" % (
            dps, tok, _fmt(r["lat"]), _fmt(r["en"]), _fmt(r["mem"]),
            r"$\bullet$" if r["pareto"] else "")
        if show_diff:
            diff = " & ".join(("--" if r["bylevel"].get(l) is None else f"{r['bylevel'][l]:.0f}")
                              for l in levels)
            L.append(r"\code{%s} & %s & %s & %s & %s" % (
                disp(m), provider(m), _fmt(r["pass1"], 1), diff, tail))
        else:
            L.append(r"\code{%s} & %s & %s & %s" % (
                disp(m), provider(m), _fmt(r["pass1"], 1), tail))
    L.append(r"\bottomrule"); L.append(r"\end{tabular}"); L.append(r"\end{table*}")
    return "\n".join(L) + "\n"

def emit_ratios_json(rq1, models):
    order = sorted([m for m in models if rq1["mean_ratio"][m]["runtime_ms"] is not None],
                   key=lambda m: rq1["mean_ratio"][m]["runtime_ms"])
    return dict(
        languages=[d for _, d in LANGS], lang_slugs=LANG_SLUGS,
        models=[disp(m) for m in order],
        model_backends=list(order),   # backend names, for stable color mapping
        heatmap_runtime=[[rq1["score"][s][m]["runtime_ms"] for s, _ in LANGS] for m in order],
        heatmap_energy=[[rq1["score"][s][m]["runtime_energy_j"] for s, _ in LANGS] for m in order],
        heatmap_memory=[[rq1["score"][s][m]["memory_mb"] for s, _ in LANGS] for m in order],
        mean_runtime=[rq1["mean_ratio"][m]["runtime_ms"] for m in order],
        mean_energy=[rq1["mean_ratio"][m]["runtime_energy_j"] for m in order],
        mean_memory=[rq1["mean_ratio"][m]["memory_mb"] for m in order],
    )


def emit_appendix_tables(comp, comp_macro, rq1, models):
    """Per-language ranking tables (tab:app-<lang>): models ranked independently
    by latency, energy, and peak memory within each language."""
    L = []
    for slug, _disp in LANGS:
        ranked = {}
        for mk, _ in METRICS:
            ranked[mk] = sorted([m for m in models if rq1["score"][slug][m][mk] is not None],
                                key=lambda m: rq1["score"][slug][m][mk])
        nrows = max((len(ranked[mk]) for mk, _ in METRICS), default=0)
        L.append(r"\begin{table}[H]")
        L.append(r"\centering"); L.append(r"\footnotesize")
        L.append(r"\setlength{\tabcolsep}{4pt}")
        L.append(r"\caption{\llms on \code{%s} (%s), ranked independently by latency, "
                 r"energy, and peak memory. Each value is the geometric-mean normalized "
                 r"ratio (lower is better).}" % (slug, comp_macro))
        L.append(r"\label{tab:app-%s-%s}" % (comp, slug))
        L.append(r"\begin{tabular}{@{}rl rl rl@{}}")
        L.append(r"\toprule")
        L.append(r"\multicolumn{2}{c}{\textbf{Latency}} & \multicolumn{2}{c}{\textbf{Energy}} "
                 r"& \multicolumn{2}{c}{\textbf{Peak memory}} \\")
        L.append(r"\cmidrule(lr){1-2}\cmidrule(lr){3-4}\cmidrule(l){5-6}")
        L.append(r"\midrule")
        for i in range(nrows):
            cells = []
            for mk, _ in METRICS:
                rr = ranked[mk]
                if i < len(rr):
                    m = rr[i]
                    cells.append(r"%.2f & \code{%s}" % (rq1["score"][slug][m][mk], disp(m)))
                else:
                    cells.append(r" & ")
            L.append(" & ".join(cells) + r" \\")
        L.append(r"\bottomrule"); L.append(r"\end{tabular}"); L.append(r"\end{table}")
        L.append("")
    return "\n".join(L) + "\n"


def emit_difficulty_table(rq2, models):
    """Appendix table: pass@1 per LeetCode difficulty level, sorted by pass@1."""
    order = sorted([m for m in models if rq2[m]["pass1"] is not None],
                   key=lambda m: rq2[m]["pass1"], reverse=True)
    L = [r"\begin{table}[H]", r"\centering", r"\footnotesize",
         r"\setlength{\tabcolsep}{6pt}",
         r"\caption{Accept rate by \leetcode difficulty on \leetcodeComp (percent), sorted by "
         r"pass@1. The set holds 33~problems at each level, each measured in the "
         r"\totalExecutedLanguages languages.}",
         r"\label{tab:difficulty-breakdown}", r"\begin{tabular}{@{}l rrr@{}}", r"\toprule",
         r"\textbf{\llm} & \textbf{Easy} & \textbf{Med.} & \textbf{Hard} \\", r"\midrule"]
    for m in order:
        bl = rq2[m]["bylevel"]
        L.append(r"\code{%s} & " % disp(m) + " & ".join(
            "--" if bl.get(l) is None else f"{bl[l]:.0f}" for l in _LVL_ORDER) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(L) + "\n"

def emit_baseline_contrib(winners, models):
    """Appendix table: how many golden-baseline values each model supplies, per
    language (all three metrics), with the maximum of each column in bold."""
    cnt = {m: {s: 0 for s in LANG_SLUGS} for m in models}
    for s, probs in winners.items():
        for cell in probs.values():
            for mdl in cell.values():
                if mdl in cnt and s in cnt[mdl]:
                    cnt[mdl][s] += 1
    tot = {m: sum(cnt[m].values()) for m in models}
    order = sorted(models, key=lambda m: (-tot[m], disp(m)))
    col_max = {s: max(cnt[m][s] for m in models) for s in LANG_SLUGS}
    tot_max = max(tot.values())
    b = lambda v, mx: r"\textbf{%d}" % v if v == mx and v > 0 else str(v)
    L = [r"\scriptsize", r"\setlength{\tabcolsep}{2.5pt}",
         r"\begin{tabular}{@{}l " + "r" * len(LANGS) + r" r@{}}", r"\toprule",
         r"\textbf{\llm} & " + " & ".join(r"\textbf{%s}" % d for _, d in LANGS)
         + r" & \textbf{Total} \\", r"\midrule"]
    for m in order:
        L.append(r"\code{%s} & " % disp(m) + " & ".join(b(cnt[m][s], col_max[s]) for s in LANG_SLUGS)
                 + " & " + b(tot[m], tot_max) + r" \\")
    L.append(r"\midrule")
    L.append(r"\textbf{Total} & " + " & ".join(str(sum(cnt[m][s] for m in models)) for s in LANG_SLUGS)
             + " & " + str(sum(tot.values())) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(L) + "\n"

def compute_shared(base, key, comp, models, rq1, baseline=None):
    """Scores over the SHARED cells only: the problems that every model solved
    in a language (with a value for the metric). Same aggregation as the main
    score (geometric mean within a language, then over the languages), so it
    checks whether scoring each model on its own solved set biases the ranking.
    -> ({model: {metric: score}}, {metric: number of shared cells})"""
    vals = {m: {mk: [] for mk, _ in METRICS} for m in models}
    n_cells = {mk: 0 for mk, _ in METRICS}
    for slug, _d in LANGS:
        per = {m: {mk: [] for mk, _ in METRICS} for m in models}
        for p in fetch_compare(base, key, comp, slug, models).get("problems", []):
            pm = p.get("per_model", {})
            for mk, _ in METRICS:
                best = ((baseline or rq1["bests"]).get(slug, {}).get(p["problem_slug"], {}) or {}).get(mk)
                if not best or not all(pm.get(m, {}).get("accepted") and pm[m].get(mk)
                                       for m in models):
                    continue
                n_cells[mk] += 1
                for m in models:
                    per[m][mk].append(pm[m][mk] / best)
        for m in models:
            for mk, _ in METRICS:
                if per[m][mk]:
                    vals[m][mk].append(statistics.geometric_mean(per[m][mk]))
    score = {m: {mk: statistics.geometric_mean(v) if v else None for mk, v in d.items()}
             for m, d in vals.items()}
    return score, n_cells

def emit_shared_table(comp_macro, rq1, shared, n_cells, models):
    """Appendix table: each model's score over its solved problems (All) next
    to its score over the shared cells (Shared), with ranks, sorted by the
    shared latency score."""
    def ranks(get):
        order = sorted(models, key=get)
        return {m: i + 1 for i, m in enumerate(order)}
    cols = [mk for mk, _ in METRICS]
    r_all = {mk: ranks(lambda m, mk=mk: rq1["mean_ratio"][m][mk]) for mk in cols}
    r_sh = {mk: ranks(lambda m, mk=mk: shared[m][mk]) for mk in cols}
    L = [r"\begin{table}[H]", r"\centering", r"\footnotesize", r"\setlength{\tabcolsep}{3pt}",
         r"\caption{Geometric-mean ratio and rank of each \llm on " + comp_macro +
         r" over the problems that it solved (All) and over the %d~cells that every \llm solved (Shared).}"
         % n_cells["runtime_ms"],
         r"\label{tab:shared-cells}",
         r"\begin{tabular}{@{}l" + " rr" * len(cols) + r"@{}}", r"\toprule",
         r" & \multicolumn{2}{c}{\textbf{Latency}} & \multicolumn{2}{c}{\textbf{Energy}}"
         r" & \multicolumn{2}{c}{\textbf{Peak memory}} \\",
         r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}",
         r"\textbf{\llm}" + r" & \textbf{All} & \textbf{Shared}" * len(cols) + r" \\", r"\midrule"]
    for m in sorted(models, key=lambda m: shared[m]["runtime_ms"]):
        cells = []
        for mk in cols:
            cells.append(f"{rq1['mean_ratio'][m][mk]:.2f} ({r_all[mk][m]})")
            cells.append(f"{shared[m][mk]:.2f} ({r_sh[mk][m]})")
        L.append(r"\code{%s} & " % disp(m) + " & ".join(cells) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(L) + "\n"

# A submission that sends the code of an earlier sweep again for a new verdict
# receives all its attempts at once, so the gaps between them do not measure a
# sweep. It keeps the sweep time of the submission whose code it carries:
# analysis/data/<comp>_sweep_time_from.json maps its id to that submission's id.
def sweep_time_sources(comp):
    p = Path(__file__).resolve().parent / "data" / f"{comp}_sweep_time_from.json"
    if not p.exists():
        return {}
    return {int(k): int(v) for k, v in json.loads(p.read_text()).items()}


def campaign_cost(base, key, comp, models):
    """Generation and measurement effort of the campaign for one competition,
    in total and per model (one sweep, i.e. one submission per language).
    Validation+measurement time: for each model, the wall time between its
    consecutive solutions (the client sends one solution and waits for its
    verdict), excluding pauses over two hours, minus the generation time."""
    from datetime import datetime as _dt
    sel = campaign_submissions(base, key, comp, models)
    per = {}
    for (m, _l), (s, _p) in sel.items():
        d, t = per.setdefault(m, dict(n_sols=0, tokens=0, cost=0.0, gen_s=0.0)), campaign_totals(base, key, s)
        d["n_sols"] += t["n"]
        d["tokens"] += t["tok"]
        d["cost"] += t["cost"]
        d["gen_s"] += t["gen_s"]
    src = sweep_time_sources(comp)
    subs = {s["id"]: s for s in _submissions(base, key)} if src else {}
    for m, d in per.items():
        busy = gen = 0.0
        atts = sorted((a for (mm, _l), (s, _p) in sel.items() if mm == m
                       for a in campaign_attempts(base, key, subs.get(src.get(s["id"]), s))),
                      key=lambda a: a.get("submitted_at") or "")
        for x, y in zip(atts, atts[1:]):
            gap = (_dt.fromisoformat(y["submitted_at"]) - _dt.fromisoformat(x["submitted_at"])).total_seconds()
            if gap < 7200:
                busy += gap
                gen += (y.get("wall_clock_ms") or 0) / 1000
        d["valmeas_s"] = busy - gen
    out = dict(n_subs=len(sel), n_models=len(per))
    for k in ("n_sols", "tokens", "cost", "gen_s", "valmeas_s"):
        out[k] = sum(d[k] for d in per.values())
    out["per_model"] = per
    return out

def emit_cost_table(rq2s, costs):
    """Cost table: pass@1 and the cost of each model's sweep on both
    competitions, in response tokens and in USD, per correct solution and in
    total, with the generation and measurement time. Sorted by LeetCodeComp
    USD per correct solution, with the best value of each column in bold and a
    last row with the overall rates and the sums over all sweeps."""
    comps = ("leetcode-99-perf", "clbg-10")
    def row(c, m):
        q, k = rq2s[c][m], costs[c]["per_model"][m]
        return [q["pass1"], q["tok_per_sol"], k["tokens"], q["dollar_per_sol"], k["cost"],
                k["gen_s"] / 3600, k["valmeas_s"] / 3600]
    def total(c):
        k, q = costs[c], rq2s[c]
        solved = sum(k["per_model"][m]["tokens"] / q[m]["tok_per_sol"]
                     for m in q if q[m]["tok_per_sol"])
        return [statistics.mean(q[m]["pass1"] for m in q), k["tokens"] / solved, k["tokens"],
                k["cost"] / solved, k["cost"], k["gen_s"] / 3600, k["valmeas_s"] / 3600]
    fmt = [lambda v: f"{v:.1f}", lambda v: f"{v:,.0f}", lambda v: f"{v / 1e3:,.0f}K",
           lambda v: f"{v:.4f}", lambda v: f"{v:.2f}", lambda v: f"{v:.1f}", lambda v: f"{v:.1f}"]
    num = lambda s: float(s.rstrip("K").replace(",", ""))
    aggs = [max] + [min] * 6
    order = sorted(rq2s["leetcode-99-perf"], key=lambda m: rq2s["leetcode-99-perf"][m]["dollar_per_sol"])
    rows = {(c, m): row(c, m) for c in comps for m in order}
    best = {(i, c): aggs[i](num(fmt[i](rows[c, m][i])) for m in order)
            for i in range(len(fmt)) for c in comps}
    def cells(vals, bold):
        out = []
        for i, f in enumerate(fmt):
            for c in comps:
                s = f(vals[c][i])
                out.append(r"\textbf{%s}" % s if bold and num(s) == best[i, c] else s)
        return " & ".join(out)
    groups = [(r"pass@1 (\%)", 1), ("Response tokens", 2), ("Cost (USD)", 2), ("Time (h)", 2)]
    subs = ["", "Per sol.", "Sweep", "Per sol.", "Sweep", r"Gen.", r"Meas."]
    rule = lambda a, b: r"\cmidrule(lr){%d-%d}" % (a, b)
    L = [r"\begin{table*}[t]", r"\centering", r"\scriptsize", r"\setlength{\tabcolsep}{3pt}",
         r"\caption{Correctness and cost of the sweep of each \llm (one submission per language) on "
         r"\leetcodeComp (LC) and \clbgComp (CLBG), sorted by the cost in USD per correct solution on LC. "
         r"Per sol.\ divides the response tokens or the generation cost by the number of correct solutions, "
         r"and Sweep gives the total of the sweep. Gen.\ and Meas.\ are the generation time and the "
         r"validation and measurement time of the sweep, where Meas.\ is the wall time between consecutive "
         r"solutions, without pauses over two hours, minus the generation time. The last row gives the "
         r"overall rates and the sums over all sweeps. Bold marks the best value in each column.}",
         r"\label{tab:cost}", r"\begin{tabular}{@{}l " + " ".join(["rr"] * len(fmt)) + r"@{}}", r"\toprule"]
    head, rules, col = [], [], 2
    for g, n in groups:
        head.append(r"\multicolumn{%d}{c}{\textbf{%s}}" % (2 * n, g))
        rules.append(rule(col, col + 2 * n - 1))
        col += 2 * n
    L += [" & " + " & ".join(head) + r" \\", "".join(rules),
          " & " + " & ".join(r"\multicolumn{2}{c}{\textbf{%s}}" % s if s else r"\multicolumn{2}{c}{}"
                             for s in subs) + r" \\",
          "".join(rule(2 + 2 * i, 3 + 2 * i) for i in range(1, len(fmt))),
          r"\textbf{\llm} & " + " & ".join([r"\textbf{LC} & \textbf{CLBG}"] * len(fmt)) + r" \\",
          r"\midrule"]
    for m in order:
        L.append(r"\code{%s} & " % disp(m) + cells({c: rows[c, m] for c in comps}, True) + r" \\")
    L += [r"\midrule",
          r"All \totalExecutedModels~\llms & " + cells({c: total(c) for c in comps}, False) + r" \\",
          r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    return "\n".join(L) + "\n"


# The reported roster: the 12 models frozen in baseline v1 plus glm-5.3-flash,
# which now has full submissions on the deployed platform (added 2026-10-07).
DEFAULT_MODELS = ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.5",
                  "gpt-5.4-mini", "claude-fable-5-1", "claude-opus-4-8",
                  "claude-opus-5-5", "claude-sonnet-5-5",
                  "deepseek/deepseek-v4.1-flash", "google/gemini-3.8-flash",
                  "x-ai/grok-4.6", "z-ai/glm-5.3-flash"]
# Models kept out of the reported analysis for now. Dropped from any roster,
# even one passed via --models, so the paper never includes them until removed.
EXCLUDE_MODELS = {"gemma4:e4b"}
COMP_MACRO = {"leetcode-99-perf": r"\leetcodeComp", "clbg-10": r"\clbgComp"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition", required=True)
    ap.add_argument("--base-url", default=os.environ.get("ARENA_BASE", "http://offline.invalid"))
    ap.add_argument("--api-key", default=os.environ.get("ARENA_KEY", ""))
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="comma-separated backend model_names")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "out"),
                    help="output root (writes tables/ and figs/data/)")
    ap.add_argument("--baseline", default=None,
                    help="normalize against a FROZEN per-cell baseline instead of the "
                         "live best: a version tag (resolves to "
                         "figs/data/<comp>_baseline_<tag>.json) or a path to such a file")
    ap.add_argument("--freeze-baseline", default=None, metavar="VERSION",
                    help="snapshot the current live per-cell bests to "
                         "figs/data/<comp>_baseline_<VERSION>.json for later --baseline runs")
    ap.add_argument("--cache-dir", default=None,
                    help="where to cache per-submission attempts "
                         "(default: $PERFARENA_CACHE or ~/.cache/perfarena-paper)")
    args = ap.parse_args()
    if args.cache_dir:
        global CACHE_DIR
        CACHE_DIR = Path(args.cache_dir)
    models = [m.strip() for m in args.models.split(",") if m.strip()
              and m.strip() not in EXCLUDE_MODELS]
    comp = args.competition
    macro = COMP_MACRO.get(comp, r"\code{%s}" % comp)
    global LANGS, LANG_SLUGS
    langs = competition_languages(args.base_url, args.api_key, comp, models)
    skipped = [s for s, _ in LANGS if (s, dict(LANGS)[s]) not in langs]
    if skipped:
        print(f"[{comp}] not every model ran {skipped}; leaving them out")
    LANGS, LANG_SLUGS = langs, [s for s, _ in langs]
    out = Path(args.out_dir)
    (out / "figs" / "data").mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(parents=True, exist_ok=True)

    # Resolve an optional frozen baseline: a bare version tag maps to the
    # conventional path, otherwise it is taken as a path.
    baseline_best, baseline_info, baseline_winners = None, {"mode": "live"}, {}
    if args.baseline:
        bpath = (out / "figs" / "data" / f"{comp}_baseline_{args.baseline}.json"
                 if ("/" not in args.baseline and not args.baseline.endswith(".json"))
                 else Path(args.baseline))
        loaded = json.loads(bpath.read_text())
        if loaded.get("competition") not in (None, comp):
            sys.exit(f"baseline {bpath} is for competition "
                     f"{loaded.get('competition')!r}, not {comp!r}")
        baseline_best = loaded.get("best") or {}
        baseline_winners = loaded.get("winner") or {}
        # Record a path relative to the paper root (never an absolute machine
        # path), so the committed stats artifact stays portable.
        try:
            rel_file = str(bpath.resolve().relative_to(out.resolve()))
        except ValueError:
            rel_file = bpath.name
        baseline_info = {"mode": "frozen", "version": loaded.get("version"),
                         "file": rel_file, "created_utc": loaded.get("created_utc")}
        print(f"[{comp}] normalizing against FROZEN baseline {bpath} "
              f"(version={loaded.get('version')})")

    print(f"[{comp}] RQ1: pulling /api/compare for {len(models)} models x {len(LANGS)} langs...")
    rq1 = compute_rq1(args.base_url, args.api_key, comp, models, baseline=baseline_best)
    if baseline_best is not None and rq1.get("missing_baseline"):
        print(f"[{comp}] WARNING: {rq1['missing_baseline']} (problem,language,metric) cells "
              f"were absent from the frozen baseline and were skipped")
    print(f"[{comp}] RQ2: pulling /api/submissions...")
    rq2, spear = compute_rq2(args.base_url, args.api_key, comp, models, rq1)

    (out / "figs" / "data" / f"{comp}_ratios.json").write_text(
        json.dumps(emit_ratios_json(rq1, models), indent=2))
    by_tag_out = {m: {t: {"accepted": a, "attempted": n,
                          "pass_rate": round(a / n, 3) if n else None}
                      for t, (a, n) in sorted(tags.items())}
                  for m, tags in rq1["by_tag"].items()}
    (out / "figs" / "data" / f"{comp}_stats.json").write_text(
        json.dumps({"kendall": rq1["kendall"], "spearman": spear,
                    "n_problems": rq1["n_problems"], "by_tag": by_tag_out,
                    "baseline": baseline_info}, indent=2))
    ratio_files = {c: out / "figs" / "data" / f"{c}_ratios.json" for c in COMP_MACRO}
    if all(f.exists() for f in ratio_files.values()):
        (out / "tables" / "rq1_efficiency.tex").write_text(emit_rq1_integrated(
            {c: json.loads(f.read_text()) for c, f in ratio_files.items()}))
    (out / "tables" / f"rq2_tradeoffs_{comp}.tex").write_text(
        emit_rq2_table(comp, macro, rq2, models))
    (out / "tables" / f"appendix_rankings_{comp}.tex").write_text(
        emit_appendix_tables(comp, macro, rq1, models))
    if comp == "leetcode-99-perf":
        (out / "tables" / "difficulty_breakdown_leetcode.tex").write_text(
            emit_difficulty_table(rq2, models))
        # CLBG has too few shared cells (8 of 70) for this check.
        shared, n_shared = compute_shared(args.base_url, args.api_key, comp, models,
                                          rq1, baseline=baseline_best)
        (out / "tables" / "shared_cells_leetcode.tex").write_text(
            emit_shared_table(macro, rq1, shared, n_shared, models))
        print(f"[{comp}] shared cells {n_shared}: " + ", ".join(
            f"{disp(m)} {shared[m]['runtime_ms']:.2f}"
            for m in sorted(models, key=lambda m: shared[m]["runtime_ms"])))
    winners = baseline_winners if baseline_best is not None else rq1["winners"]
    (out / "tables" / f"baseline_contrib_{comp}.tex").write_text(
        emit_baseline_contrib(winners, models))
    (out / "figs" / "data" / f"{comp}_rq2.json").write_text(json.dumps(rq2, indent=2))
    (out / "figs" / "data" / f"{comp}_cost.json").write_text(
        json.dumps(campaign_cost(args.base_url, args.api_key, comp, models), indent=2))
    rq2_files = {c: out / "figs" / "data" / f"{c}_rq2.json" for c in COMP_MACRO}
    cost_files = {c: out / "figs" / "data" / f"{c}_cost.json" for c in COMP_MACRO}
    if all(f.exists() for f in [*rq2_files.values(), *cost_files.values()]):
        (out / "tables" / "cost.tex").write_text(emit_cost_table(
            {c: json.loads(f.read_text()) for c, f in rq2_files.items()},
            {c: json.loads(f.read_text()) for c, f in cost_files.items()}))

    if args.freeze_baseline:
        n_cells = sum(len(ps) for ps in rq1["bests"].values())
        # contributors: how many frozen bests each model actually produced, so the
        # file shows which models the composite reference draws on (not just the
        # candidate pool it was selected from).
        contributors: dict[str, int] = {}
        for lang in rq1["winners"].values():
            for cell in lang.values():
                for mdl in cell.values():
                    contributors[mdl] = contributors.get(mdl, 0) + 1
        contributors = dict(sorted(contributors.items(),
                                   key=lambda kv: (-kv[1], kv[0])))
        bfile = out / "figs" / "data" / f"{comp}_baseline_{args.freeze_baseline}.json"
        bfile.write_text(json.dumps({
            "schema": "perfarena-baseline/1",
            "version": args.freeze_baseline,
            "competition": comp,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": args.base_url,
            "models_in_pool": models,        # candidates eligible to set a best
            "contributors": contributors,    # models that actually produced bests -> count
            "metrics": [mk for mk, _ in METRICS],
            "n_cells": n_cells,
            "best": rq1["bests"],            # {lang: {problem: {metric: value}}}
            "winner": rq1["winners"],        # {lang: {problem: {metric: model}}}
        }, indent=2))
        print(f"[{comp}] froze {n_cells} per-cell bests from "
              f"{len(contributors)} models -> {bfile}")

    print(f"[{comp}] kendall tau-b: {rq1['kendall']}")
    print(f"[{comp}] spearman: {spear}")
    print(f"[{comp}] wrote tables/rq1_efficiency.tex, tables/rq2_tradeoffs_{comp}.tex,"
          f" figs/data/{comp}_ratios.json, figs/data/{comp}_stats.json")


if __name__ == "__main__":
    main()
