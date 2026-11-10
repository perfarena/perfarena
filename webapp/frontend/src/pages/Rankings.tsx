import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AgentSummary, api, CompetitionPublic, CorrectnessOraclePublic, HarnessPublic,
  PromptTemplatePublic, RankingEntry,
} from "../lib/api";
import {
  coefficientClass, formatCoefficient, formatDollar, formatDuration, formatEnergy, formatMemory,
  formatPassAt1, formatPercentile, formatRuntime,
} from "../lib/format";
import Th from "../components/Th";
import PromptName from "../components/PromptName";
import LanguageLink from "../components/LanguageLink";
import EfficiencyBoard, { ReferenceBadge } from "../components/EfficiencyBoard";
import { SHOW_RELATIVE_METRICS } from "../lib/flags";

// Two views of one competition. "efficiency" (the default) ranks each model ×
// workflow by win probability per metric; "submissions" lists every judged
// submission with its raw numbers.
type View = "efficiency" | "submissions";

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
  const [view, setView] = useState<View>(() => {
    if (typeof window === "undefined") return "efficiency";
    return new URLSearchParams(window.location.search).get("view") === "submissions"
      ? "submissions" : "efficiency";
  });
  // Workflow filters, shared by both views: agent slug, agent kind, and
  // single-round vs multi-round. "" = any.
  const [agent, setAgent] = useState<string>("");
  const [agentKind, setAgentKind] = useState<string>("");
  const [rounds, setRounds] = useState<string>("");
  const [promptId, setPromptId] = useState<string>("");
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [prompts, setPrompts] = useState<PromptTemplatePublic[]>([]);

  // Load competitions + harnesses + correctness oracles once for the selectors.
  useEffect(() => {
    api.agents().then(setAgents).catch(() => setAgents([]));
    api.prompts().then(setPrompts).catch(() => setPrompts([]));
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
    if (view === "submissions") u.searchParams.set("view", view);
    else u.searchParams.delete("view");
    window.history.replaceState(null, "", u);
  }, [competitionSlug, view]);

  // Client-side view sort, toggled by clicking column headers.
  const [sortKey, setSortKey] = useState<string>("pass_at_1");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const [q, setQ] = useState("");
  const [ranges, setRanges] = useState(DEFAULT_RANGES);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  useEffect(() => {
    if (view !== "submissions") return;
    const kNum = k === "" ? undefined : Number(k);
    api.rankings({
      language, level, k: kNum,
      prompt_id: promptId ? Number(promptId) : undefined,
      competition: competitionSlug || undefined,
      harness: harnessSlug || undefined,
      correctness_oracle: correctnessOracleSlug || undefined,
      agent: agent || undefined,
      agent_kind: agentKind || undefined,
      rounds: rounds || undefined,
    })
      .then(setRows).catch((e: unknown) => setErr(String(e)));
  }, [view, language, level, k, promptId, competitionSlug, harnessSlug, correctnessOracleSlug, agent, agentKind, rounds]);

  useEffect(() => { setPage(1); }, [language, level, k, promptId, competitionSlug, harnessSlug, correctnessOracleSlug, agent, agentKind, rounds, q, ranges, pageSize, sortKey, sortDir]);

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
          The efficiency ranking compares each pair of models on the problems
          that both models solved in the same language. For each metric, a
          model's win probability is its mean chance of beating another model
          on such a problem. The ranking also shows pass@1 and cost. The
          submissions view lists
          every judged submission with its raw numbers. The
          {" "}<Link to="/competitions">Competitions</Link> page lists
          every competition and lets you add new ones.
        </p>
        <p className="muted" style={{ fontSize: "0.85rem" }}>
          {view === "efficiency"
            ? <>A run that attempted less than <b>one third</b> of its problems is
                listed as partial and gets no rank.</>
            : <>This view shows only the submissions that attempted at least{" "}
                <b>one third</b> of the problems in the selected competition.
                Partial runs are not comparable to full sweeps.</>}
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

      <div className="row" style={{ gap: 6, marginBottom: 12 }} role="tablist" aria-label="View">
        <button className={view === "efficiency" ? "" : "secondary"} role="tab"
                aria-selected={view === "efficiency"} onClick={() => setView("efficiency")}>
          Efficiency ranking
        </button>
        <button className={view === "submissions" ? "" : "secondary"} role="tab"
                aria-selected={view === "submissions"} onClick={() => setView("submissions")}>
          Submissions
        </button>
      </div>

      {view === "submissions" && !language && (
        <div className="card" style={{
          borderColor: "var(--medium)",
          background: "rgba(220,180,80,0.08)",
          fontSize: "0.88rem",
        }}>
          <b>Do not compare the raw columns across languages.</b> The <code>Avg RT</code>,{" "}
          <code>Best RT</code>, <code>Peak mem</code>, and <code>Best mem</code> columns show
          absolute values in ms and MB. Python's interpreter alone adds ~30 to 50 ms to
          every solution, and LeetCode gives each language a different time limit.
          Thus, this table mutes these columns. To un-mute them and compare within
          one language, pick a language filter.
        </div>
      )}

      <div className="filter-bar">
        <input
          className="grow"
          placeholder={view === "efficiency" ? "Search model, workflow…" : "Search model, submitter, language…"}
          value={q}
          onChange={e => setQ(e.target.value)}
          style={{ minWidth: 240 }}
        />
        <select value={harnessSlug}
                onChange={e => setHarnessSlug(e.target.value)}
                title={view === "efficiency"
                  ? "The ranking uses the measurements of this performance oracle. One ranking never mixes oracles. The default is the oracle that scored the most submissions."
                  : "Show only the submissions that one PERFORMANCE oracle scored (LeetCode, an in-house harness, and so on)"}>
          <option value="">{view === "efficiency" ? "Main perf oracle" : "All perf oracles"}</option>
          {harnesses.map(h => (
            <option key={h.slug} value={h.slug}>{h.name}</option>
          ))}
        </select>
        {view === "submissions" && (
          <select value={correctnessOracleSlug}
                  onChange={e => setCorrectnessOracleSlug(e.target.value)}
                  title="Show only the submissions that one CORRECTNESS oracle judged. The correctness oracle gives the verdict and is separate from the performance oracle.">
            <option value="">All correctness oracles</option>
            {correctnessOracles.map(o => (
              <option key={o.slug} value={o.slug}>{o.name}</option>
            ))}
          </select>
        )}
        <select value={language} onChange={e => setLanguage(e.target.value)}
                title={view === "efficiency"
                  ? "'All languages' pools the duels of every language. One language ranks the models only inside that language. An entrant needs 3 solved problems in that language to get a rank."
                  : undefined}>
          <option value="">All languages</option>
          {LANGS.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
        <select value={agent} onChange={e => setAgent(e.target.value)}
                title="Workflow that produced the solutions: a plain prompt (direct) or one agent">
          <option value="">All workflows</option>
          {agents.map(a => <option key={a.slug} value={a.slug}>{a.name}</option>)}
        </select>
        <select value={agentKind} onChange={e => setAgentKind(e.target.value)}
                title="Kind of workflow: direct (a plain prompt), agentic, or custom">
          <option value="">Any kind</option>
          {Array.from(new Set(agents.map(a => a.kind))).sort().map(kd => (
            <option key={kd} value={kd}>{kd}</option>
          ))}
        </select>
        <select value={rounds} onChange={e => setRounds(e.target.value)}
                title="Single round: one answer per problem. Multi-round: the model refines its answer with feedback, and the last round counts.">
          <option value="">Any rounds</option>
          <option value="single">Single round</option>
          <option value="multi">Multi-round</option>
        </select>
        <select value={promptId} onChange={e => setPromptId(e.target.value)}
                title="Show only the submissions made with one registered prompt template">
          <option value="">Any prompt</option>
          {prompts.map(p => <option key={p.id} value={String(p.id)}>{p.name}</option>)}
        </select>
        {view === "submissions" && (
          <>
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
          </>
        )}
      </div>

      {view === "efficiency" && (
        <EfficiencyBoard competition={competitionSlug} harness={harnessSlug} language={language}
                         agent={agent} agentKind={agentKind} rounds={rounds} promptId={promptId}
                         query={q} />
      )}

      {view === "submissions" && showAdvanced && (
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

      {view === "submissions" && err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      {view !== "submissions" ? null : rows.length === 0 ? (
        <div className="empty">No judged submissions yet. Submit a batch from the <Link to="/submit">Submit</Link> page (you must log in).</div>
      ) : total === 0 ? (
        <div className="empty">No rows match the current filters.</div>
      ) : (
        <>
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <Th label="#" tip="Row position in the current sort order. Click a sortable header to sort by that column. Click it again to reverse the order." />
                <Th label="Model" tip="Model name and version. Click the name to open the model's aggregate page." sortKey="model_name" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Subm." tip="Submitter: organization or individual who submitted the batch. Click to open all of their submissions." sortKey="submitter" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Lang" tip="Programming language used for every solution in the batch. Submissions are language-specific: one batch is always one language." sortKey="language" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Prompt" tip="Registered prompt template that produced these solutions (clickable). 'ad-hoc' means inline prompt text that is not from the registry. Submissions with different prompts are different experimental cells." />
                <Th label="Workflow" tip="Workflow that produced the solutions: a plain prompt (direct) or an agent. Filter with the workflow selectors above." />
                <Th label="Harness" tip="Performance oracle that produced the runtime, memory, and percentile numbers on this row. Each submission resolves its own oracle: the submission override first, then the competition default. The startup backfill resolves all legacy rows to `leetcode`. New harnesses (in-house runner, HumanEval unittest, and so on) appear here after you add them on /harnesses." />
                <Th label="Correct" tip="Correctness oracle that gave the verdict on this row (Accepted, Wrong Answer, Runtime Error, or TLE). It is separate from the performance oracle to the left. Thus, one source can judge the correctness of a submission and another source can measure its performance. Every legacy row resolves to `leetcode`." />
                <Th label="k" tip="Sample budget per problem (n_samples_per_problem). When the submitter creates the submission, the submitter commits to exactly k samples per problem. k=1 is the classic single-shot setup. With k>1, you can report pass@k." sortKey="n_samples_per_problem" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@1" tip="Unbiased pass@1 estimator over k samples: the average of c/n per problem. When k=1, it is equal to the single-shot pass rate. When k>1, it is statistically more reliable. Use this column to compare submissions with different k values." sortKey="pass_at_1" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@k" tip="Mean acceptance rate across the SUBMITTED attempts: total accepted samples / total submitted samples. When k is constant, it is equal to the average of the per-problem acceptance fractions. The denominator is the number of attempts actually submitted, NOT the total number of supported problems. Thus, a partial submission gets no penalty for the problems that it did not attempt. This differs from pass@1 (which divides by the total supported problems) and from pass@full (best of k per problem)." sortKey="pass_at_k" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="pass@full" tip="Fraction of all supported problems with at least one accepted sample (best of k per problem). For partial submissions with a high acceptance rate, it is always ≤ pass@1 ≤ pass@k. Use it as the maximum achievable rate at this sample budget." sortKey="pass_at_full" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Solved" tip="Number of problems with at least one accepted sample / total supported problems. Sorts by problems solved." sortKey="total_solved" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Attempts" tip="Total number of attempts submitted (one attempt is one POST of code and trace). For a complete submission, it is equal to k × total problems." sortKey="n_attempts" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Avg RT" tip="Average runtime. We submit each problem 5× to smooth LeetCode's noise. For each problem, we take the MEAN of its accepted runs. This column is the mean of those per-problem means across all accepted problems." sortKey="avg_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best RT" tip="Best runtime. For each problem, we take the FASTEST of its 5 runs. This column is the mean of those per-problem bests across all accepted problems." sortKey="best_runtime_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Peak mem" tip="Mean of per-problem mean PEAK memory (5 runs each) across all accepted problems. Memory is peak (max resident) on both oracles, never per function call." sortKey="avg_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Best mem" tip="Best (lowest) peak memory. Mean of per-problem minimum PEAK memory (5 runs each) across all accepted problems." sortKey="best_memory_mb" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                {SHOW_RELATIVE_METRICS && <Th label="RT %ile" tip="Mean of the per-problem LeetCode runtime percentile, averaged across accepted attempts. Higher means that LeetCode ranked the run as faster than more of the LeetCode population AT THE TIME. The per-run percentile is noisy: the 5 runs of a single attempt spread by ~4 pp (see docs/leetcode-percentile-semantics.md). The ~500-sample mean shown here smooths most of that noise." sortKey="avg_runtime_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="Mem %ile" tip="Mean of the per-problem LeetCode memory percentile, averaged across accepted attempts. The same noise caveat as RT %ile applies, but in practice memory %ile is much less noisy than runtime %ile." sortKey="avg_memory_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showEnergy && <Th label="Avg E" tip="Average energy (joules): mean of per-problem mean runtime energy across accepted problems. Shown only when the scoring harness measures execution energy (LeetCode never does). Lower is better." sortKey="avg_runtime_energy_j" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showEnergy && <Th label="Best E" tip="Best energy (joules): mean of per-problem BEST (minimum) runtime energy across accepted problems. Same source as Avg E, but it takes the lowest energy reading per problem." sortKey="best_runtime_energy_j" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showEnergy && SHOW_RELATIVE_METRICS && <Th label="E %ile" tip="Mean of the per-problem runtime-energy percentile reported by the harness, averaged across accepted attempts. Higher = the model used less energy than more of the harness's baseline population." sortKey="avg_runtime_energy_percentile" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {showMachine && <Th label="Machine" tip="Machine and energy tool that measured the runtime and energy numbers on this row. Energy and time are comparable only within one (machine, energy tool) pair. A dash marks a row that LeetCode scored on its own servers, so the machine is unknown." />}
                {SHOW_RELATIVE_METRICS && <Th label="t vs avg" tip="Speedup against the AVERAGE LeetCode submission. We compute it per accepted attempt as leetcode_avg_runtime_ms / model_runtime_ms, then average it. 1.0× means parity. 2.0× means twice as fast as the LeetCode average. The LeetCode population mixes human and AI-assisted submissions, and LeetCode does not separate them." sortKey="time_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="t vs exp" tip="Speedup against the EXPERT cohort on LeetCode (~top decile of the population distribution). leetcode_expert_runtime_ms / model_runtime_ms averaged across accepted attempts. ≥1.0× means that the model at least matches top LeetCode performers on time." sortKey="time_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="m vs avg" tip="Memory ratio vs. the AVERAGE LeetCode submission: leetcode_avg_memory_mb / model_memory_mb averaged across accepted attempts. >1.0× = the model uses less memory than the LeetCode average." sortKey="mem_human_avg" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                {SHOW_RELATIVE_METRICS && <Th label="m vs exp" tip="Memory ratio vs. the EXPERT LeetCode cohort. >1.0× = the model uses less memory than top LeetCode performers." sortKey="mem_human_expert" activeKey={sortKey} dir={sortDir} onSort={onSort} />}
                <Th label="Tokens" tip="Total tokens: sum of code-only response tokens across every attempt in this submission. It grows with the number of attempts (and thus with the number of problems covered). To compare the typical spend per problem, use 'tok/att'." sortKey="total_response_tokens" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="tok/att" tip="Median tokens per attempt: per-attempt median of response_tokens. You can compare it across submissions, whatever number of problems each one covers. It answers the question 'how many tokens does ONE solution from this model typically cost?'" sortKey="median_response_tokens_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Cost" tip="Total cost ($): sum of LLM cost across every attempt in this submission. When stored tokens are available, the platform recomputes the cost live from /models pricing × stored tokens. For legacy attempts, it uses the snapshot cost recorded at submit time. The cost is 0 for submissions that use only Ollama models." sortKey="total_cost_usd" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="$/att" tip="Median cost per attempt in USD. It gives a like-for-like 'typical cost per problem solved', whatever the submission size." sortKey="median_cost_usd_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="Gen time" tip="Total generation wall-clock time across every attempt in this submission. It grows with the number of attempts. To compare the typical latency per solution, use 'gen/att'." sortKey="total_generation_ms" activeKey={sortKey} dir={sortDir} onSort={onSort} />
                <Th label="gen/att" tip="Median generation time per attempt: per-attempt median of wall_clock_ms. It answers the question 'how long does this model typically take to generate ONE solution?', whatever the number of problems in the submission." sortKey="median_generation_ms_per_attempt" activeKey={sortKey} dir={sortDir} onSort={onSort} />
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
                      {!r.agent_slug || r.agent_slug === "direct"
                        ? <span className="muted">direct</span>
                        : r.agent_kind === "reference"
                          ? <ReferenceBadge version={r.model_version} />
                          : <span title={r.agent_slug}>{r.agent_name ?? r.agent_slug}</span>}
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
                    <td><Link to={`/submissions/${r.submission_id}`}>Details →</Link></td>
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
        <span className="muted">to</span>
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

/* Display formatter for the token columns. Null → dash so the legacy
 * submissions that didn't capture tokens don't render as "0" (0 means none,
 * dash means unknown). Tokens get a thousands separator and a "k" suffix
 * above 10k to keep the column narrow on a busy table. Dollar amounts use
 * formatDollar from lib/format. */
function formatTokenCount(v: number | null | undefined): string {
  if (v == null) return "–";
  if (v >= 10_000) return (v / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}
