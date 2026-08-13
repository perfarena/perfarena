import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  api, CompareResponse, CompareSummaryRow, CompareWinners,
  ComparePromptRow, ComparePromptsResponse, ComparePromptsWinners,
  ModelListEntry, ProblemPublic, PromptTemplatePublic,
  SubmissionPublic,
} from "../lib/api";
import CompareBars, {
  MODEL_COLOURS, shouldShowEnergyColumn,
} from "../components/CompareHistogram";
import CompareByLevel from "../components/CompareByLevel";
import CompareByTag from "../components/CompareByTag";
import LanguageLink from "../components/LanguageLink";
import LevelBadge from "../components/LevelBadge";
import PromptName from "../components/PromptName";
import {
  formatCoefficient, formatEnergy, formatMemory, formatPassAt1, formatPercentile, formatRuntime,
  coefficientClass,
} from "../lib/format";
import CorrectnessVsPerformance from "./CorrectnessVsPerformance";

const LANGS = [
  "python3","cpp","c","rust","java","csharp","javascript","typescript",
  "golang","kotlin","swift","scala","ruby","php",
];

type Tab = "models" | "submissions" | "prompts" | "perf-vs-correctness";

const VALID_TABS: ReadonlySet<Tab> = new Set([
  "models", "submissions", "prompts", "perf-vs-correctness",
]);

/* /compare hosts three sibling views that share the same audience (someone
 * trying to read one model vs another, one prompt strategy vs another, or
 * the cross-language landscape):
 *   - Models                    — head-to-head model comparison + per-problem
 *                                  overlap bars. Optional prompt filter.
 *   - Prompt strategies         — head-to-head prompt-template comparison
 *                                  within ONE model+version+language cell.
 *   - Correctness vs Performance— per-language scatter + filterable model
 *                                  multi-select (the old "Analysis" tab).
 *
 * Active tab persists in ?tab= so links are shareable; the legacy /analysis
 * URL — and the older ?tab=analysis query — redirect onto the renamed
 * "perf-vs-correctness" key. */
export default function Compare() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") ?? "models";
  // Back-compat: the old key was `analysis`. Map it onto the new key so
  // bookmarks and external links keep landing on the right tab.
  const normalised: Tab = raw === "analysis"
    ? "perf-vs-correctness"
    : (VALID_TABS.has(raw as Tab) ? (raw as Tab) : "models");
  const tab: Tab = normalised;

  function setTab(next: Tab) {
    // Preserve other params (filter state could grow into the URL later)
    // while flipping the tab.
    const p = new URLSearchParams(params);
    if (next === "models") p.delete("tab");
    else p.set("tab", next);
    setParams(p, { replace: false });
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row" style={{ gap: 4, borderBottom: "1px solid var(--border)" }}>
        <TabBtn active={tab === "models"}             onClick={() => setTab("models")}>Models</TabBtn>
        <TabBtn active={tab === "submissions"}        onClick={() => setTab("submissions")}>Submissions</TabBtn>
        <TabBtn active={tab === "prompts"}            onClick={() => setTab("prompts")}>Prompt strategies</TabBtn>
        <TabBtn active={tab === "perf-vs-correctness"} onClick={() => setTab("perf-vs-correctness")}>Correctness vs Performance</TabBtn>
      </div>
      {tab === "models"             && <CompareView />}
      {tab === "submissions"        && <SubmissionsCompareView />}
      {tab === "prompts"            && <PromptStrategiesView />}
      {tab === "perf-vs-correctness" && <CorrectnessVsPerformance />}
    </div>
  );
}

function TabBtn({
  active, onClick, children,
}: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={active ? undefined : "secondary"}
      style={{
        borderRadius: "8px 8px 0 0",
        borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
        background: active ? "var(--accent-soft)" : "transparent",
        color: active ? "var(--fg)" : "var(--fg-dim)",
        fontWeight: active ? 700 : 500,
        padding: "8px 16px",
        border: active ? undefined : "none",
      }}
    >
      {children}
    </button>
  );
}

function CompareView() {
  const [models, setModels] = useState<ModelListEntry[]>([]);
  const [problems, setProblems] = useState<ProblemPublic[]>([]);
  const [prompts, setPrompts] = useState<PromptTemplatePublic[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [modelQuery, setModelQuery] = useState<string>("");
  const [language, setLanguage] = useState<string>("");
  // "" = any prompt; numeric string = a specific PromptTemplate id. Filters
  // the comparison to submissions made with that template only — useful for
  // an apples-to-apples model vs model when the same prompt was used.
  const [promptIdSel, setPromptIdSel] = useState<string>("");
  const [problemFilter, setProblemFilter] = useState<string[]>([]);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([api.models(), api.problems(), api.prompts()])
      .then(([m, p, pr]) => { setModels(m); setProblems(p); setPrompts(pr); })
      .catch((e: unknown) => setErr(String(e)));
  }, []);

  async function run() {
    setErr(""); setLoading(true);
    try {
      const r = await api.compare({
        models: picked,
        language: language || undefined,
        problems: problemFilter.length ? problemFilter : undefined,
        promptId: promptIdSel ? Number(promptIdSel) : undefined,
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
        <p>Select two or more models. You can also select a language or a
          subset of problems. The summary combines the <i>best</i> attempt of
          each model for each problem in the filter. The matrix below shows
          the direct per-problem comparison.</p>
      </div>

      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Models</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          Search, then click to add or remove a model. You need at least 2 models to compare.
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
            ? <span className="muted">No models are submitted yet</span>
            : visibleModels.length === 0
              ? <span className="muted">No models match "{modelQuery}"</span>
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
              <option value="">all languages (max for each model)</option>
              {langOptions.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
          <label className="col" style={{ gap: 4 }}>
            <span className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase" }}>prompt</span>
            <select value={promptIdSel} onChange={e => setPromptIdSel(e.target.value)}
                    title="Limit the comparison to submissions that use this prompt template">
              <option value="">any prompt</option>
              {prompts.map(p => (
                <option key={p.id} value={String(p.id)}>{p.name}</option>
              ))}
            </select>
          </label>
          <button onClick={run} disabled={loading || picked.length < 2}>
            {loading ? "Comparison in progress…" : `Compare ${picked.length || ""} model${picked.length === 1 ? "" : "s"}`}
          </button>
          {(picked.length > 0 || promptIdSel) && (
            <button className="secondary" onClick={() => { setPicked([]); setProblemFilter([]); setPromptIdSel(""); setData(null); }}>
              Clear
            </button>
          )}
        </div>
        {promptIdSel && (
          <div className="muted" style={{ fontSize: "0.85rem", marginTop: -8 }}>
            Filter: prompt = <PromptName id={Number(promptIdSel)} name={prompts.find(p => p.id === Number(promptIdSel))?.name ?? null} />.
            The comparison excludes submissions that use any other prompt.
          </div>
        )}
      </section>

      <section className="col">
        <details>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>
            Limit to specific problems ({problemFilter.length || "any"})
          </summary>
          <p className="muted" style={{ marginTop: 8 }}>
            Click to add or remove a problem. If empty, the comparison uses all attempted problems.
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
            <b>This comparison covers {langs.size} languages ({Array.from(langs).sort().join(", ")}).</b>{" "}
            The raw avg and best runtime and memory do not compare directly
            across languages. Therefore this page hides the "fastest avg
            runtime" and "leanest peak mem" verdicts below. The other winners
            are fair across languages: pass@1, pass@k, pass@full, problems
            solved, and runtime and memory percentile. To show the raw-number
            winners, select a language filter above.
          </div>
        );
      })()}
      {data && <WinnersBanner winners={data.winners} />}
      {data && <SummaryTable rows={data.summary} winners={data.winners} />}
      {data && data.by_level && data.by_level.length > 0 && (
        <CompareByLevel models={data.models} rows={data.by_level} />
      )}
      {data && data.by_tag && data.by_tag.length > 0 && (
        <CompareByTag models={data.models} rows={data.by_tag} />
      )}
      {data && data.problems.length > 0 && (
        <PerProblemMatrix models={data.models} rows={data.problems} />
      )}
      {data && data.summary.every(s => s.n_submissions === 0) && (
        <div className="empty">No selected model has a submission that matches this filter and has a result.</div>
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
    ["Leanest peak mem", m.leanest_avg_memory],
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
          <span className="muted">No clear winner yet. Not enough metrics are decided.</span>
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
        One row for each model. The "Best" metrics use the best attempt for
        each problem. The system averages them across all problems that the
        model accepted in the filter scope.
      </p>
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Model</th>
            <th title="The number of distinct submissions in scope.">Subs</th>
            <th title="The number of distinct problems that have at least one accepted attempt.">Solved</th>
            <th title="The number of distinct problems that the model attempted.">Attempted</th>
            <th title="The languages that the model has submissions in, within the filter.">Langs</th>
            <th title="The best pass@1 across the submissions of the model. It is unbiased over k samples.">pass@1</th>
            <th title="The best pass@k.">pass@k</th>
            <th title="The best pass@full (any of k).">pass@full</th>
            <th title="The mean of the per-problem average runtime.">Avg rt</th>
            <th title="The mean of the per-problem best runtime.">Best rt</th>
            <th title="The mean of the per-problem mean peak memory. It is peak (maximum resident) on both oracles. It is never a per-op value.">Peak mem</th>
            <th title="The mean of the per-problem best (minimum) peak memory.">Best peak mem</th>
            <th title="The mean LeetCode runtime percentile.">RT %ile</th>
            <th title="The mean LeetCode memory percentile.">Mem %ile</th>
            <th title="The speedup against the average LeetCode submission. A value above 1 is faster than the average.">t/avg</th>
            <th title="The speedup against the expert LeetCode cohort. A value above 1 matches the top performers.">t/exp</th>
            <th title="The memory ratio against the average LeetCode submission. A value above 1 uses less memory.">m/avg</th>
            <th title="The memory ratio against the expert LeetCode cohort.">m/exp</th>
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
  // The overlapping-bar comparison column is only meaningful when exactly
  // TWO models are being compared (it's a head-to-head visual). With 3+
  // models the column is omitted and the matrix falls back to the plain
  // per-model cells.
  // The overlapping-bar comparison column appears as soon as we have a
  // head-to-head set (≥ 2 models). For 3+ models the bars stack N-wise
  // within each metric, sorted largest-first so the smallest bar stays
  // visible in front.
  const showBars = models.length >= 2;
  const showEnergy = showBars && shouldShowEnergyColumn(rows, models);
  return (
    <section className="col">
      <h2 style={{ marginBottom: 4 }}>Per-problem comparison ({rows.length})</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Each cell is the best attempt of the model for that problem. The
        system sorts by accepted first, then by fastest. A dash means the
        model did not attempt this problem in the filter scope.
      </p>
      {showBars && (
        /* Colour-key banner — identifies which overlapping bar belongs to
         * which model, sticky above the table so it's visible while
         * scrolling the long per-problem list. */
        <div className="card" style={{
          padding: 10,
          background: "var(--accent-soft)",
          borderColor: "var(--accent)",
          fontSize: "0.9rem",
        }}>
          <div className="row" style={{ gap: 16, alignItems: "center", flexWrap: "wrap" }}>
            <b style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: 0.5 }}>
              Comparison legend
            </b>
            {models.map((m, i) => (
              <span key={m} className="row" style={{ gap: 6, alignItems: "center" }}>
                <span style={{
                  display: "inline-block", width: 22, height: 12,
                  // Modulo matches the histogram's palette cycling for
                  // models past the palette's natural length.
                  background: MODEL_COLOURS[i % MODEL_COLOURS.length],
                  borderRadius: 3, opacity: 0.85,
                  border: "1px solid var(--border)",
                }} />
                <code style={{ fontSize: "0.9rem" }}>{m}</code>
              </span>
            ))}
            <span className="muted" style={{ fontSize: "0.85rem" }}>
              · The bar width shows the rank of each model on the metric within
              the problem. A lower value of ms or MB{showEnergy ? " or J" : ""} is better.
              {" "}A larger bar shows a better model. The winner gets the full bar.
            </span>
          </div>
        </div>
      )}
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Problem</th><th>Level</th>
            {models.map(m => <th key={m}>{m}</th>)}
            {showBars && <th title="This column shows overlapping bars for each metric. The lowest value of ms, MB, or J in the row is the best model and fills the track. The other bars scale down by ratio. A larger bar is a better model. Every row uses the same rule, so the winner always has the longest bar for any metric.">Comparison</th>}
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
              // Best energy — prefer runtime energy (arena-local etc.),
              // fall back to generation energy. Same "lowest wins" rule.
              const cellEnergy = (c: NonNullable<typeof p.per_model[string]>) =>
                c.runtime_energy_j ?? c.generation_energy_j;
              const bestEnergyModel = acceptedCells
                .filter(x => cellEnergy(x.cell) != null)
                .sort((a, b) => (cellEnergy(a.cell)! - cellEnergy(b.cell)!))[0]?.model;

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
                    const isMostEfficient = m === bestEnergyModel;
                    const cellHighlight: React.CSSProperties | undefined =
                      (isFastest || isLeanest || isMostEfficient)
                        ? { background: "rgba(120,200,120,0.12)" } : undefined;
                    const energyForCell = cellEnergy(c);
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
                          {energyForCell != null && (
                            <>
                              {" · "}
                              <span style={isMostEfficient ? { fontWeight: 700, color: "var(--easy)" } : undefined}
                                    title={c.runtime_energy_j != null
                                      ? "runtime energy (execution side)"
                                      : "generation energy (language model side)"}>
                                {formatEnergy(energyForCell)}{isMostEfficient && " ★"}
                              </span>
                            </>
                          )}
                        </div>
                        <div className="muted" style={{ fontSize: "0.72rem", marginTop: 2 }}>
                          {c.language}{c.submission_id != null && (
                            <> · <Link to={`/submissions/${c.submission_id}`}>#{c.submission_id}</Link></>
                          )}
                        </div>
                      </td>
                    );
                  })}
                  {showBars && (
                    <td style={{ verticalAlign: "middle", paddingLeft: 8 }}>
                      <CompareBars
                        models={models}
                        cells={models.map(m => p.per_model[m])}
                        showEnergy={showEnergy}
                      />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}


/* Prompt-strategy comparison view — pick one model + version + language, pick
 * 2+ prompt templates, and read off which prompt buys more correctness or
 * performance within that experiment cell. Mirrors CompareView's UX but the
 * axis on the result table is the prompt rather than the model. */
function PromptStrategiesView() {
  // (name, version) pairs come from /api/registered-models — that's the
  // canonical pair the rest of the platform keys off.
  const [pairs, setPairs] = useState<{ name: string; version: string; languages: string[] }[]>([]);
  const [prompts, setPrompts] = useState<PromptTemplatePublic[]>([]);
  const [pairKey, setPairKey] = useState<string>("");   // "name|version"
  const [language, setLanguage] = useState<string>("");
  const [pickedPrompts, setPickedPrompts] = useState<number[]>([]);
  const [data, setData] = useState<ComparePromptsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([api.registeredModels(true), api.prompts()])
      .then(([rms, ps]) => {
        setPairs(rms.map(r => ({
          name: r.name, version: r.version, languages: r.languages,
        })));
        setPrompts(ps);
      })
      .catch((e: unknown) => setErr(String(e)));
  }, []);

  const selectedPair = useMemo(
    () => pairs.find(p => `${p.name}|${p.version}` === pairKey) ?? null,
    [pairs, pairKey],
  );
  const langOptions = selectedPair?.languages ?? [];

  function togglePrompt(id: number) {
    setPickedPrompts(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);
  }

  async function run() {
    if (!selectedPair) return;
    setErr(""); setLoading(true);
    try {
      const r = await api.comparePrompts({
        modelName: selectedPair.name,
        modelVersion: selectedPair.version,
        language: language || undefined,
        promptIds: pickedPrompts,
      });
      setData(r);
    } catch (e: unknown) {
      setErr(String(e));
    } finally { setLoading(false); }
  }

  return (
    <div className="col">
      <div className="hero">
        <h1>Compare prompt strategies</h1>
        <p>
          Keep a model, version, and language fixed. Then select two or more
          prompt templates. The summary combines the <i>best</i> attempt of
          each prompt for each problem in that cell. Then you can see if richer
          prompts give more correctness or performance than the plain template.
          For example, compare <code>explain-then-code</code> with the plain
          template.
        </p>
      </div>

      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Model / version</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
          The comparison uses one model and version, so the only variable is
          the prompt. Select any model that the platform has seen submit answers.
        </p>
        <select value={pairKey} onChange={e => {
          setPairKey(e.target.value);
          // Reset language when the model changes — its language set differs.
          setLanguage("");
          setData(null);
        }}
                style={{ minWidth: 320 }}>
          <option value="">select a model</option>
          {pairs.map(p => {
            const key = `${p.name}|${p.version}`;
            return (
              <option key={key} value={key}>
                {p.name} · {p.version}{p.languages.length ? `  ·  ${p.languages.length} lang` : ""}
              </option>
            );
          })}
        </select>
      </section>

      <section className="col">
        <h2 style={{ marginBottom: 4 }}>Prompts</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: "0.9rem" }}>
          Click to add or remove a prompt. You need at least 2 prompts to compare.
          {pickedPrompts.length > 0 && <> Picked: <b>{pickedPrompts.length}</b>.</>}
        </p>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {prompts.length === 0
            ? <span className="muted">No prompts are in the registry yet</span>
            : prompts.map(p => (
              <button key={p.id}
                      className={pickedPrompts.includes(p.id) ? "" : "secondary"}
                      onClick={() => togglePrompt(p.id)}>
                <b>{p.name}</b>
                <span className="muted">
                  {" "}· <span style={{ textTransform: "uppercase", fontSize: "0.75em" }}>{p.status}</span>
                </span>
              </button>
            ))}
        </div>
      </section>

      <section className="col">
        <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="col" style={{ gap: 4 }}>
            <span className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase" }}>language</span>
            <select value={language} onChange={e => setLanguage(e.target.value)} disabled={!selectedPair}>
              <option value="">all languages that this model has used</option>
              {langOptions.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
          <button onClick={run}
                  disabled={loading || !selectedPair || pickedPrompts.length < 2}>
            {loading ? "Comparison in progress…" : `Compare ${pickedPrompts.length || ""} prompt${pickedPrompts.length === 1 ? "" : "s"}`}
          </button>
          {(pickedPrompts.length > 0 || pairKey || language) && (
            <button className="secondary"
                    onClick={() => {
                      setPickedPrompts([]); setPairKey("");
                      setLanguage(""); setData(null);
                    }}>Clear</button>
          )}
        </div>
        {!selectedPair && (
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            Select a model first. Its language set fills the language picker.
          </div>
        )}
      </section>

      {err && (
        <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>
          {err}
        </div>
      )}

      {data && (
        <>
          <PromptWinnersBanner winners={data.winners} />
          <PromptSummaryTable rows={data.rows} winners={data.winners} />
        </>
      )}
    </div>
  );
}

function PromptWinnersBanner({ winners }: { winners: ComparePromptsWinners }) {
  const fields: { label: string; key: keyof ComparePromptsWinners; tip: string }[] = [
    { label: "best pass@1",        key: "best_pass_at_1",        tip: "The highest pass@1 across the prompts." },
    { label: "best pass@k",        key: "best_pass_at_k",        tip: "The highest pass@k." },
    { label: "most problems",      key: "most_problems_solved",  tip: "The most distinct accepted problems." },
    { label: "fastest avg runtime",key: "fastest_avg_runtime",   tip: "The lowest mean LeetCode runtime (ms) across accepted attempts." },
    { label: "leanest peak mem",   key: "leanest_avg_memory",    tip: "The lowest mean peak memory (MB) across accepted attempts." },
    { label: "best runtime %ile",  key: "best_runtime_percentile", tip: "The highest mean LeetCode runtime percentile." },
    { label: "best memory %ile",   key: "best_memory_percentile",  tip: "The highest mean LeetCode memory percentile." },
  ];
  return (
    <section className="col">
      <h2 style={{ marginBottom: 4 }}>Winners</h2>
      {winners.overall && (
        <div className="card" style={{
          borderColor: "var(--accent)", background: "var(--accent-soft)",
          padding: 12, fontSize: "0.95rem",
        }}>
          <b>Overall winner:</b> <code>{winners.overall}</code>
          <span className="muted" style={{ marginLeft: 8 }}>
            (most metric wins; pass@1 tie-break)
          </span>
        </div>
      )}
      <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
        {fields.map(f => (
          <div key={String(f.key)} className="card"
               style={{ padding: 10, minWidth: 180 }} title={f.tip}>
            <div className="muted" style={{ fontSize: "0.7rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{f.label}</div>
            <div style={{ fontFamily: "ui-monospace, Menlo, monospace" }}>
              {(winners[f.key] as string | null) ?? <span className="muted">—</span>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function PromptSummaryTable({
  rows, winners,
}: { rows: ComparePromptRow[]; winners?: ComparePromptsWinners }) {
  function mark(metricKey: keyof ComparePromptsWinners, name: string): React.CSSProperties | undefined {
    if (!winners) return undefined;
    return winners[metricKey] === name ? WIN_STYLE : undefined;
  }
  return (
    <section className="col">
      <h2 style={{ marginBottom: 4 }}>Summary</h2>
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Prompt</th>
            <th title="The number of submissions with a result in scope.">Subs</th>
            <th title="The number of distinct accepted problems.">Solved</th>
            <th title="The best pass@1 across the submissions in scope.">pass@1</th>
            <th title="The best pass@k.">pass@k</th>
            <th title="The best pass@full.">pass@full</th>
            <th title="The mean of the accepted-best average runtime (ms).">Avg runtime</th>
            <th title="The mean of the accepted-best best runtime (ms).">Best runtime</th>
            <th title="The mean of the accepted-best mean peak memory (MB). It is peak on both oracles.">Peak mem</th>
            <th title="The mean of the accepted-best average runtime percentile.">RT %ile</th>
            <th title="The mean of the accepted-best average memory percentile.">Mem %ile</th>
            <th title="The time coefficient against the LeetCode average. A value above 1 is faster than the LeetCode population.">t vs avg</th>
            <th title="The memory coefficient against the LeetCode average.">m vs avg</th>
            <th></th>
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.prompt_id}>
                <td>
                  <PromptName id={r.prompt_id} name={r.prompt_name} />
                  <span className="muted" style={{ fontSize: "0.75em", marginLeft: 6 }}>{r.status}</span>
                </td>
                <td className="metric">{r.n_submissions}</td>
                <td className="metric" style={mark("most_problems_solved", r.prompt_name)}>
                  {r.n_problems_accepted} / {r.n_problems_attempted}
                </td>
                <td className="metric" style={mark("best_pass_at_1", r.prompt_name)}>{formatPassAt1(r.pass_at_1_max)}</td>
                <td className="metric" style={mark("best_pass_at_k", r.prompt_name)}>{formatPassAt1(r.pass_at_k_max)}</td>
                <td className="metric" style={mark("best_pass_at_full", r.prompt_name)}>{formatPassAt1(r.pass_at_full_max)}</td>
                <td className="metric" style={mark("fastest_avg_runtime", r.prompt_name)}>{formatRuntime(r.avg_runtime_ms)}</td>
                <td className="metric">{formatRuntime(r.best_runtime_ms)}</td>
                <td className="metric" style={mark("leanest_avg_memory", r.prompt_name)}>{formatMemory(r.avg_memory_mb)}</td>
                <td className="metric" style={mark("best_runtime_percentile", r.prompt_name)}>{formatPercentile(r.avg_runtime_percentile)}</td>
                <td className="metric" style={mark("best_memory_percentile", r.prompt_name)}>{formatPercentile(r.avg_memory_percentile)}</td>
                <td className={`metric ${coefficientClass(r.time_human_avg)}`}>{formatCoefficient(r.time_human_avg)}</td>
                <td className={`metric ${coefficientClass(r.mem_human_avg)}`}>{formatCoefficient(r.mem_human_avg)}</td>
                <td>
                  {r.leading_submission_id != null && (
                    <Link to={`/submissions/${r.leading_submission_id}`}>top run →</Link>
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


/* "Submissions" tab — same response shape as the Models tab, but the
 * axis is specific submission IDs. Lets you compare two snapshots of
 * the same model (different prompts/temperatures/sweep runs) or pit any
 * two arbitrary submissions head-to-head — without the implicit
 * aggregation the Models tab does over every submission a model has
 * ever made. */
function SubmissionsCompareView() {
  const [submissions, setSubmissions] = useState<SubmissionPublic[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [filter, setFilter] = useState("");
  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.submissions()
      .then(rows => setSubmissions(
        rows.slice().sort((a, b) => b.id - a.id)))    // newest first
      .catch((e: unknown) => setErr(String(e)));
  }, []);

  async function run() {
    setErr(""); setLoading(true);
    try {
      const r = await api.compareSubmissions({ ids: picked });
      setData(r);
    } catch (e: unknown) { setErr(String(e)); }
    finally { setLoading(false); }
  }

  function toggle(id: number) {
    setPicked(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);
  }

  // Visible list — substring match across model, language, or "#id". A picked
  // submission stays visible even when the filter would otherwise hide it,
  // so you can't accidentally drop a selection by typing.
  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return submissions;
    return submissions.filter(s =>
      picked.includes(s.id) ||
      s.model_name.toLowerCase().includes(needle) ||
      s.language.toLowerCase().includes(needle) ||
      String(s.id).includes(needle) ||
      (s.prompt_name?.toLowerCase().includes(needle) ?? false));
  }, [submissions, filter, picked]);

  return (
    <div className="col">
      <div className="hero">
        <h1>Compare submissions</h1>
        <p>Select two or more submissions to compare them directly. This helps
          you compare the same model with different prompts, sweep runs, or
          settings. It does not average them together. The Models tab averages
          them. The summary, the per-difficulty charts, and the per-problem
          matrix below use the same component as the Models tab.</p>
      </div>

      <section className="col">
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input placeholder="Search by model / language / #id / prompt…"
                 value={filter} onChange={e => setFilter(e.target.value)}
                 style={{ minWidth: 320 }} />
          {filter && <button className="secondary" onClick={() => setFilter("")}>clear</button>}
          <span className="muted">
            {visible.length} / {submissions.length} shown · picked: <b>{picked.length}</b>
          </span>
        </div>
        <div className="card tbl-scroll" style={{ padding: 0, maxHeight: 360 }}>
          <table className="tbl">
            <thead><tr>
              <th></th>
              <th>#</th>
              <th>Model</th>
              <th>Lang</th>
              <th>Prompt</th>
              <th>pass@1</th>
              <th>Solved</th>
              <th>When</th>
            </tr></thead>
            <tbody>
              {visible.slice(0, 200).map(s => {
                const isPicked = picked.includes(s.id);
                return (
                  <tr key={s.id}
                      style={isPicked ? { background: "var(--panel-2)" } : undefined}>
                    <td>
                      <input type="checkbox"
                             checked={isPicked}
                             onChange={() => toggle(s.id)} />
                    </td>
                    <td><Link to={`/submissions/${s.id}`}>#{s.id}</Link></td>
                    <td><b>{s.model_name}</b>{" "}<span className="muted">{s.model_version}</span></td>
                    <td>{s.language}</td>
                    <td className="muted">{s.prompt_name ?? "—"}</td>
                    <td className="metric">{formatPassAt1(s.pass_at_1)}</td>
                    <td className="metric">{s.total_solved}/{s.total_problems}</td>
                    <td className="muted">{new Date(s.created_at).toLocaleDateString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {visible.length > 200 && (
            <div className="empty">This list shows the first 200 of {visible.length} matches.</div>
          )}
        </div>

        <div className="row" style={{ gap: 8, alignItems: "center" }}>
          <button onClick={run} disabled={picked.length < 2 || loading}>
            {loading ? "Comparison in progress…" : `Compare ${picked.length} submission${picked.length === 1 ? "" : "s"}`}
          </button>
          {picked.length > 0 && (
            <button className="secondary" onClick={() => setPicked([])}>clear selection</button>
          )}
          {err && <span style={{ color: "var(--bad)" }}>{err}</span>}
        </div>
      </section>

      {data && <WinnersBanner winners={data.winners} />}
      {data && <SummaryTable rows={data.summary} winners={data.winners} />}
      {data && data.by_level && data.by_level.length > 0 && (
        <CompareByLevel models={data.models} rows={data.by_level} />
      )}
      {data && data.by_tag && data.by_tag.length > 0 && (
        <CompareByTag models={data.models} rows={data.by_tag} />
      )}
      {data && data.problems.length > 0 && (
        <PerProblemMatrix models={data.models} rows={data.problems} />
      )}
      {data && data.summary.every(s => s.n_problems_attempted === 0) && (
        <div className="empty">The selected submissions have no attempts.</div>
      )}
    </div>
  );
}
