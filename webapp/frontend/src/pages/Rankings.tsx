import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, RankingEntry } from "../lib/api";
import {
  coefficientClass, formatCoefficient, formatMemory,
  formatPassAt1, formatPercentile, formatRuntime,
} from "../lib/format";
import Th from "../components/Th";
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

export default function Rankings() {
  const [rows, setRows] = useState<RankingEntry[]>([]);
  const [language, setLanguage] = useState<string>("");
  const [level, setLevel] = useState<string>("");
  const [k, setK] = useState<string>("");                    // "" = any
  const [err, setErr] = useState<string>("");

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
    api.rankings({ language, level, k: kNum })
      .then(setRows).catch((e: unknown) => setErr(String(e)));
  }, [language, level, k]);

  useEffect(() => { setPage(1); }, [language, level, k, q, ranges, pageSize, sortKey, sortDir]);

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
        <p>Pass@1, runtime percentile, and memory percentile per model × language × difficulty.</p>
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
                <Th label="k" tip="Sample budget per problem (n_samples_per_problem). The submitter commits to providing exactly k samples per problem at submission creation. k=1 is the classic single-shot setup; k>1 lets you report pass@k." sortKey="n_samples_per_problem" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@1" tip="Unbiased pass@1 estimator over k samples — average of c/n per problem. Equivalent to single-shot pass-rate when k=1, and statistically more reliable when k>1. Use this column to compare submissions across different k values." sortKey="pass_at_1" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@k" tip="Probability that at least one of the k sampled solutions per problem is accepted, averaged across problems. The standard code-LLM metric." sortKey="pass_at_k" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@full" tip="Fraction of problems with at least one accepted sample (best-of-k). Always ≥ pass@k. Useful as the maximum achievable rate the submitter unlocks at this sample budget." sortKey="pass_at_full" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Solved" tip="Number of problems with at least one accepted sample / total supported problems. Sorts by problems solved." sortKey="total_solved" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Attempts" tip="Total number of attempts submitted (one Attempt = one code + trace POST). Equals k × total problems for a complete submission." sortKey="n_attempts" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Avg runtime" tip="Each problem is submitted 5× to smooth LeetCode's noise. Per problem we take the MEAN of its accepted runs; this column is the mean of those per-problem means across all accepted problems." sortKey="avg_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best runtime" tip="Per problem we take the FASTEST of its 5 runs; this column is the mean of those per-problem bests across all accepted problems." sortKey="best_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Avg memory" tip="Mean of per-problem mean memory (5 runs each) across all accepted problems." sortKey="avg_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best memory" tip="Mean of per-problem minimum memory (5 runs each) across all accepted problems." sortKey="best_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Runtime %ile" tip="Mean of the per-problem LeetCode runtime percentile (the model's runtime vs. all OTHER submissions on the same problem in the same language), averaged across accepted attempts. Higher = faster than more humans." sortKey="avg_runtime_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Memory %ile" tip="Mean of the per-problem LeetCode memory percentile, averaged across accepted attempts. Higher = leaner than more humans." sortKey="avg_memory_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="t vs avg" tip="Speedup against the AVERAGE human submission. Computed per accepted attempt as human_avg_runtime_ms / model_runtime_ms, then averaged. 1.0× = parity; 2.0× = twice as fast as the average human." sortKey="time_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="t vs exp" tip="Speedup against the EXPERT human cohort (top decile-ish). human_expert_runtime_ms / model_runtime_ms averaged across accepted attempts. ≥1.0× means the model is at least matching top human performers on time." sortKey="time_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="m vs avg" tip="Memory ratio vs. the AVERAGE human submission: human_avg_memory_mb / model_memory_mb averaged across accepted attempts. >1.0× = the model uses less memory than the average human." sortKey="mem_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="m vs exp" tip="Memory ratio vs. the EXPERT human cohort. >1.0× = the model is leaner than top human performers." sortKey="mem_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />
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
