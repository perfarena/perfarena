import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, CompetitionPublic, RankingEntry } from "../lib/api";
import {
  coefficientClass, formatCoefficient, formatDuration, formatMemory,
  formatPassAt1, formatPercentile, formatRuntime,
} from "../lib/format";
import Th from "../components/Th";
import PromptName from "../components/PromptName";
import LanguageLink from "../components/LanguageLink";

const LANGS = ["python3", "java", "cpp", "javascript", "typescript", "csharp", "c",
  "golang", "rust", "kotlin", "ruby", "swift", "scala", "php"];

const DEFAULT_RANGES = {
  passMin: "", passMax: "",
  rtMin: "", rtMax: "",
  memMin: "", memMax: "",
};

// Columns that sort lexicographically; everything else is numeric.
const STRING_COLS = new Set(["model_name", "submitter", "language"]);

// Default competition shown on the leaderboard until the user picks another.
// The seed competition contains every problem, so this is equivalent to
// "no filter" on a fresh install — but switching to e.g. HumanEval later
// scopes the leaderboard to that competition's problem set.
const DEFAULT_COMPETITION = "leetcode-99-perf";

export default function Rankings() {
  const [rows, setRows] = useState<RankingEntry[]>([]);
  const [language, setLanguage] = useState<string>("");
  const [level, setLevel] = useState<string>("");
  const [k, setK] = useState<string>("");                    // "" = any
  const [competitionSlug, setCompetitionSlug] = useState<string>(DEFAULT_COMPETITION);
  const [competitions, setCompetitions] = useState<CompetitionPublic[]>([]);
  const [err, setErr] = useState<string>("");

  // Load competitions once for the selector.
  useEffect(() => {
    api.competitions().then(setCompetitions).catch(() => setCompetitions([]));
  }, []);

  // Client-side view sort, toggled by clicking column headers.
  const [sortKey, setSortKey] = useState<string>("pass_at_1");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const [q, setQ] = useState("");
  const [ranges, setRanges] = useState(DEFAULT_RANGES);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  useEffect(() => {
    const kNum = k === "" ? undefined : Number(k);
    api.rankings({
      language, level, k: kNum,
      competition: competitionSlug || undefined,
    })
      .then(setRows).catch((e: unknown) => setErr(String(e)));
  }, [language, level, k, competitionSlug]);

  useEffect(() => { setPage(1); }, [language, level, k, competitionSlug, q, ranges, pageSize, sortKey, sortDir]);

  const selectedCompetition = competitions.find(c => c.slug === competitionSlug) ?? null;

  function onSort(key: string) {
    if (key === sortKey) {
      setSortDir(d => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Numbers default high→low (best first); strings default A→Z.
      setSortDir(STRING_COLS.has(key) ? "asc" : "desc");
    }
  }

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const passMin = numOr(ranges.passMin, -Infinity) / 100;
    const passMax = numOr(ranges.passMax,  Infinity) / 100;
    const rtMin = numOr(ranges.rtMin,  -Infinity);
    const rtMax = numOr(ranges.rtMax,   Infinity);
    const memMin = numOr(ranges.memMin, -Infinity);
    const memMax = numOr(ranges.memMax,  Infinity);

    return rows.filter(r => {
      if (needle) {
        const hay = (
          r.model_name + " " + r.model_version + " " +
          r.submitter + " " + r.language
        ).toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      if (r.pass_at_1 < passMin || r.pass_at_1 > passMax) return false;
      if (!inRange(r.avg_runtime_percentile, rtMin, rtMax)) return false;
      if (!inRange(r.avg_memory_percentile, memMin, memMax)) return false;
      return true;
    });
  }, [rows, q, ranges]);

  // Raw ms/MB are only meaningful within one language. When no language
  // filter is set we visually de-emphasise those cells (and the banner above
  // tells the user why).
  const rawCellStyle: React.CSSProperties | undefined =
    language ? undefined : { opacity: 0.45 };

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      const av = (a as unknown as Record<string, unknown>)[sortKey];
      const bv = (b as unknown as Record<string, unknown>)[sortKey];
      const aNull = av == null || av === "";
      const bNull = bv == null || bv === "";
      if (aNull && bNull) return 0;
      if (aNull) return 1;          // missing values always sort last
      if (bNull) return -1;
      const cmp = STRING_COLS.has(sortKey)
        ? String(av).localeCompare(String(bv))
        : Number(av) - Number(bv);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  const total = sorted.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * pageSize;
  const paged = sorted.slice(start, start + pageSize);
  const anyAdvanced = Object.values(ranges).some(v => v !== "");

  return (
    <div>
      <div className="hero">
        <h1>Leaderboard</h1>
        <p>
          Pass@1, runtime percentile, and memory percentile per
          model × language × difficulty —
          {" "}<b>scoped to the competition you pick below.</b> Use the
          {" "}<Link to="/competitions">Competitions</Link> page for the full
          list of contests and to add new ones.
        </p>
        {selectedCompetition && (
          <div className="card" style={{
            marginTop: 12,
            background: "var(--accent-soft)",
            borderColor: "var(--accent)",
          }}>
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <div className="col" style={{ gap: 2 }}>
                <div style={{ fontWeight: 700 }}>
                  <Link to={`/competitions/${selectedCompetition.slug}`}>
                    {selectedCompetition.name}
                  </Link>
                  <span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>
                    · <code>{selectedCompetition.slug}</code> · {selectedCompetition.kind}
                  </span>
                </div>
                {selectedCompetition.description && (
                  <div className="muted" style={{ fontSize: "0.88rem" }}>
                    {selectedCompetition.description}
                  </div>
                )}
              </div>
              <div className="muted" style={{ fontSize: "0.85rem", whiteSpace: "nowrap" }}>
                {selectedCompetition.n_problems} problems · {selectedCompetition.n_submissions} submissions · {selectedCompetition.n_models} models
              </div>
            </div>
          </div>
        )}
      </div>

      {!language && (
        <div className="card" style={{
          borderColor: "var(--medium)",
          background: "rgba(220,180,80,0.08)",
          fontSize: "0.88rem",
        }}>
          <b>Reading this table across languages?</b> Raw <code>Avg runtime</code>,{" "}
          <code>Best runtime</code>, <code>Avg memory</code> and <code>Best memory</code> columns are
          measured in absolute ms / MB and aren't comparable across languages —
          Python's interpreter alone adds ~30-50 ms to every solution, and
          LeetCode gives each language a different time limit. They're muted
          for that reason. For cross-language comparisons use the percentile
          columns and the <code>t vs avg</code> / <code>t vs exp</code> / <code>m vs avg</code> /{" "}
          <code>m vs exp</code> coefficients — those are normalised per-(problem, language)
          baseline and are directly comparable. Pick a language filter to
          un-mute the raw columns.
        </div>
      )}

      <div className="filter-bar">
        <input
          className="grow"
          placeholder="Search model, submitter, language…"
          value={q}
          onChange={e => setQ(e.target.value)}
          style={{ minWidth: 240 }}
        />
        <select value={competitionSlug}
                onChange={e => setCompetitionSlug(e.target.value)}
                title="Scope the leaderboard to one competition's problem set">
          {/* Empty value = no competition filter (full platform-wide
              leaderboard). Default lands on the seed LC-99. */}
          <option value="">All competitions</option>
          {competitions.map(c => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
        <select value={language} onChange={e => setLanguage(e.target.value)}>
          <option value="">All languages</option>
          {LANGS.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
        <select value={level} onChange={e => setLevel(e.target.value)}>
          <option value="">All difficulties</option>
          <option>Easy</option><option>Medium</option><option>Hard</option>
        </select>
        <select value={k} onChange={e => setK(e.target.value)}
                title="Filter by sample budget — submissions with the same k are directly comparable">
          <option value="">Any k</option>
          {Array.from(new Set(rows.map(r => r.n_samples_per_problem))).sort((a, b) => a - b).map(kv => (
            <option key={kv} value={kv}>k = {kv}</option>
          ))}
        </select>
        <button
          className="secondary"
          onClick={() => setShowAdvanced(s => !s)}
          aria-expanded={showAdvanced}
        >
          {showAdvanced ? "Hide" : "More"} filters{anyAdvanced ? " ●" : ""}
        </button>
      </div>

      {showAdvanced && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row" style={{ flexWrap: "wrap", gap: 24 }}>
            <RangeInput
              label="pass@1 (%)"
              minVal={ranges.passMin} maxVal={ranges.passMax}
              onMin={v => setRanges(r => ({ ...r, passMin: v }))}
              onMax={v => setRanges(r => ({ ...r, passMax: v }))}
              hintMin="0" hintMax="100"
            />
            <RangeInput
              label="avg runtime %ile"
              minVal={ranges.rtMin} maxVal={ranges.rtMax}
              onMin={v => setRanges(r => ({ ...r, rtMin: v }))}
              onMax={v => setRanges(r => ({ ...r, rtMax: v }))}
              hintMin="0" hintMax="100"
            />
            <RangeInput
              label="avg memory %ile"
              minVal={ranges.memMin} maxVal={ranges.memMax}
              onMin={v => setRanges(r => ({ ...r, memMin: v }))}
              onMax={v => setRanges(r => ({ ...r, memMax: v }))}
              hintMin="0" hintMax="100"
            />
            <button className="secondary" onClick={() => setRanges(DEFAULT_RANGES)} disabled={!anyAdvanced}>
              Reset
            </button>
          </div>
        </div>
      )}

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      {rows.length === 0 ? (
        <div className="empty">No judged submissions yet. Submit a batch from the <Link to="/submit">Submit</Link> page (auth required).</div>
      ) : total === 0 ? (
        <div className="empty">No rows match the current filters.</div>
      ) : (
        <>
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <Th label="#" tip="Row position under the current sort. Click any sortable header to re-sort; click again to flip the direction." />
                <Th label="Model" tip="Model name and version. Click the name to open the model's aggregate page." sortKey="model_name" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Submitter" tip="Organization or individual who submitted the batch. Click to open all of their submissions." sortKey="submitter" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Lang" tip="Programming language used for every solution in the batch. Submissions are language-specific — one batch is always one language." sortKey="language" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Prompt" tip="Registered prompt template that produced these solutions (clickable). 'ad-hoc' = inline prompt text, not from the registry. Submissions using different prompts are different experimental cells." />
                <Th label="Competition" tip="Competition this submission targeted. pass@1, pass@k, and Solved use THIS competition's problem set as the denominator (not 'every Problem row in the DB'). — = legacy row created before the per-submission scope existed." />
                <Th label="k" tip="Sample budget per problem (n_samples_per_problem). The submitter commits to providing exactly k samples per problem at submission creation. k=1 is the classic single-shot setup; k>1 lets you report pass@k." sortKey="n_samples_per_problem" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@1" tip="Unbiased pass@1 estimator over k samples — average of c/n per problem. Equivalent to single-shot pass-rate when k=1, and statistically more reliable when k>1. Use this column to compare submissions across different k values." sortKey="pass_at_1" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@k" tip="Mean acceptance rate across the SUBMITTED attempts: total accepted samples / total submitted samples. Equivalently, the average of per-problem acceptance fractions when k is constant. Denominator is the count of attempts actually submitted, NOT total supported problems — so a partial submission isn't penalised for unattempted problems. Differs from pass@1 (which divides by total supported) and pass@full (best-of-k per problem)." sortKey="pass_at_k" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@full" tip="Fraction of all supported problems with at least one accepted sample (best-of-k per problem). Always ≤ pass@1 ≤ pass@k for partial submissions with high accept rate. Useful as the maximum achievable rate the submitter unlocks at this sample budget." sortKey="pass_at_full" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Solved" tip="Number of problems with at least one accepted sample / total supported problems. Sorts by problems solved." sortKey="total_solved" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Attempts" tip="Total number of attempts submitted (one Attempt = one code + trace POST). Equals k × total problems for a complete submission." sortKey="n_attempts" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Avg runtime" tip="Each problem is submitted 5× to smooth LeetCode's noise. Per problem we take the MEAN of its accepted runs; this column is the mean of those per-problem means across all accepted problems." sortKey="avg_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best runtime" tip="Per problem we take the FASTEST of its 5 runs; this column is the mean of those per-problem bests across all accepted problems." sortKey="best_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Avg memory" tip="Mean of per-problem mean memory (5 runs each) across all accepted problems." sortKey="avg_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best memory" tip="Mean of per-problem minimum memory (5 runs each) across all accepted problems." sortKey="best_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Runtime %ile" tip="Mean of the per-problem LeetCode runtime percentile, averaged across accepted attempts. Higher = LC ranked the run as faster than more of the LC population AT THE TIME. The per-run percentile is noisy (~4 pp spread within a single attempt's 5 runs — see docs/leetcode-percentile-semantics.md); the ~500-sample mean shown here smooths most of that out." sortKey="avg_runtime_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Memory %ile" tip="Mean of the per-problem LeetCode memory percentile, averaged across accepted attempts. Same noise caveat as Runtime %ile, but memory %ile tends to be much less noisy than runtime %ile in practice." sortKey="avg_memory_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="t vs avg" tip="Speedup against the AVERAGE LeetCode submission. Computed per accepted attempt as leetcode_avg_runtime_ms / model_runtime_ms, then averaged. 1.0× = parity; 2.0× = twice as fast as the LeetCode average. (The LeetCode population mixes human and AI-assisted submissions — LeetCode doesn't separate them.)" sortKey="time_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="t vs exp" tip="Speedup against the EXPERT cohort on LeetCode (~top decile of the population distribution). leetcode_expert_runtime_ms / model_runtime_ms averaged across accepted attempts. ≥1.0× means the model is at least matching top LeetCode performers on time." sortKey="time_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="m vs avg" tip="Memory ratio vs. the AVERAGE LeetCode submission: leetcode_avg_memory_mb / model_memory_mb averaged across accepted attempts. >1.0× = the model uses less memory than the LeetCode average." sortKey="mem_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="m vs exp" tip="Memory ratio vs. the EXPERT LeetCode cohort. >1.0× = the model is leaner than top LeetCode performers." sortKey="mem_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Total tokens" tip="Sum of code-only response tokens across every attempt in this submission. Scales with attempt count (and therefore with problems covered); use 'Median tokens / att' to compare typical spend per problem instead." sortKey="total_response_tokens" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Median tokens / att" tip="Per-attempt median of response_tokens. Comparable across submissions regardless of how many problems each covers — answers 'how many tokens does ONE solution from this model cost, typically?'" sortKey="median_response_tokens_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Total cost ($)" tip="Sum of LLM cost across every attempt in this submission. Live-recomputed from /models pricing × stored tokens when available; falls back to the snapshot cost stamped at submit time for legacy attempts. 0 for all-Ollama submissions." sortKey="total_cost_usd" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Median $ / att" tip="Per-attempt median cost in USD. Apples-to-apples 'typical cost per problem solved' regardless of submission size." sortKey="median_cost_usd_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Total gen time" tip="Sum of generation wall-clock across every attempt in this submission. Scales with attempt count; use 'Median gen time / att' to compare typical per-solution latency instead." sortKey="total_generation_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Median gen time / att" tip="Per-attempt median of wall_clock_ms — typical 'how long does ONE solution take this model to generate?' regardless of how many problems the submission covered." sortKey="median_generation_ms_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <th></th>
              </tr></thead>
              <tbody>
                {paged.map((r, i) => (
                  <tr key={r.submission_id}>
                    <td>{start + i + 1}</td>
                    <td>
                      <Link to={`/models/${encodeURIComponent(r.model_name)}`}>
                        <b>{r.model_name}</b>
                      </Link>
                      <span className="muted"> {r.model_version}</span>
                    </td>
                    <td><Link to={`/submitters/${r.submitter_slug}`}>{r.submitter}</Link></td>
                    <td><LanguageLink name={r.language} /></td>
                    <td><PromptName id={r.prompt_id} name={r.prompt_name} /></td>
                    <td>
                      {r.competition_slug
                        ? <Link to={`/competitions/${r.competition_slug}`}
                                style={{
                                  padding: "2px 8px", borderRadius: 999,
                                  background: "var(--panel-2)",
                                  border: "1px solid var(--border)",
                                  fontSize: "0.8rem", whiteSpace: "nowrap",
                                  textDecoration: "none",
                                }}>{r.competition_name ?? r.competition_slug}</Link>
                        : <span className="muted">—</span>}
                    </td>
                    <td className="metric">{r.n_samples_per_problem}</td>
                    <td className="metric">{formatPassAt1(r.pass_at_1)}</td>
                    <td className="metric">{formatPassAt1(r.pass_at_k)}</td>
                    <td className="metric">{formatPassAt1(r.pass_at_full)}</td>
                    <td className="metric">{r.total_solved}/{r.total_problems}</td>
                    <td className="metric">{r.n_attempts}</td>
                    <td className="metric" style={rawCellStyle}>{formatRuntime(r.avg_runtime_ms)}</td>
                    <td className="metric" style={rawCellStyle}>{formatRuntime(r.best_runtime_ms)}</td>
                    <td className="metric" style={rawCellStyle}>{formatMemory(r.avg_memory_mb)}</td>
                    <td className="metric" style={rawCellStyle}>{formatMemory(r.best_memory_mb)}</td>
                    <td className="metric">{formatPercentile(r.avg_runtime_percentile)}</td>
                    <td className="metric">{formatPercentile(r.avg_memory_percentile)}</td>
                    <td className={`metric ${coefficientClass(r.time_human_avg)}`}>{formatCoefficient(r.time_human_avg)}</td>
                    <td className={`metric ${coefficientClass(r.time_human_expert)}`}>{formatCoefficient(r.time_human_expert)}</td>
                    <td className={`metric ${coefficientClass(r.mem_human_avg)}`}>{formatCoefficient(r.mem_human_avg)}</td>
                    <td className={`metric ${coefficientClass(r.mem_human_expert)}`}>{formatCoefficient(r.mem_human_expert)}</td>
                    <td className="metric">{formatTokenCount(r.total_response_tokens)}</td>
                    <td className="metric">{formatTokenCount(r.median_response_tokens_per_attempt)}</td>
                    <td className="metric">{formatDollar(r.total_cost_usd)}</td>
                    <td className="metric">{formatDollar(r.median_cost_usd_per_attempt)}</td>
                    <td className="metric">{formatDuration(r.total_generation_ms)}</td>
                    <td className="metric">{formatDuration(r.median_generation_ms_per_attempt)}</td>
                    <td><Link to={`/submissions/${r.submission_id}`}>details →</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Pagination
            page={safePage} totalPages={totalPages}
            pageSize={pageSize}
            onPage={setPage} onPageSize={setPageSize}
            shownFrom={start + 1} shownTo={start + paged.length} total={total}
          />
        </>
      )}
    </div>
  );
}

function RangeInput({
  label, minVal, maxVal, onMin, onMax, hintMin, hintMax,
}: {
  label: string;
  minVal: string; maxVal: string;
  onMin: (v: string) => void; onMax: (v: string) => void;
  hintMin?: string; hintMax?: string;
}) {
  return (
    <div className="col" style={{ gap: 4, minWidth: 200 }}>
      <span className="muted" style={{ fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</span>
      <div className="row" style={{ gap: 6 }}>
        <input
          type="number" inputMode="decimal"
          value={minVal} onChange={e => onMin(e.target.value)}
          placeholder={hintMin ?? "min"} style={{ width: 90 }}
        />
        <span className="muted">–</span>
        <input
          type="number" inputMode="decimal"
          value={maxVal} onChange={e => onMax(e.target.value)}
          placeholder={hintMax ?? "max"} style={{ width: 90 }}
        />
      </div>
    </div>
  );
}

function Pagination({
  page, totalPages, pageSize, onPage, onPageSize,
  shownFrom, shownTo, total,
}: {
  page: number; totalPages: number; pageSize: number;
  onPage: (p: number) => void; onPageSize: (s: number) => void;
  shownFrom: number; shownTo: number; total: number;
}) {
  return (
    <div className="row" style={{ justifyContent: "space-between", marginTop: 12, flexWrap: "wrap", gap: 12 }}>
      <span className="muted">Showing {shownFrom}–{shownTo} of {total}</span>
      <div className="row" style={{ gap: 8 }}>
        <select value={pageSize} onChange={e => onPageSize(Number(e.target.value))}>
          {[10, 25, 50, 100].map(n => <option key={n} value={n}>{n} / page</option>)}
        </select>
        <button className="secondary" onClick={() => onPage(1)} disabled={page <= 1}>«</button>
        <button className="secondary" onClick={() => onPage(page - 1)} disabled={page <= 1}>‹</button>
        <span className="muted">page {page} / {totalPages}</span>
        <button className="secondary" onClick={() => onPage(page + 1)} disabled={page >= totalPages}>›</button>
        <button className="secondary" onClick={() => onPage(totalPages)} disabled={page >= totalPages}>»</button>
      </div>
    </div>
  );
}

function numOr(s: string, fallback: number): number {
  if (s === "") return fallback;
  const n = Number(s);
  return Number.isFinite(n) ? n : fallback;
}

function inRange(v: number | null, min: number, max: number): boolean {
  if (v == null) return min === -Infinity && max === Infinity;
  return v >= min && v <= max;
}

/* Display formatters for the four new $/token columns. Null → em-dash
 * so the legacy submissions that didn't capture cost/tokens don't render
 * as "$0" (which would be misleading — 0 means free, dash means unknown).
 *
 * Tokens are formatted with a thousands separator and a "k" suffix above
 * 10k to keep the column narrow on a busy table. Dollar amounts use a
 * 4-decimal format below $0.01 (most LLM calls land here) and 2-decimal
 * above so the column reads naturally regardless of order of magnitude. */
function formatTokenCount(v: number | null | undefined): string {
  if (v == null) return "–";
  if (v >= 10_000) return (v / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function formatDollar(v: number | null | undefined): string {
  if (v == null) return "–";
  if (v === 0) return "$0";
  if (v < 0.01)  return `$${v.toFixed(4)}`;
  if (v < 1)     return `$${v.toFixed(3)}`;
  if (v < 1000)  return `$${v.toFixed(2)}`;
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
