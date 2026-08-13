import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, CompetitionPublic, CorrectnessOraclePublic, HarnessPublic, RankingEntry,
} from "../lib/api";
import {
  coefficientClass, formatCoefficient, formatDuration, formatEnergy, formatMemory,
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
  // Harness filter. Empty = "any harness" (default) so users landing on the
  // page see the full board; picking one scopes to submissions scored by
  // that oracle. Hydrated from ?harness=… on first mount so the HarnessDetail
  // page's "See full leaderboard on this harness →" deep link works.
  const [harnessSlug, setHarnessSlug] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("harness") ?? "";
  });
  const [harnesses, setHarnesses] = useState<HarnessPublic[]>([]);
  // Correctness-oracle filter. Same URL-hydration pattern as harness so a
  // deep link from /correctness-oracles/{slug}'s "See full leaderboard"
  // button lands scoped correctly.
  const [correctnessOracleSlug, setCorrectnessOracleSlug] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("correctness_oracle") ?? "";
  });
  const [correctnessOracles, setCorrectnessOracles] = useState<CorrectnessOraclePublic[]>([]);
  const [err, setErr] = useState<string>("");

  // Load competitions + harnesses + correctness oracles once for the selectors.
  useEffect(() => {
    api.competitions().then(setCompetitions).catch(() => setCompetitions([]));
    api.harnesses().then(setHarnesses).catch(() => setHarnesses([]));
    api.correctnessOracles().then(setCorrectnessOracles).catch(() => setCorrectnessOracles([]));
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
      harness: harnessSlug || undefined,
      correctness_oracle: correctnessOracleSlug || undefined,
    })
      .then(setRows).catch((e: unknown) => setErr(String(e)));
  }, [language, level, k, competitionSlug, harnessSlug, correctnessOracleSlug]);

  useEffect(() => { setPage(1); }, [language, level, k, competitionSlug, harnessSlug, correctnessOracleSlug, q, ranges, pageSize, sortKey, sortDir]);

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

  // Energy columns are meaningful only when at least one row in scope came
  // from a harness that measured execution energy. LeetCode-only data leaves
  // every field null; hiding the columns in that case keeps the table narrow
  // and stops the header from advertising a metric nothing populates. As
  // soon as an execution-instrumented harness lands a submission, the
  // columns appear automatically.
  const showEnergy = rows.some(r =>
    r.avg_runtime_energy_j != null || r.best_runtime_energy_j != null,
  );

  // Machine / energy-tool provenance column — meaningful only when at least
  // one row in scope was measured on a known machine (execution-instrumented
  // harnesses populate it; LeetCode-scored rows leave it null). Energy is only
  // comparable within one machine, so surfacing it lets a reader see which.
  const showMachine = rows.some(r => r.machine_name != null || r.energy_tool != null);

  return (
    <div>
      <div className="hero">
        <h1>Leaderboard</h1>
        <p>
          This page shows pass@1, runtime percentile, and memory
          percentile for each combination of model, language, and
          difficulty. The data applies to the competition that you
          select below. The
          {" "}<Link to="/competitions">Competitions</Link> page lists
          every competition. You can add a new competition there.
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
          <b>Do you read this table across languages?</b> The raw <code>Avg runtime</code>,{" "}
          <code>Best runtime</code>, <code>Peak mem</code> and <code>Best peak mem</code> columns
          use absolute ms or MB. These columns do not compare across languages.
          The Python interpreter adds approximately 30 to 50 ms to each solution.
          Also, LeetCode gives each language a different time limit. Therefore this
          table shows these columns as dim. To compare across languages, use the
          percentile columns and the <code>t vs avg</code>, <code>t vs exp</code>, <code>m vs avg</code>, and{" "}
          <code>m vs exp</code> coefficients. Each coefficient uses a baseline for each
          problem and language, so it compares directly. To show the raw columns
          fully, select a language filter.
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
                title="Limit the leaderboard to the problem set of one competition">
          {/* Empty value = no competition filter (full platform-wide
              leaderboard). Default lands on the seed LC-99. */}
          <option value="">All competitions</option>
          {competitions.map(c => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
        <select value={harnessSlug}
                onChange={e => setHarnessSlug(e.target.value)}
                title="Limit to the submissions that one performance oracle scored (for example, LeetCode or an internal harness)">
          <option value="">All perf oracles</option>
          {harnesses.map(h => (
            <option key={h.slug} value={h.slug}>{h.name}</option>
          ))}
        </select>
        <select value={correctnessOracleSlug}
                onChange={e => setCorrectnessOracleSlug(e.target.value)}
                title="Limit to the submissions that one correctness oracle scored. The correctness oracle gives the verdict. It is separate from the performance oracle above.">
          <option value="">All correctness oracles</option>
          {correctnessOracles.map(o => (
            <option key={o.slug} value={o.slug}>{o.name}</option>
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
                title="Filter by sample budget. Submissions that have the same k compare directly.">
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
        <div className="empty">No submissions have a result yet. Submit a batch from the <Link to="/submit">Submit</Link> page. You must sign in first.</div>
      ) : total === 0 ? (
        <div className="empty">No rows match the current filters.</div>
      ) : (
        <>
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <Th label="#" tip="The position of the row in the current sort order. Click a sortable header to sort again. Click the header a second time to reverse the direction." />
                <Th label="Model" tip="The name and version of the model. Click the name to open the aggregate page of the model." sortKey="model_name" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Submitter" tip="The organization or person that submitted the batch. Click to open all of their submissions." sortKey="submitter" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Lang" tip="The programming language of every solution in the batch. Each submission uses one language. One batch is always one language." sortKey="language" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Prompt" tip="The registered prompt template that made these solutions. Click it to open the template. 'ad-hoc' means inline prompt text that does not come from the registry. Submissions that use different prompts are different experiment cells." />
                <Th label="Competition" tip="The competition that this submission targeted. pass@1, pass@k, and Solved use the problem set of this competition as the denominator. They do not use every problem row in the database. A dash marks an old row that the system created before the per-submission scope existed." />
                <Th label="Harness" tip="The performance oracle that made the runtime, memory, and percentile numbers on this row. The system resolves it for each submission. A submission override wins; if there is none, the competition default applies. The startup backfill sets all old rows to `leetcode`. New harnesses appear when you connect them on /harnesses. Examples are an internal runner and a HumanEval unit test." />
                <Th label="Correctness" tip="The correctness oracle that made the verdict on this row. The verdict is Accepted, Wrong Answer, Runtime Error, or TLE. It is separate from the performance oracle at the left. Therefore one source can check a submission for correctness, and another source can measure it for performance. The system resolves every old row to `leetcode`." />
                <Th label="k" tip="The sample budget for each problem (n_samples_per_problem). The submitter agrees to give exactly k samples for each problem when they create the submission. k=1 is the single-shot setup. k>1 lets you report pass@k." sortKey="n_samples_per_problem" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@1" tip="The unbiased pass@1 estimator over k samples. It is the average of c/n for each problem. It equals the single-shot pass rate when k=1. It is more reliable when k>1. Use this column to compare submissions that have different k values." sortKey="pass_at_1" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@k" tip="The mean acceptance rate across the submitted attempts. It is the total accepted samples divided by the total submitted samples. When k is constant, it equals the average of the acceptance fraction for each problem. The denominator is the count of attempts that you submitted, not the total supported problems. Therefore a partial submission does not lose points for the problems that it does not attempt. This differs from pass@1, which divides by the total supported problems, and from pass@full, which takes the best of k for each problem." sortKey="pass_at_k" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@full" tip="The fraction of all supported problems that have at least one accepted sample. This is the best of k for each problem. For partial submissions that have a high accept rate, pass@full ≤ pass@1 ≤ pass@k. Use it as the maximum possible rate at this sample budget." sortKey="pass_at_full" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Solved" tip="The number of problems that have at least one accepted sample, divided by the total supported problems. It sorts by the number of solved problems." sortKey="total_solved" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Attempts" tip="The total number of attempts that you submitted. One attempt is one POST of code and a trace. For a complete submission, this equals k times the total problems." sortKey="n_attempts" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Avg runtime" tip="The team submits each problem 5 times to reduce the noise of LeetCode. For each problem, the team takes the mean of its accepted runs. This column is the mean of those per-problem means across all accepted problems." sortKey="avg_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best runtime" tip="For each problem, the team takes the fastest of its 5 runs. This column is the mean of those per-problem best values across all accepted problems." sortKey="best_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Peak mem" tip="The mean of the per-problem mean peak memory across all accepted problems, with 5 runs for each problem. Memory is the peak (maximum resident) value on both oracles. It is never a per-op value." sortKey="avg_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best peak mem" tip="The mean of the per-problem minimum peak memory across all accepted problems, with 5 runs for each problem." sortKey="best_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Runtime %ile" tip="The mean of the per-problem LeetCode runtime percentile across accepted attempts. A higher value means LeetCode ranked the run as faster than more of the LeetCode population at that time. The percentile of a single run is noisy. It varies by approximately 4 percentage points across the 5 runs of one attempt (see docs/leetcode-percentile-semantics.md). The mean of approximately 500 samples that this column shows removes most of that noise." sortKey="avg_runtime_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Memory %ile" tip="The mean of the per-problem LeetCode memory percentile across accepted attempts. The same noise note applies as for Runtime %ile. In practice, memory %ile is much less noisy than runtime %ile." sortKey="avg_memory_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                {showEnergy && <Th label="Avg energy" tip="The mean of the per-problem mean runtime energy in joules, across accepted problems. This column shows a value only when the scoring harness measures execution energy. LeetCode never measures it. A lower value is better." sortKey="avg_runtime_energy_j" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showEnergy && <Th label="Best energy" tip="The mean of the per-problem best (minimum) runtime energy across accepted problems. It has the same source as Avg energy, but it takes the lowest energy value for each problem." sortKey="best_runtime_energy_j" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showEnergy && <Th label="Energy %ile" tip="The mean of the per-problem runtime-energy percentile that the harness reports, across accepted attempts. A higher value means the model used less energy than more of the baseline population of the harness." sortKey="avg_runtime_energy_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showMachine && <Th label="Machine" tip="The machine and energy tool that measured the runtime and energy numbers of this row. Energy and time compare only within one pair of machine and energy tool. A dash marks a row that LeetCode scored. LeetCode ran it on its own servers, so the machine is unknown." />}
                <Th label="t vs avg" tip="The speedup against the average LeetCode submission. For each accepted attempt, the team computes leetcode_avg_runtime_ms / model_runtime_ms, then takes the average. 1.0× means equal speed. 2.0× means twice as fast as the LeetCode average. The LeetCode population mixes human submissions and AI-assisted submissions. LeetCode does not separate them." sortKey="time_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="t vs exp" tip="The speedup against the expert cohort on LeetCode. This cohort is approximately the top ten percent of the population. The team computes leetcode_expert_runtime_ms / model_runtime_ms and takes the average across accepted attempts. A value of 1.0× or more means the model is at least as fast as the top LeetCode performers." sortKey="time_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="m vs avg" tip="The memory ratio against the average LeetCode submission. The team computes leetcode_avg_memory_mb / model_memory_mb and takes the average across accepted attempts. A value above 1.0× means the model uses less memory than the LeetCode average." sortKey="mem_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="m vs exp" tip="The memory ratio against the expert LeetCode cohort. A value above 1.0× means the model uses less memory than the top LeetCode performers." sortKey="mem_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Total tokens" tip="The sum of the code-only response tokens across every attempt in this submission. It grows with the number of attempts, and therefore with the number of problems. To compare the typical cost for each problem, use 'Median tokens / att'." sortKey="total_response_tokens" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Median tokens / att" tip="The median of response_tokens for each attempt. It compares across submissions, although each submission covers a different number of problems. It answers this question: how many tokens does one solution from this model usually cost?" sortKey="median_response_tokens_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Total cost ($)" tip="The sum of the language model cost across every attempt in this submission. When pricing is available, the system computes it again from the /models pricing and the stored tokens. For old attempts, the system uses the snapshot cost from submit time. It is 0 for submissions that use only Ollama." sortKey="total_cost_usd" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Median $ / att" tip="The median cost in USD for each attempt. It gives a fair comparison of the typical cost for each solved problem, although the submissions have different sizes." sortKey="median_cost_usd_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Total gen time" tip="The sum of the generation wall-clock time across every attempt in this submission. It grows with the number of attempts. To compare the typical latency for each solution, use 'Median gen time / att'." sortKey="total_generation_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Median gen time / att" tip="The median of wall_clock_ms for each attempt. It shows how long this model usually takes to generate one solution, although the submissions cover a different number of problems." sortKey="median_generation_ms_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
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
                    <td>
                      {r.harness_slug
                        ? <Link to={`/harnesses/${r.harness_slug}`}
                                style={{
                                  padding: "2px 8px", borderRadius: 999,
                                  background: "var(--panel-2)",
                                  border: "1px solid var(--border)",
                                  fontSize: "0.8rem", whiteSpace: "nowrap",
                                  textDecoration: "none",
                                }}>{r.harness_name ?? r.harness_slug}</Link>
                        : <span className="muted">—</span>}
                    </td>
                    <td>
                      {r.correctness_oracle_slug
                        ? <Link to={`/correctness-oracles/${r.correctness_oracle_slug}`}
                                style={{
                                  padding: "2px 8px", borderRadius: 999,
                                  background: "var(--panel-2)",
                                  border: "1px solid var(--border)",
                                  fontSize: "0.8rem", whiteSpace: "nowrap",
                                  textDecoration: "none",
                                }}>{r.correctness_oracle_name ?? r.correctness_oracle_slug}</Link>
                        : <span className="muted">—</span>}
                    </td>
                    <td className="metric">{r.n_samples_per_problem}</td>
                    <td className="metric">{formatPassAt1(r.pass_at_1)}</td>
                    <td className="metric">{formatPassAt1(r.pass_at_k)}</td>
                    <td className="metric">{formatPassAt1(r.pass_at_full)}</td>
                    <td className="metric">{r.total_solved}/{r.total_problems}</td>
                    <td className="metric">{r.n_attempts}</td>
                    <td className="metric" style={rawCellStyle}>
                      {formatRuntime(r.avg_runtime_ms)}
                      {(r.machine_name != null || r.energy_tool != null) && r.avg_runtime_ms != null && (
                        <sup className="muted" style={{ fontSize: "0.68em", marginLeft: 1 }}>/op</sup>
                      )}
                    </td>
                    <td className="metric" style={rawCellStyle}>
                      {formatRuntime(r.best_runtime_ms)}
                      {(r.machine_name != null || r.energy_tool != null) && r.best_runtime_ms != null && (
                        <sup className="muted" style={{ fontSize: "0.68em", marginLeft: 1 }}>/op</sup>
                      )}
                    </td>
                    <td className="metric" style={rawCellStyle}>{formatMemory(r.avg_memory_mb)}</td>
                    <td className="metric" style={rawCellStyle}>{formatMemory(r.best_memory_mb)}</td>
                    <td className="metric">{formatPercentile(r.avg_runtime_percentile)}</td>
                    <td className="metric">{formatPercentile(r.avg_memory_percentile)}</td>
                    {showEnergy && <td className="metric">{formatEnergy(r.avg_runtime_energy_j)}</td>}
                    {showEnergy && <td className="metric">{formatEnergy(r.best_runtime_energy_j)}</td>}
                    {showEnergy && <td className="metric">{formatPercentile(r.avg_runtime_energy_percentile)}</td>}
                    {showMachine && (
                      <td>
                        {r.machine_name || r.energy_tool ? (
                          <span style={{
                            padding: "2px 8px", borderRadius: 999,
                            background: "var(--panel-2)",
                            border: "1px solid var(--border)",
                            fontSize: "0.8rem", whiteSpace: "nowrap",
                            display: "inline-flex", gap: 6, alignItems: "baseline",
                          }}>
                            {r.machine_name ?? "unknown"}
                            {r.energy_tool && (
                              <span className="muted" style={{ fontSize: "0.72rem" }}>{r.energy_tool}</span>
                            )}
                          </span>
                        ) : <span className="muted">—</span>}
                      </td>
                    )}
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
      <span className="muted">This page shows {shownFrom} to {shownTo} of {total}.</span>
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
