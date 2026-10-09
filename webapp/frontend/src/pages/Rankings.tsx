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
import { SHOW_RELATIVE_METRICS } from "../lib/flags";

// A submission must have attempted at least this fraction of the competition's
// problems to appear on the leaderboard — a partial run (a handful of problems)
// isn't comparable to a full sweep, so we hide it and disclaim the cutoff.
const MIN_ATTEMPTED_FRACTION = 1 / 3;

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
  const [competitionSlug, setCompetitionSlug] = useState<string>(() => {
    if (typeof window === "undefined") return DEFAULT_COMPETITION;
    // Honour ?competition=… so the homepage's per-competition "see full
    // leaderboard →" deep links land scoped to the right competition.
    return new URLSearchParams(window.location.search).get("competition") ?? DEFAULT_COMPETITION;
  });
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
    api.competitions().then(cs => {
      setCompetitions(cs);
      // Each tab scopes the board to exactly one competition. If the current
      // slug (the default or a ?competition=… deep link) isn't among the
      // loaded competitions, land on the first tab so the board is never empty.
      setCompetitionSlug(prev => (cs.some(c => c.slug === prev) ? prev : (cs[0]?.slug ?? prev)));
    }).catch(() => setCompetitions([]));
    api.harnesses().then(setHarnesses).catch(() => setHarnesses([]));
    api.correctnessOracles().then(setCorrectnessOracles).catch(() => setCorrectnessOracles([]));
  }, []);

  // Keep ?competition=… in sync so the chosen tab survives a refresh and is
  // shareable (the page already hydrates competitionSlug from this param).
  useEffect(() => {
    if (typeof window === "undefined" || !competitionSlug) return;
    const u = new URL(window.location.href);
    u.searchParams.set("competition", competitionSlug);
    window.history.replaceState(null, "", u);
  }, [competitionSlug]);

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
      // Coverage gate: hide submissions that attempted fewer than 1/3 of the
      // competition's problems — a partial run isn't comparable to a full sweep.
      // total_problems is the competition size for this row; legacy rows with
      // it unset (0/null) are kept (no denominator to gate on).
      if (r.total_problems && r.n_attempts < Math.ceil(r.total_problems * MIN_ATTEMPTED_FRACTION))
        return false;
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
          pass@1, runtime, and memory for every model × language × difficulty,
          scoped to the competition you pick below. The
          {" "}<Link to="/competitions">Competitions</Link> page lists
          every competition and lets you add new ones.
        </p>
        <p className="muted" style={{ fontSize: "0.85rem" }}>
          Only submissions that attempted at least <b>one third</b> of the
          selected competition's problems are shown — partial runs aren't
          comparable to full sweeps.
        </p>
        {competitions.length > 0 && (
          <div className="tabs" role="tablist" aria-label="Competition" style={{ marginTop: 12, marginBottom: 0 }}>
            {competitions.map(c => (
              <button
                key={c.slug}
                role="tab"
                aria-selected={c.slug === competitionSlug}
                className={`tab${c.slug === competitionSlug ? " active" : ""}`}
                onClick={() => setCompetitionSlug(c.slug)}
                title={c.description || c.name}
              >
                {c.name}
              </button>
            ))}
          </div>
        )}
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
          <b>Reading this table across languages?</b> The raw <code>Avg RT</code>,{" "}
          <code>Best RT</code>, <code>Peak mem</code> and <code>Best mem</code> columns are
          measured in absolute ms / MB and don't compare across languages.
          Python's interpreter alone adds ~30-50 ms to every solution, and
          LeetCode gives each language a different time limit, so those
          columns are muted here. Pick a language filter to un-mute them and
          compare within one language.
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
        <select value={harnessSlug}
                onChange={e => setHarnessSlug(e.target.value)}
                title="Scope to submissions scored by one PERFORMANCE oracle (LeetCode, an in-house harness, and so on)">
          <option value="">All perf oracles</option>
          {harnesses.map(h => (
            <option key={h.slug} value={h.slug}>{h.name}</option>
          ))}
        </select>
        <select value={correctnessOracleSlug}
                onChange={e => setCorrectnessOracleSlug(e.target.value)}
                title="Scope to submissions scored by one CORRECTNESS oracle (the verdict source, separate from the performance oracle above)">
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
                title="Filter by sample budget. Submissions with the same k compare directly.">
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
            {SHOW_RELATIVE_METRICS && (
              <>
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
              </>
            )}
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
                <Th label="Subm." tip="Submitter: organization or individual who submitted the batch. Click to open all of their submissions." sortKey="submitter" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Lang" tip="Programming language used for every solution in the batch. Submissions are language-specific: one batch is always one language." sortKey="language" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Prompt" tip="Registered prompt template that produced these solutions (clickable). 'ad-hoc' = inline prompt text, not from the registry. Submissions using different prompts are different experimental cells." />
                <Th label="Harness" tip="Perf oracle that produced the runtime / memory / percentile numbers on this row. Resolved per submission (submission override → competition default). All legacy rows resolve to `leetcode` via the startup backfill; new harnesses (in-house runner, HumanEval unittest, …) appear once you plug them in on /harnesses." />
                <Th label="Correct" tip="Correctness oracle that produced the Accepted / Wrong Answer / Runtime Error / TLE verdict on this row. Decoupled from the perf oracle to the left, so a submission can be judged for correctness by one source and measured for performance by another. Every legacy row resolves to `leetcode`." />
                <Th label="k" tip="Sample budget per problem (n_samples_per_problem). The submitter commits to providing exactly k samples per problem at submission creation. k=1 is the classic single-shot setup; k>1 lets you report pass@k." sortKey="n_samples_per_problem" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@1" tip="Unbiased pass@1 estimator over k samples: the average of c/n per problem. Equivalent to single-shot pass-rate when k=1, and statistically more reliable when k>1. Use this column to compare submissions across different k values." sortKey="pass_at_1" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@k" tip="Mean acceptance rate across the SUBMITTED attempts: total accepted samples / total submitted samples. Equivalently, the average of per-problem acceptance fractions when k is constant. Denominator is the count of attempts actually submitted, NOT total supported problems, so a partial submission isn't penalised for unattempted problems. Differs from pass@1 (which divides by total supported) and pass@full (best-of-k per problem)." sortKey="pass_at_k" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@full" tip="Fraction of all supported problems with at least one accepted sample (best-of-k per problem). Always ≤ pass@1 ≤ pass@k for partial submissions with high accept rate. Useful as the maximum achievable rate at this sample budget." sortKey="pass_at_full" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Solved" tip="Number of problems with at least one accepted sample / total supported problems. Sorts by problems solved." sortKey="total_solved" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Attempts" tip="Total number of attempts submitted (one Attempt = one code + trace POST). Equals k × total problems for a complete submission." sortKey="n_attempts" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Avg RT" tip="Average runtime. Each problem is submitted 5× to smooth LeetCode's noise. Per problem we take the MEAN of its accepted runs; this column is the mean of those per-problem means across all accepted problems." sortKey="avg_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best RT" tip="Best runtime. Per problem we take the FASTEST of its 5 runs; this column is the mean of those per-problem bests across all accepted problems." sortKey="best_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Peak mem" tip="Mean of per-problem mean PEAK memory (5 runs each) across all accepted problems. Memory is peak (max resident) on both oracles, never per function call." sortKey="avg_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best mem" tip="Best (lowest) peak memory. Mean of per-problem minimum PEAK memory (5 runs each) across all accepted problems." sortKey="best_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                {SHOW_RELATIVE_METRICS && <Th label="RT %ile" tip="Mean of the per-problem LeetCode runtime percentile, averaged across accepted attempts. Higher = LC ranked the run as faster than more of the LC population AT THE TIME. The per-run percentile is noisy (~4 pp spread within a single attempt's 5 runs; see docs/leetcode-percentile-semantics.md); the ~500-sample mean shown here smooths most of that out." sortKey="avg_runtime_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="Mem %ile" tip="Mean of the per-problem LeetCode memory percentile, averaged across accepted attempts. Same noise caveat as Runtime %ile, but memory %ile tends to be much less noisy than runtime %ile in practice." sortKey="avg_memory_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showEnergy && <Th label="Avg E" tip="Average energy (Joules): mean of per-problem mean runtime energy across accepted problems. Reported only when the scoring harness measures execution energy (LeetCode never does). Lower is better." sortKey="avg_runtime_energy_j" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showEnergy && <Th label="Best E" tip="Best energy (Joules): mean of per-problem BEST (minimum) runtime energy across accepted problems. Same source as Avg energy but takes the lowest energy reading per problem." sortKey="best_runtime_energy_j" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showEnergy && SHOW_RELATIVE_METRICS && <Th label="E %ile" tip="Mean of the per-problem runtime-energy percentile reported by the harness, averaged across accepted attempts. Higher = the model used less energy than more of the harness's baseline population." sortKey="avg_runtime_energy_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showMachine && <Th label="Machine" tip="Machine + energy tool this row's runtime / energy numbers were measured on. Energy and time are only comparable within one (machine, energy tool) pair. A dash marks a LeetCode-scored row, run on LeetCode's own servers, so the machine is unknown." />}
                {SHOW_RELATIVE_METRICS && <Th label="t vs avg" tip="Speedup against the AVERAGE LeetCode submission. Computed per accepted attempt as leetcode_avg_runtime_ms / model_runtime_ms, then averaged. 1.0× = parity; 2.0× = twice as fast as the LeetCode average. (The LeetCode population mixes human and AI-assisted submissions; LeetCode doesn't separate them.)" sortKey="time_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="t vs exp" tip="Speedup against the EXPERT cohort on LeetCode (~top decile of the population distribution). leetcode_expert_runtime_ms / model_runtime_ms averaged across accepted attempts. ≥1.0× means the model is at least matching top LeetCode performers on time." sortKey="time_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="m vs avg" tip="Memory ratio vs. the AVERAGE LeetCode submission: leetcode_avg_memory_mb / model_memory_mb averaged across accepted attempts. >1.0× = the model uses less memory than the LeetCode average." sortKey="mem_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="m vs exp" tip="Memory ratio vs. the EXPERT LeetCode cohort. >1.0× = the model is leaner than top LeetCode performers." sortKey="mem_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                <Th label="Tokens" tip="Total tokens: sum of code-only response tokens across every attempt in this submission. Scales with attempt count (and therefore with problems covered); use 'tok/att' to compare typical spend per problem instead." sortKey="total_response_tokens" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="tok/att" tip="Median tokens per attempt: per-attempt median of response_tokens. Comparable across submissions regardless of how many problems each covers. It answers 'how many tokens does ONE solution from this model cost, typically?'" sortKey="median_response_tokens_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Cost" tip="Total cost ($): sum of LLM cost across every attempt in this submission. Live-recomputed from /models pricing × stored tokens when available; falls back to the snapshot cost stamped at submit time for legacy attempts. 0 for all-Ollama submissions." sortKey="total_cost_usd" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="$/att" tip="Median cost per attempt in USD. Apples-to-apples 'typical cost per problem solved' regardless of submission size." sortKey="median_cost_usd_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Gen time" tip="Total generation wall-clock across every attempt in this submission. Scales with attempt count; use 'gen/att' to compare typical per-solution latency instead." sortKey="total_generation_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="gen/att" tip="Median generation time per attempt: per-attempt median of wall_clock_ms. Typical 'how long does ONE solution take this model to generate?' regardless of how many problems the submission covered." sortKey="median_generation_ms_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
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
                        <sup className="muted" style={{ fontSize: "0.68em", marginLeft: 1 }}>/call</sup>
                      )}
                    </td>
                    <td className="metric" style={rawCellStyle}>
                      {formatRuntime(r.best_runtime_ms)}
                      {(r.machine_name != null || r.energy_tool != null) && r.best_runtime_ms != null && (
                        <sup className="muted" style={{ fontSize: "0.68em", marginLeft: 1 }}>/call</sup>
                      )}
                    </td>
                    <td className="metric" style={rawCellStyle}>{formatMemory(r.avg_memory_mb)}</td>
                    <td className="metric" style={rawCellStyle}>{formatMemory(r.best_memory_mb)}</td>
                    {SHOW_RELATIVE_METRICS && <td className="metric">{formatPercentile(r.avg_runtime_percentile)}</td>}
                    {SHOW_RELATIVE_METRICS && <td className="metric">{formatPercentile(r.avg_memory_percentile)}</td>}
                    {showEnergy && <td className="metric">{formatEnergy(r.avg_runtime_energy_j)}</td>}
                    {showEnergy && <td className="metric">{formatEnergy(r.best_runtime_energy_j)}</td>}
                    {showEnergy && SHOW_RELATIVE_METRICS && <td className="metric">{formatPercentile(r.avg_runtime_energy_percentile)}</td>}
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
                    {SHOW_RELATIVE_METRICS && <td className={`metric ${coefficientClass(r.time_human_avg)}`}>{formatCoefficient(r.time_human_avg)}</td>}
                    {SHOW_RELATIVE_METRICS && <td className={`metric ${coefficientClass(r.time_human_expert)}`}>{formatCoefficient(r.time_human_expert)}</td>}
                    {SHOW_RELATIVE_METRICS && <td className={`metric ${coefficientClass(r.mem_human_avg)}`}>{formatCoefficient(r.mem_human_avg)}</td>}
                    {SHOW_RELATIVE_METRICS && <td className={`metric ${coefficientClass(r.mem_human_expert)}`}>{formatCoefficient(r.mem_human_expert)}</td>}
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
      <span className="muted">Showing {shownFrom} to {shownTo} of {total}</span>
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
