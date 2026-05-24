import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, CompareResponse, CompareSummaryRow, CompareWinners,
  ModelListEntry, ProblemPublic,
} from "../lib/api";
import LanguageLink from "../components/LanguageLink";
import LevelBadge from "../components/LevelBadge";
import {
  formatCoefficient, formatMemory, formatPassAt1, formatPercentile, formatRuntime,
  coefficientClass,
} from "../lib/format";

const LANGS = [
  "python3","cpp","c","rust","java","csharp","javascript","typescript",
  "golang","kotlin","swift","scala","ruby","php",
];

export default function Compare() {
  const [models, setModels] = useState<ModelListEntry[]>([]);
  const [problems, setProblems] = useState<ProblemPublic[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [modelQuery, setModelQuery] = useState<string>("");
  const [language, setLanguage] = useState<string>("");
  const [problemFilter, setProblemFilter] = useState<string[]>([]);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([api.models(), api.problems()])
      .then(([m, p]) => { setModels(m); setProblems(p); })
      .catch((e: unknown) => setErr(String(e)));
  }, []);

  async function run() {
    setErr(""); setLoading(true);
    try {
      const r = await api.compare({
        models: picked,
        language: language || undefined,
        problems: problemFilter.length ? problemFilter : undefined,
      });
      setData(r);
    } catch (e: unknown) {
      setErr(String(e));
    } finally { setLoading(false); }
  }

  function toggle(m: string) {
    setPicked(p => p.includes(m) ? p.filter(x => x !== m) : [...p, m]);
  }
  function toggleProblem(s: string) {
    setProblemFilter(p => p.includes(s) ? p.filter(x => x !== s) : [...p, s]);
  }

  const langOptions = useMemo(() => {
    const counts: Record<string, number> = {};
    models.forEach(m => m.languages.forEach(l => { counts[l] = (counts[l] ?? 0) + 1; }));
    return LANGS.filter(l => counts[l]);
  }, [models]);

  // Model picker search — a model stays visible if it matches the query OR is
  // already picked (so a selection never disappears behind a filter).
  const visibleModels = useMemo(() => {
    const needle = modelQuery.trim().toLowerCase();
    if (!needle) return models;
    return models.filter(m =>
      m.name.toLowerCase().includes(needle) || picked.includes(m.name));
  }, [models, modelQuery, picked]);

  return (
    <div className="col">
      <div className="hero">
        <h1>Compare models</h1>
        <p>Pick two or more models and (optionally) a language or a problem
          subset. The summary aggregates each model's <i>best</i> attempt per
          problem within the filter; the matrix below shows the per-problem
          head-to-head.</p>
      </div>

      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Models</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Search, then click to add / remove. Need at least 2 to compare.
          {picked.length > 0 && <> Picked: <b>{picked.length}</b>.</>}
        </p>
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            placeholder="Search models…"
            value={modelQuery}
            onChange={e => setModelQuery(e.target.value)}
            style={{ minWidth: 260 }}
          />
          {modelQuery && (
            <button className="secondary" onClick={() => setModelQuery("")}>clear</button>
          )}
          <span className="muted">
            {visibleModels.length} / {models.length} shown
          </span>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {models.length === 0
            ? <span className="muted">no models submitted yet</span>
            : visibleModels.length === 0
              ? <span className="muted">no models match “{modelQuery}”</span>
              : visibleModels.map(m => (
                <button key={m.name}
                        className={picked.includes(m.name) ? "" : "secondary"}
                        onClick={() => toggle(m.name)}>
                  <b>{m.name}</b>
                  <span className="muted">
                    {" "}· {m.n_submissions} sub{m.n_submissions === 1 ? "" : "s"}
                    {" "}· {m.languages.length} lang{m.languages.length === 1 ? "" : "s"}
                  </span>
                </button>
              ))}
        </div>
      </section>

      <section className="col">
        <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="col" style={{ gap: 4 }}>
            <span className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase" }}>language</span>
            <select value={language} onChange={e => setLanguage(e.target.value)}>
              <option value="">all languages (per-model max)</option>
              {langOptions.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
          <button onClick={run} disabled={loading || picked.length < 2}>
            {loading ? "Comparing…" : `Compare ${picked.length || ""} model${picked.length === 1 ? "" : "s"}`}
          </button>
          {picked.length > 0 && (
            <button className="secondary" onClick={() => { setPicked([]); setProblemFilter([]); setData(null); }}>
              Clear
            </button>
          )}
        </div>
      </section>

      <section className="col">
        <details>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>
            Restrict to specific problems ({problemFilter.length || "any"})
          </summary>
          <p className="muted" style={{ marginTop: 8 }}>
            Click to add / remove. Empty = compare across all attempted problems.
          </p>
          <div className="row" style={{ gap: 4, flexWrap: "wrap", maxHeight: 220, overflowY: "auto" }}>
            {problems.map(p => (
              <button key={p.title_slug}
                      className={problemFilter.includes(p.title_slug) ? "" : "secondary"}
                      onClick={() => toggleProblem(p.title_slug)}
                      style={{ fontSize: "0.78rem", padding: "4px 8px" }}>
                <code>{p.title_slug}</code>
              </button>
            ))}
          </div>
        </details>
      </section>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {data && data.language == null && (() => {
        const langs = new Set<string>();
        data.summary.forEach(r => r.languages.forEach(l => langs.add(l)));
        if (langs.size <= 1) return null;
        return (
          <div className="card" style={{
            borderColor: "var(--medium)",
            background: "rgba(220,180,80,0.08)",
            fontSize: "0.88rem",
          }}>
            <b>Comparing across {langs.size} languages ({Array.from(langs).sort().join(", ")}).</b>{" "}
            Raw avg / best runtime + memory are not directly comparable across
            languages, so the "fastest avg runtime" and "leanest avg memory"
            verdicts are suppressed below. The remaining winners (pass@1 / pass@k /
            pass@full / problems solved / runtime + memory percentile) are
            language-fair. To unlock the raw-number winners, pick a language
            filter above.
          </div>
        );
      })()}
      {data && <WinnersBanner winners={data.winners} />}
      {data && <SummaryTable rows={data.summary} winners={data.winners} />}
      {data && data.problems.length > 0 && (
        <PerProblemMatrix models={data.models} rows={data.problems} />
      )}
      {data && data.summary.every(s => s.n_submissions === 0) && (
        <div className="empty">None of the selected models have judged submissions matching this filter.</div>
      )}
    </div>
  );
}

function WinnersBanner({ winners }: { winners: CompareWinners }) {
  const m = winners;
  const rows: Array<[string, string | null]> = [
    ["Best pass@1", m.best_pass_at_1],
    ["Best pass@k", m.best_pass_at_k],
    ["Best pass@full", m.best_pass_at_full],
    ["Most problems solved", m.most_problems_solved],
    ["Fastest avg runtime", m.fastest_avg_runtime],
    ["Leanest avg memory", m.leanest_avg_memory],
    ["Best runtime %ile", m.best_runtime_percentile],
    ["Best memory %ile", m.best_memory_percentile],
  ];
  const visible = rows.filter(([, v]) => v != null) as Array<[string, string]>;
  const wins = Object.entries(m.overall_wins_by_model).filter(([, n]) => n > 0);
  wins.sort((a, b) => b[1] - a[1]);

  if (!m.overall && visible.length === 0) return null;

  return (
    <section className="card" style={{
      borderColor: "var(--easy)",
      background: "rgba(120,200,120,0.08)",
    }}>
      <div className="row" style={{ alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0 }}>Verdict</h2>
        {m.overall ? (
          <span style={{ fontSize: "1.1rem" }}>
            Overall winner: <Link to={`/models/${encodeURIComponent(m.overall)}`}><b>{m.overall}</b></Link>
            {" "}<span className="muted">
              ({wins.find(([n]) => n === m.overall)?.[1]} metric wins
              {wins.length > 1 ? `; trailing: ${wins.slice(1, 4).map(([n, w]) => `${n} (${w})`).join(", ")}` : ""})
            </span>
          </span>
        ) : (
          <span className="muted">No clear winner — not enough metrics decided yet.</span>
        )}
      </div>
      {visible.length > 0 && (
        <ul className="muted" style={{ marginTop: 8, lineHeight: 1.7 }}>
          {visible.map(([label, name]) => (
            <li key={label}>
              {label}: <Link to={`/models/${encodeURIComponent(name)}`}><b>{name}</b></Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const WIN_STYLE: React.CSSProperties = {
  background: "rgba(120,200,120,0.18)",
  fontWeight: 600,
};

function SummaryTable({ rows, winners }: { rows: CompareSummaryRow[]; winners?: CompareWinners }) {
  const w = winners;
  const isWinner = (m: string, key: keyof CompareWinners) =>
    !!w && w[key] === m;
  return (
    <section className="col">
      <h2 style={{ marginBottom: 4 }}>Summary</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        One row per model. "Best" metrics are the per-problem best attempt,
        averaged across all problems the model accepted in the filter scope.
      </p>
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Model</th>
            <th title="Distinct submissions in scope">Subs</th>
            <th title="Distinct problems with at least one accepted attempt">Solved</th>
            <th title="Distinct problems attempted">Attempted</th>
            <th title="Languages the model has submissions in (within filter)">Langs</th>
            <th title="Best pass@1 across the model's submissions (unbiased over k samples)">pass@1</th>
            <th title="Best pass@k">pass@k</th>
            <th title="Best pass@full (any-of-k)">pass@full</th>
            <th title="Mean per-problem avg runtime">Avg rt</th>
            <th title="Mean per-problem best runtime">Best rt</th>
            <th title="Mean per-problem avg memory">Avg mem</th>
            <th title="Mean per-problem best memory">Best mem</th>
            <th title="Mean LeetCode runtime percentile">RT %ile</th>
            <th title="Mean LeetCode memory percentile">Mem %ile</th>
            <th title="Speedup vs avg human (>1 = faster than avg)">t/avg</th>
            <th title="Speedup vs expert human (>1 = matches top humans)">t/exp</th>
            <th title="Memory ratio vs avg human (>1 = uses less)">m/avg</th>
            <th title="Memory ratio vs expert human">m/exp</th>
            <th></th>
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.model_name}>
                <td><Link to={`/models/${encodeURIComponent(r.model_name)}`}><b>{r.model_name}</b></Link></td>
                <td className="metric">{r.n_submissions}</td>
                <td className="metric" style={isWinner(r.model_name, "most_problems_solved") ? WIN_STYLE : undefined}>{r.n_problems_accepted}</td>
                <td className="metric">{r.n_problems_attempted}</td>
                <td>{r.languages.map((l, i) => (
                  <span key={l}>{i > 0 && " · "}<LanguageLink name={l} /></span>
                ))}</td>
                <td className="metric" style={isWinner(r.model_name, "best_pass_at_1") ? WIN_STYLE : undefined}>{formatPassAt1(r.pass_at_1_max)}</td>
                <td className="metric" style={isWinner(r.model_name, "best_pass_at_k") ? WIN_STYLE : undefined}>{formatPassAt1(r.pass_at_k_max)}</td>
                <td className="metric" style={isWinner(r.model_name, "best_pass_at_full") ? WIN_STYLE : undefined}>{formatPassAt1(r.pass_at_full_max)}</td>
                <td className="metric" style={isWinner(r.model_name, "fastest_avg_runtime") ? WIN_STYLE : undefined}>{formatRuntime(r.avg_runtime_ms)}</td>
                <td className="metric">{formatRuntime(r.best_runtime_ms)}</td>
                <td className="metric" style={isWinner(r.model_name, "leanest_avg_memory") ? WIN_STYLE : undefined}>{formatMemory(r.avg_memory_mb)}</td>
                <td className="metric">{formatMemory(r.best_memory_mb)}</td>
                <td className="metric" style={isWinner(r.model_name, "best_runtime_percentile") ? WIN_STYLE : undefined}>{formatPercentile(r.avg_runtime_percentile)}</td>
                <td className="metric" style={isWinner(r.model_name, "best_memory_percentile") ? WIN_STYLE : undefined}>{formatPercentile(r.avg_memory_percentile)}</td>
                <td className={`metric ${coefficientClass(r.time_human_avg)}`}>{formatCoefficient(r.time_human_avg)}</td>
                <td className={`metric ${coefficientClass(r.time_human_expert)}`}>{formatCoefficient(r.time_human_expert)}</td>
                <td className={`metric ${coefficientClass(r.mem_human_avg)}`}>{formatCoefficient(r.mem_human_avg)}</td>
                <td className={`metric ${coefficientClass(r.mem_human_expert)}`}>{formatCoefficient(r.mem_human_expert)}</td>
                <td>
                  {r.leading_submission_id != null && (
                    <Link to={`/submissions/${r.leading_submission_id}`}>best →</Link>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PerProblemMatrix({ models, rows }: { models: string[]; rows: import("../lib/api").CompareProblemRow[] }) {
  return (
    <section className="col">
      <h2 style={{ marginBottom: 4 }}>Per-problem head-to-head ({rows.length})</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Each cell is the model's best attempt for that problem (accepted-first,
        then fastest). <b>—</b> means the model didn't attempt this problem in
        the filter scope.
      </p>
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Problem</th><th>Level</th>
            {models.map(m => <th key={m}>{m}</th>)}
          </tr></thead>
          <tbody>
            {rows.map(p => {
              // Per-row bests across the compared models. Only consider
              // accepted attempts — a faster runtime on a rejected attempt
              // is meaningless. Ties: the first model in `models` order wins.
              const acceptedCells = models
                .map(m => ({ model: m, cell: p.per_model[m] }))
                .filter(x => x.cell && x.cell.accepted);
              const bestRtModel = acceptedCells
                .filter(x => x.cell.runtime_ms != null)
                .sort((a, b) => (a.cell.runtime_ms! - b.cell.runtime_ms!))[0]?.model;
              const bestMemModel = acceptedCells
                .filter(x => x.cell.memory_mb != null)
                .sort((a, b) => (a.cell.memory_mb! - b.cell.memory_mb!))[0]?.model;

              return (
                <tr key={p.problem_slug}>
                  <td>
                    <Link to={`/problems/${p.problem_slug}`}><code>{p.problem_slug}</code></Link>
                  </td>
                  <td><LevelBadge level={p.level} /></td>
                  {models.map(m => {
                    const c = p.per_model[m];
                    if (!c || c.submission_id == null) {
                      return <td key={m} className="muted">—</td>;
                    }
                    const isFastest = m === bestRtModel;
                    const isLeanest = m === bestMemModel;
                    const cellHighlight: React.CSSProperties | undefined =
                      (isFastest || isLeanest)
                        ? { background: "rgba(120,200,120,0.12)" } : undefined;
                    return (
                      <td key={m} style={cellHighlight}>
                        <span className={`badge ${c.accepted ? "badge-easy" : c.status === "queued" ? "badge-medium" : "badge-hard"}`}>
                          {c.status}
                        </span>
                        <div className="muted metric" style={{ fontSize: "0.75rem", marginTop: 4 }}>
                          <span style={isFastest ? { fontWeight: 700, color: "var(--easy)" } : undefined}>
                            {formatRuntime(c.runtime_ms)}{isFastest && " ★"}
                          </span>
                          {" · "}
                          <span style={isLeanest ? { fontWeight: 700, color: "var(--easy)" } : undefined}>
                            {formatMemory(c.memory_mb)}{isLeanest && " ★"}
                          </span>
                        </div>
                        <div className="muted" style={{ fontSize: "0.72rem", marginTop: 2 }}>
                          {c.language}{c.submission_id != null && (
                            <> · <Link to={`/submissions/${c.submission_id}`}>#{c.submission_id}</Link></>
                          )}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
