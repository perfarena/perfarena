import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AnalysisPoint, AnalysisResponse, DiscriminationResponse, api,
} from "../lib/api";
import { formatPassAt1, formatPercentile } from "../lib/format";
import LanguageLink from "../components/LanguageLink";

/* /compare → "Correctness vs Performance" tab.
 *
 * Was the old "Analysis" page — renamed because the page IS the
 * correctness × performance question (does a more-correct model also run
 * faster?), and the new name says so on the tin.
 *
 * Two views over the same judged-submission pool:
 *   1. A per-language grid (correctness + performance side by side) — the
 *      cross-language comparison the platform was missing.
 *   2. A correctness-vs-performance scatter (pass@k on x, runtime/memory
 *      percentile on y), one dot per submission, coloured by language —
 *      to eyeball whether more-correct submissions are also faster (the
 *      literature says they're largely decoupled).
 *
 * NEW: an optional model filter at the top lets you restrict the entire
 * page to one or more models. The per-language aggregates and the global
 * Pearson r are recomputed CLIENT-SIDE from the filtered points so the
 * backend doesn't have to grow a parallel parameterised path.
 *
 * The scatter is hand-rolled inline SVG (same approach as RunsChart). */

// A small categorical palette; languages cycle through it deterministically.
const PALETTE = [
  "#7c8cff", "#4ade80", "#fbbf24", "#f87171", "#22d3ee", "#c084fc",
  "#fb923c", "#34d399", "#f472b6", "#a3e635", "#60a5fa", "#facc15",
  "#fca5a5", "#2dd4bf",
];

type XKey = "pass_at_1" | "pass_at_k";
type YKey = "avg_runtime_percentile" | "avg_memory_percentile";

export default function CorrectnessVsPerformance() {
  const [data, setData] = useState<AnalysisResponse | null>(null);
  const [disc, setDisc] = useState<DiscriminationResponse | null>(null);
  const [err, setErr] = useState("");
  const [xMetric, setXMetric] = useState<XKey>("pass_at_1");
  const [yMetric, setYMetric] = useState<YKey>("avg_runtime_percentile");

  // Model filter — start with "all models" (empty Set). User can toggle
  // individual models on/off; aggregates + scatter respect the filter.
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set());
  const [modelQuery, setModelQuery] = useState<string>("");

  useEffect(() => {
    api.analysis().then(setData).catch((e: unknown) => setErr(String(e)));
    api.discrimination().then(setDisc).catch(() => setDisc(null));
  }, []);

  // Models present in the analysis pool, sorted by descending submission
  // count so the most-active ones surface first in the picker.
  const allModels = useMemo<{ name: string; n: number }[]>(() => {
    if (!data) return [];
    const counts: Record<string, number> = {};
    for (const p of data.points) counts[p.model_name] = (counts[p.model_name] ?? 0) + 1;
    return Object.entries(counts)
      .map(([name, n]) => ({ name, n }))
      .sort((a, b) => b.n - a.n);
  }, [data]);

  // Filter the points pool. Empty filter = no filter = full pool.
  const filteredPoints = useMemo(() => {
    if (!data) return [] as AnalysisPoint[];
    if (selectedModels.size === 0) return data.points;
    return data.points.filter(p => selectedModels.has(p.model_name));
  }, [data, selectedModels]);

  // Recompute the by-language aggregates from the filtered point set, so the
  // table re-grouping mirrors whatever the user picked.
  const byLanguage = useMemo(() => {
    if (selectedModels.size === 0 && data) return data.by_language;
    return recomputeByLanguage(filteredPoints);
  }, [data, filteredPoints, selectedModels.size]);

  // Pearson r for the chosen (x, y). When no filter is active we re-use the
  // server-computed correlation (it's exact); otherwise recompute on the
  // filtered set. The two paths agree on the unfiltered case by construction.
  const corr = useMemo<number | null>(() => {
    if (!data) return null;
    if (selectedModels.size === 0) {
      if (xMetric === "pass_at_1" && yMetric === "avg_runtime_percentile") return data.pass1_vs_runtime_corr;
      if (xMetric === "pass_at_1" && yMetric === "avg_memory_percentile")  return data.pass1_vs_memory_corr;
      if (xMetric === "pass_at_k" && yMetric === "avg_runtime_percentile") return data.passk_vs_runtime_corr;
      return null;
    }
    const xs: number[] = [], ys: number[] = [];
    for (const p of filteredPoints) {
      const x = p[xMetric]; const y = p[yMetric];
      if (x != null && y != null) { xs.push(x); ys.push(y); }
    }
    return pearson(xs, ys);
  }, [data, filteredPoints, xMetric, yMetric, selectedModels.size]);

  const langColor = useMemo(() => {
    const m = new Map<string, string>();
    byLanguage.forEach((r, i) => m.set(r.language, PALETTE[i % PALETTE.length]));
    return m;
  }, [byLanguage]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!data) return <div className="empty">Loading…</div>;

  const visibleModels = modelQuery.trim()
    ? allModels.filter(m => m.name.toLowerCase().includes(modelQuery.trim().toLowerCase())
                            || selectedModels.has(m.name))
    : allModels;

  function toggleModel(name: string) {
    setSelectedModels(s => {
      const next = new Set(s);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }
  function clearModels() { setSelectedModels(new Set()); }

  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>Correctness vs Performance</h1>
        <p style={{ maxWidth: 760 }}>
          Correctness (pass@1 / pass@k) and performance (LeetCode runtime &amp;
          memory percentile) side by side, across every supported language.
          The recurring finding in the literature is that the two are largely
          <b> decoupled</b> — a model can be more correct without being faster.
          This page lets you check that on PerfArena's real LeetCode data.
        </p>
      </div>

      {/* ---- Model filter ---------------------------------------------- */}
      <section className="col" style={{ gap: 8 }}>
        <h2 style={{ margin: 0 }}>Model filter</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
          Scope the per-language table, the correlation, and the scatter to
          specific models. Empty = all models ({allModels.length} in scope,
          {" "}{data.n_points.toLocaleString()} judged submissions total).
          {selectedModels.size > 0 && (
            <> Active: <b>{selectedModels.size}</b> model{selectedModels.size === 1 ? "" : "s"}
            {", "}{filteredPoints.length.toLocaleString()} submissions in scope. </>
          )}
        </p>
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input placeholder="Search models…"
                 value={modelQuery}
                 onChange={e => setModelQuery(e.target.value)}
                 style={{ minWidth: 220 }} />
          {modelQuery && (
            <button className="secondary" onClick={() => setModelQuery("")}>clear search</button>
          )}
          {selectedModels.size > 0 && (
            <button className="secondary" onClick={clearModels}>
              clear filter (use all models)
            </button>
          )}
          <span className="muted" style={{ fontSize: "0.85rem" }}>
            {visibleModels.length} / {allModels.length} shown
          </span>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {visibleModels.length === 0
            ? <span className="muted">no models match “{modelQuery}”</span>
            : visibleModels.map(m => {
                const picked = selectedModels.has(m.name);
                return (
                  <button key={m.name}
                          className={picked ? "" : "secondary"}
                          onClick={() => toggleModel(m.name)}>
                    <b>{m.name}</b>
                    <span className="muted" style={{ marginLeft: 6 }}>
                      · {m.n} sub{m.n === 1 ? "" : "s"}
                    </span>
                  </button>
                );
              })}
        </div>
      </section>

      {/* ---- Per-language grid ----------------------------------------- */}
      <section className="col" style={{ gap: 8 }}>
        <h2 style={{ margin: 0 }}>
          By language
          {selectedModels.size > 0 && (
            <span className="muted" style={{ fontWeight: 400, fontSize: "0.9rem", marginLeft: 8 }}>
              · {selectedModels.size} model{selectedModels.size === 1 ? "" : "s"} selected
            </span>
          )}
        </h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Means over judged submissions in each language (filtered to the
          selected models when a filter is active). <b>corr(pass@1, rt%ile)</b>
          {" "}is the within-language Pearson correlation between correctness
          and runtime percentile — near 0 means they don't move together.
        </p>
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>Language</th>
              <th title="judged submissions in this language (after filter)">Subs</th>
              <th title="distinct models (after filter)">Models</th>
              <th title="mean pass@1 across submissions">Mean pass@1</th>
              <th title="best pass@1 any submission reached">Best pass@1</th>
              <th title="mean pass@k">Mean pass@k</th>
              <th title="mean LeetCode runtime percentile">Mean rt %ile</th>
              <th title="mean LeetCode memory percentile">Mean mem %ile</th>
              <th title="within-language Pearson r between pass@1 and runtime percentile">corr(pass@1, rt%ile)</th>
            </tr></thead>
            <tbody>
              {byLanguage.length === 0 ? (
                <tr><td colSpan={9} className="empty">No judged submissions match the filter.</td></tr>
              ) : byLanguage.map(r => (
                <tr key={r.language}>
                  <td>
                    <span style={{
                      display: "inline-block", width: 10, height: 10, borderRadius: 2,
                      background: langColor.get(r.language), marginRight: 8,
                    }} />
                    <LanguageLink name={r.language} />
                  </td>
                  <td className="metric">{r.n_submissions}</td>
                  <td className="metric">{r.n_models}</td>
                  <td className="metric">{formatPassAt1(r.mean_pass_at_1)}</td>
                  <td className="metric">{formatPassAt1(r.best_pass_at_1)}</td>
                  <td className="metric">{formatPassAt1(r.mean_pass_at_k)}</td>
                  <td className="metric">{formatPercentile(r.mean_runtime_percentile)}</td>
                  <td className="metric">{formatPercentile(r.mean_memory_percentile)}</td>
                  <td className="metric">{r.pass1_runtime_corr == null ? "–" : r.pass1_runtime_corr.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ---- Correctness vs performance scatter ------------------------ */}
      <section className="col" style={{ gap: 8 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
          <h2 style={{ margin: 0 }}>Scatter</h2>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <label className="muted" style={{ fontSize: "0.85rem" }}>x:&nbsp;
              <select value={xMetric} onChange={e => setXMetric(e.target.value as XKey)}>
                <option value="pass_at_1">pass@1</option>
                <option value="pass_at_k">pass@k</option>
              </select>
            </label>
            <label className="muted" style={{ fontSize: "0.85rem" }}>y:&nbsp;
              <select value={yMetric} onChange={e => setYMetric(e.target.value as YKey)}>
                <option value="avg_runtime_percentile">runtime %ile</option>
                <option value="avg_memory_percentile">memory %ile</option>
              </select>
            </label>
          </div>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Each dot is one judged submission, coloured by language ({filteredPoints.length.toLocaleString()} points).
          {corr != null && (
            <> Pearson r = <b>{corr.toFixed(3)}</b>{" "}
              ({Math.abs(corr) < 0.2 ? "essentially decoupled"
                : Math.abs(corr) < 0.5 ? "weak"
                : Math.abs(corr) < 0.7 ? "moderate" : "strong"}
              {corr < 0 ? ", negative" : ""}).</>
          )}
        </p>
        <Scatter points={filteredPoints} xKey={xMetric} yKey={yMetric} langColor={langColor} />
        <Legend langColor={langColor} />
      </section>

      {/* Discrimination is the orthogonal view; kept available for now but
          stays under its own toggle so it doesn't drown the page. */}
      {/* <DiscriminationSection disc={disc} /> */}

      <p className="muted" style={{ fontSize: "0.85rem" }}>
        See <Link to="/rankings">Rankings</Link> for the full leaderboard and{" "}
        <Link to="/compare">Compare</Link> for head-to-head model diffs.
        {disc && null}
      </p>
    </div>
  );
}


/* ------- helpers ----------------------------------------------------- */

/** Re-aggregate the by-language table from a filtered point set.
 *
 * Mirrors the server-side aggregation in main.py:_analysis. Kept simple:
 * means + bests + the within-language pass@1×runtime%ile correlation. We
 * skip the small extras (n_judged etc.) — they aren't shown by the table. */
function recomputeByLanguage(points: AnalysisPoint[]): {
  language: string;
  n_submissions: number;
  n_models: number;
  mean_pass_at_1: number | null;
  best_pass_at_1: number | null;
  mean_pass_at_k: number | null;
  mean_runtime_percentile: number | null;
  mean_memory_percentile: number | null;
  pass1_runtime_corr: number | null;
}[] {
  const by: Record<string, AnalysisPoint[]> = {};
  for (const p of points) (by[p.language] ??= []).push(p);
  return Object.entries(by).map(([language, ps]) => {
    const models = new Set(ps.map(p => p.model_name));
    const p1 = numbers(ps, p => p.pass_at_1);
    const pk = numbers(ps, p => p.pass_at_k);
    const rt = numbers(ps, p => p.avg_runtime_percentile);
    const mem = numbers(ps, p => p.avg_memory_percentile);
    // Pairs of (pass@1, runtime%ile) where both exist — the within-lang corr.
    const xy: [number, number][] = [];
    for (const p of ps) {
      if (p.pass_at_1 != null && p.avg_runtime_percentile != null) {
        xy.push([p.pass_at_1, p.avg_runtime_percentile]);
      }
    }
    return {
      language,
      n_submissions: ps.length,
      n_models: models.size,
      mean_pass_at_1: mean(p1),
      best_pass_at_1: p1.length ? Math.max(...p1) : null,
      mean_pass_at_k: mean(pk),
      mean_runtime_percentile: mean(rt),
      mean_memory_percentile: mean(mem),
      pass1_runtime_corr: pearson(xy.map(p => p[0]), xy.map(p => p[1])),
    };
  }).sort((a, b) => b.n_submissions - a.n_submissions);
}

function numbers(ps: AnalysisPoint[], k: (p: AnalysisPoint) => number | null): number[] {
  const out: number[] = [];
  for (const p of ps) { const v = k(p); if (v != null) out.push(v); }
  return out;
}
function mean(xs: number[]): number | null {
  return xs.length === 0 ? null : xs.reduce((a, b) => a + b, 0) / xs.length;
}
function pearson(xs: number[], ys: number[]): number | null {
  if (xs.length !== ys.length || xs.length < 3) return null;
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, dx = 0, dy = 0;
  for (let i = 0; i < n; i++) {
    const ex = xs[i] - mx; const ey = ys[i] - my;
    num += ex * ey; dx += ex * ex; dy += ey * ey;
  }
  if (dx === 0 || dy === 0) return null;
  return num / Math.sqrt(dx * dy);
}


/* ------- scatter + legend -------------------------------------------- */

function Scatter({
  points, xKey, yKey, langColor,
}: {
  points: AnalysisPoint[];
  xKey: XKey;
  yKey: YKey;
  langColor: Map<string, string>;
}) {
  const W = 760, H = 360, PAD = 36;
  const usable = points.filter(p => p[xKey] != null && p[yKey] != null);
  if (usable.length === 0) {
    return <div className="empty">No (x, y) pairs to plot in this filter.</div>;
  }
  const xMin = 0, xMax = xKey === "pass_at_1" || xKey === "pass_at_k" ? 1 : 100;
  const yMin = 0, yMax = 100;
  const x = (v: number) => PAD + ((v - xMin) / (xMax - xMin)) * (W - 2 * PAD);
  const y = (v: number) => H - PAD - ((v - yMin) / (yMax - yMin)) * (H - 2 * PAD);

  return (
    <div className="card" style={{ padding: 8, overflowX: "auto" }}>
      <svg width={W} height={H} style={{ display: "block" }}>
        {/* Gridlines + axes */}
        {[0, 25, 50, 75, 100].map(t => (
          <g key={`g${t}`}>
            <line x1={PAD} x2={W - PAD}
                  y1={y((t / 100) * yMax)} y2={y((t / 100) * yMax)}
                  stroke="var(--border)" strokeDasharray="2 3" />
            <text x={6} y={y((t / 100) * yMax) + 4} fontSize="0.7rem" fill="var(--fg-dim)">
              {t}%
            </text>
          </g>
        ))}
        {[0, 0.25, 0.5, 0.75, 1].map(t => (
          <g key={`gx${t}`}>
            <line y1={PAD} y2={H - PAD}
                  x1={x(t * xMax)} x2={x(t * xMax)}
                  stroke="var(--border)" strokeDasharray="2 3" />
            <text x={x(t * xMax) - 6} y={H - 8} fontSize="0.7rem" fill="var(--fg-dim)">
              {xKey === "pass_at_1" || xKey === "pass_at_k" ? `${Math.round(t * 100)}%` : t * xMax}
            </text>
          </g>
        ))}
        {/* Axis labels */}
        <text x={W / 2} y={H - 2} fontSize="0.72rem" fill="var(--fg-dim)" textAnchor="middle">
          {xKey === "pass_at_1" ? "pass@1" : "pass@k"}
        </text>
        <text x={4} y={PAD - 8} fontSize="0.72rem" fill="var(--fg-dim)">
          {yKey === "avg_runtime_percentile" ? "runtime %ile" : "memory %ile"}
        </text>
        {/* Dots */}
        {usable.map(p => (
          <circle key={p.submission_id}
                  cx={x(p[xKey]!)} cy={y(p[yKey]!)} r={3.5}
                  fill={langColor.get(p.language) ?? "#999"}
                  fillOpacity={0.7}
                  stroke="white" strokeOpacity={0.3} strokeWidth={0.5}>
            <title>{`${p.model_name} · ${p.language}\npass@1 ${formatPassAt1(p.pass_at_1)} · runtime ${formatPercentile(p.avg_runtime_percentile)} · memory ${formatPercentile(p.avg_memory_percentile)}`}</title>
          </circle>
        ))}
      </svg>
    </div>
  );
}

function Legend({ langColor }: { langColor: Map<string, string> }) {
  return (
    <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
      <span className="muted" style={{ fontSize: "0.85rem" }}>Languages:</span>
      {Array.from(langColor.entries()).map(([lang, col]) => (
        <span key={lang} className="row" style={{ gap: 5, alignItems: "center" }}>
          <span style={{ display: "inline-block", width: 10, height: 10,
                         borderRadius: 2, background: col }} />
          <code style={{ fontSize: "0.8rem" }}>{lang}</code>
        </span>
      ))}
    </div>
  );
}
