import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, AttemptResultPayload, BaselineSummary, getApiKey, MePayload, SubmissionDetail, SubmissionPublic } from "../lib/api";
import {
  formatCoefficient, formatDuration, formatEnergy, formatMemory, formatMicros, formatPassAt1, formatPercentile, formatRuntime,
} from "../lib/format";
import LanguageLink from "../components/LanguageLink";
import AttemptsTable from "../components/AttemptsTable";
import CompareByLevel from "../components/CompareByLevel";
import CompareByTag from "../components/CompareByTag";
import { CompareByLevelRow, CompareByTagRow } from "../lib/api";

function BaselineSourceChip({
  summary, isAdmin, onRefreshed,
}: {
  summary?: BaselineSummary;
  isAdmin: boolean;
  onRefreshed: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  if (!summary || summary.n_used === 0) return null;
  const real = summary.by_source["leetcode-distribution"] ?? 0;
  const syn  = summary.by_source["synthetic"] ?? 0;
  const missing = summary.by_source["missing"] ?? 0;
  const allReal = real > 0 && syn === 0 && missing === 0;
  const fresh = summary.newest_fetched_at
    ? new Date(summary.newest_fetched_at).toLocaleString()
    : null;

  async function refresh() {
    setBusy(true); setNote("");
    try {
      const r = await api.refreshBaselines();
      setNote(`Backfilled: ${r.real} real · ${r.synthetic_placeholders} synthetic · ${r.already_real} already real.`);
      onRefreshed();   // reload the submission so the chip reflects new sources
    } catch (e: unknown) { setNote(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="card" style={{
      padding: 10,
      borderColor: allReal ? "var(--easy)" : "var(--medium)",
      background: allReal
        ? "rgba(120,200,120,0.08)"
        : "rgba(220,180,80,0.08)",
      fontSize: "0.85rem",
    }}>
      <b>{allReal ? "Real baselines" : "Mixed / synthetic baselines"}</b> ·
      {" "}leetcode-distribution: <b>{real}</b>
      {" "}· synthetic placeholder: <b>{syn}</b>
      {missing > 0 && <> · missing: <b>{missing}</b></>}
      {" "}({summary.n_used} accepted problems in {summary.language})
      {fresh && <> · newest fetched {fresh}</>}
      {!allReal && (
        <div className="muted" style={{ marginTop: 4 }}>
          Coefficients that include synthetic baselines are estimates. New
          submissions auto-seed a baseline for their (language, problem) pair;
          the auto-refresher upgrades placeholders to real LeetCode data when
          an accepted submission + credentials are available.
          {isAdmin && (
            <div className="row" style={{ gap: 8, marginTop: 6, alignItems: "center" }}>
              <button className="secondary" disabled={busy} onClick={refresh}>
                {busy ? "Refreshing…" : "Refresh baselines now"}
              </button>
              {note && <span className="muted">{note}</span>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function LeaderboardBanner({ s }: { s: SubmissionPublic }) {
  if (s.leaderboard_eligible) {
    return (
      <div className="card" style={{ borderColor: "var(--easy)", background: "rgba(120,200,120,0.08)" }}>
        <b>On the leaderboard.</b> This submission is complete and passes more than 25% of supported problems.
      </div>
    );
  }
  const r = s.leaderboard_reason;
  let msg: string;
  if (r === "incomplete") {
    msg = `Not on the leaderboard yet: this submission needs at least one attempt for every supported problem. Solved ${s.total_solved}/${s.total_problems}; total attempts so far: ${s.n_attempts}. Append the missing slugs via /api/submissions/${s.id}/attempts.`;
  } else if (r === "pass-rate-too-low") {
    const pct = s.total_problems > 0 ? Math.round((s.total_solved / s.total_problems) * 100) : 0;
    msg = `Not on the leaderboard: pass-rate ${pct}% does not exceed the 25% threshold. Fix or re-submit failing problems to raise it.`;
  } else if (r === "not-judged") {
    msg = `Submission is still being scored: it will be evaluated for the leaderboard once dispatch finishes.`;
  } else {
    msg = `Not on the leaderboard.`;
  }
  return (
    <div className="card" style={{ borderColor: "var(--medium)", background: "rgba(220,180,80,0.08)" }}>
      <b>Not on leaderboard.</b> {msg}
    </div>
  );
}

export default function SubmissionDetailPage() {
  const { id = "" } = useParams();
  const [d, setD] = useState<SubmissionDetail | null>(null);
  const [attempts, setAttempts] = useState<AttemptResultPayload[] | null>(null);
  const [me, setMe] = useState<MePayload | null>(null);
  const [err, setErr] = useState("");
  const nav = useNavigate();

  function loadDetail() {
    api.submissionDetail(id).then(setD).catch((e: unknown) => setErr(String(e)));
  }

  useEffect(() => {
    loadDetail();
    // Per-attempt detail (with the full N-run arrays) drives the shared
    // AttemptsTable + RunsChart — same component the gated /submit/:id/attempts
    // page uses. The endpoint is public, so it works on this public page too.
    api.submissionAttempts(id).then(setAttempts).catch(() => setAttempts([]));
    if (getApiKey()) api.me().then(setMe).catch(() => setMe(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function onDelete() {
    if (!confirm(`Permanently delete submission #${id} and all its attempts/runs? This can't be undone.`)) return;
    try {
      await api.deleteSubmission(id);
      nav("/me");
    } catch (e: unknown) {
      setErr(String(e));
    }
  }

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!d) return <div className="empty">Loading…</div>;
  const { submission: s } = d;
  // Locally-measured (arena-local / per function call) iff a machine or energy tool is
  // attached. Only then is runtime measured per function call; on
  // LeetCode-scored submissions runtime is whole-run wall time. Memory is
  // peak on both oracles, so it is never gated on this.
  const locallyMeasured = s.energy_tool != null || s.machine_name != null;

  return (
    <div className="col">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12, alignItems: "flex-start" }}>
        <div>
          <h1 style={{ marginBottom: 4 }}>{s.model_name} <span className="muted">{s.model_version}</span></h1>
          <div className="muted">
            submitted {new Date(s.created_at).toLocaleString()} ·
            by {d.submitter.org ?? d.submitter.display_name} · <LanguageLink name={s.language} /> ·
            <span className="badge badge-status" style={{ marginLeft: 8 }}>{s.status}</span>
          </div>
        </div>
        {/* Delete is now admin-only: the platform is curated, so mutating the
            submission set is reserved for the team. The owner-can-delete path
            was retired alongside self-submission. */}
        {me?.role === "admin" && (
          <button className="secondary" onClick={onDelete}
                  style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>
            Delete submission
          </button>
        )}
      </div>

      <LeaderboardBanner s={s} />

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label={`k (samples)`} value={String(s.n_samples_per_problem)} />
        <Stat label="pass@1" tip="Chen unbiased estimator at k=1, averaged over ALL supported problems (unattempted problems contribute 0). Single-shot accuracy across the full problem set." value={formatPassAt1(s.pass_at_1)} />
        <Stat label="pass@k" tip="Mean acceptance rate across submitted attempts: total accepted / total submitted. Only divides by attempts that were actually shipped, so partial submissions aren't penalised for unattempted problems." value={formatPassAt1(s.pass_at_k)} />
        <Stat label="pass@full" tip="Best-of-k per problem, averaged over all supported problems. Fraction of the full set the submitter solved AT LEAST ONCE across their k samples." value={formatPassAt1(s.pass_at_full)} />
        <Stat label="solved" value={`${s.total_solved} / ${s.total_problems}`} />
        <Stat label="attempts" value={String(s.n_attempts)} />
        <Stat label="total cost"
              tip="Sum of the $ cost of every LLM call in this submission (k samples × problems × rounds). 0 for Ollama / local models; blank if not reported."
              value={s.total_cost_usd == null ? "–" : `$${s.total_cost_usd.toFixed(4)}`} />
        <Stat label="code tokens"
              tip="Sum of code-only response tokens across all attempts (excludes reasoning). Best-effort estimate from the submitting client."
              value={s.total_response_tokens == null ? "–" : s.total_response_tokens.toLocaleString()} />
        <Stat label="total gen time"
              tip="Sum of the generation wall-clock of every solution in this submission (k samples × problems × rounds). Blank if not reported."
              value={formatDuration(s.total_generation_ms)} />
        <Stat label="avg gen time"
              tip="Mean generation wall-clock per attempt: total_gen_time / n_attempts. The per-attempt cost of producing one solution from this model+prompt+language, in isolation from the number of samples or problems shipped."
              value={
                s.total_generation_ms == null || s.n_attempts === 0
                  ? "–"
                  : formatDuration(s.total_generation_ms / s.n_attempts)
              } />
        {s.total_generation_energy_j != null && (
          <Stat label="total gen energy"
                tip="Sum of generation energy across all attempts (Joules). Reported only when the submit client has a power probe; omitted when no data."
                value={formatEnergy(s.total_generation_energy_j)} />
        )}
        <Stat label={locallyMeasured ? "avg runtime / call" : "avg runtime"}
              tip={locallyMeasured
                ? "Time per function call under the looped curated methodology. This submission was measured locally."
                : undefined}
              value={formatRuntime(s.avg_runtime_ms)} />
        <Stat label={locallyMeasured ? "best runtime / call" : "best runtime"}
              tip={locallyMeasured
                ? "Fastest per function call time. This submission was measured locally."
                : undefined}
              value={formatRuntime(s.best_runtime_ms)} />
        {s.avg_memory_mb != null && (
          <Stat label="peak mem"
                tip="Peak (max resident) memory. Peak on both oracles, never per function call."
                value={formatMemory(s.avg_memory_mb)} />
        )}
        {s.best_memory_mb != null && (
          <Stat label="best peak mem" value={formatMemory(s.best_memory_mb)} />
        )}
        {s.avg_runtime_energy_j != null && (
          <Stat label="avg runtime energy"
                tip="Mean execution energy (Joules) across accepted attempts. Reported by execution-instrumented harnesses (e.g. arena-local). Lower is better."
                value={formatEnergy(s.avg_runtime_energy_j)} />
        )}
        {s.best_runtime_energy_j != null && (
          <Stat label="best runtime energy"
                tip="Least execution energy any accepted attempt in this submission consumed. Same source as avg runtime energy."
                value={formatEnergy(s.best_runtime_energy_j)} />
        )}
        {s.avg_energy_j_per_op != null && (
          <Stat label="energy / call"
                tip="Runtime energy per function call under the looped curated methodology — the cross-language-comparable energy figure. Lower is better."
                value={formatEnergy(s.avg_energy_j_per_op)} />
        )}
        {s.avg_time_us_per_op != null && (
          <Stat label="time / call"
                tip="Runtime per function call under the looped curated methodology — the fair time, normalized across languages."
                value={formatMicros(s.avg_time_us_per_op)} />
        )}
        {s.avg_iterations != null && (
          <Stat label="calls / measurement"
                tip="Mean number of function calls behind each measurement — the confidence count. More calls mean a tighter per-function-call estimate."
                value={Math.round(s.avg_iterations).toLocaleString()} />
        )}
        <Stat label="avg runtime %ile" value={formatPercentile(s.avg_runtime_percentile)} />
        {s.avg_memory_percentile != null && (
          <Stat label="avg memory %ile" value={formatPercentile(s.avg_memory_percentile)} />
        )}
      </div>

      {/* Oracles + measurement provenance. The two oracles (perf + correctness)
          are ALWAYS shown so a reader can see HOW the submission was scored.
          The machine / energy tool / measurement config appear only for
          locally-measured (arena-local) submissions — LeetCode-scored ones
          leave them null. */}
      <div className="card" style={{ padding: 10, fontSize: "0.85rem" }}>
        <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
          <span>
            <b>Perf oracle:</b>{" "}
            {s.harness_slug
              ? <Link to={`/harnesses/${s.harness_slug}`}>{s.harness_name ?? s.harness_slug}</Link>
              : (s.harness_name ?? "unknown")}
          </span>
          <span>
            <b>Correctness oracle:</b>{" "}
            {s.correctness_oracle_name ?? s.correctness_oracle_slug ?? "unknown"}
          </span>
          {(s.machine_name || s.energy_tool) && (
            <span>
              <b>Measured on:</b> machine <b>{s.machine_name ?? "unknown"}</b>
              {s.energy_tool && <> · energy tool <b>{s.energy_tool}</b></>}
            </span>
          )}
          {s.avg_iterations != null && (
            <span>{Math.round(s.avg_iterations).toLocaleString()} solves / measurement</span>
          )}
        </div>
        {s.run_uid && (
          <div className="muted" style={{ marginTop: 4 }}>
            run{" "}
            <code title={s.run_uid}
                  style={{ fontSize: "0.78rem", maxWidth: 260,
                           display: "inline-block", overflow: "hidden",
                           textOverflow: "ellipsis", whiteSpace: "nowrap",
                           verticalAlign: "bottom" }}>
              {s.run_uid}
            </code>
          </div>
        )}
        {(s.machine_name || s.energy_tool) && (
          <div className="muted" style={{ marginTop: 4 }}>
            Energy / time figures are only comparable within one
            (energy&nbsp;tool, machine) pair.
          </div>
        )}
      </div>

      <p className="muted" style={{ marginTop: -4, fontSize: "0.85rem" }}>
        Every problem is dispatched to LeetCode 5× and all runs are stored.
        "avg" is the mean of each problem's runs; "best" is each problem's fastest /
        leanest run. Submission-level numbers are the mean of those per-problem values
        across accepted problems.
      </p>

      <h2 style={{ marginBottom: 0 }}>vs. LeetCode baselines</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        Ratio of the LeetCode population baseline to the model metric. <b>&gt; 1.0×</b> means
        the model outperforms the cohort; <b>&lt; 1.0×</b> means it's worse. The cohort
        is everyone who's submitted on LeetCode for that problem in the same language,
        both humans <em>and</em> AI-assisted submissions, since LeetCode doesn't separate them.
      </p>
      <BaselineSourceChip summary={d.baselines} isAdmin={me?.role === "admin"}
                          onRefreshed={loadDetail} />
      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="time vs LeetCode avg" value={formatCoefficient(s.time_human_avg)} />
        <Stat label="time vs LeetCode expert" value={formatCoefficient(s.time_human_expert)} />
        <Stat label="memory vs LeetCode avg" value={formatCoefficient(s.mem_human_avg)} />
        <Stat label="memory vs LeetCode expert" value={formatCoefficient(s.mem_human_expert)} />
      </div>

      <div className="card">
        <h2>Model parameters</h2>
        <pre className="code">{JSON.stringify(d.model_params, null, 2)}</pre>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
          <h2 style={{ margin: 0 }}>Prompt template</h2>
          {s.prompt_id != null && s.prompt_name ? (
            <span className="muted" style={{ fontSize: "0.85rem" }}>
              from registry:{" "}
              <Link to={`/prompts/${s.prompt_id}`}><b><code>{s.prompt_name}</code></b></Link>
            </span>
          ) : (
            <span className="muted" style={{ fontSize: "0.85rem" }}>
              ad-hoc (not from registry)
            </span>
          )}
        </div>
        <p className="muted" style={{ marginTop: 4, fontSize: "0.85rem" }}>
          The exact text snapshot persisted at submission time. If the registry's
          template was edited later, this stays the version the model saw.
        </p>
        <pre className="code">{d.prompt_template}</pre>
      </div>

      {/* Per-difficulty breakdown for THIS submission. Same grouped-bar
          chart component the /compare page uses, just with a single
          model. Hidden when there are no attempts yet (loading) or zero
          attempts in any of Easy/Medium/Hard (likely a synthetic test
          submission against a non-standard problem set). */}
      {attempts && attempts.length > 0 && (() => {
        const byLevel = buildByLevelFromAttempts(attempts, s.model_name);
        const hasData = byLevel.some(r => r.n_attempted > 0);
        return hasData
          ? <CompareByLevel models={[s.model_name]} rows={byLevel} />
          : null;
      })()}

      {/* Per-tag radar charts (pass rate / avg runtime / avg memory)
          for THIS submission. Same component the /compare page uses,
          with a single-model legend. Hidden when no attempts carry
          tags (older problems that never had LeetCode tags scraped). */}
      {attempts && attempts.length > 0 && (() => {
        const byTag = buildByTagFromAttempts(attempts, s.model_name);
        return byTag.length > 0
          ? <CompareByTag models={[s.model_name]} rows={byTag} />
          : null;
      })()}

      {attempts === null
        ? <div className="empty">Loading attempts…</div>
        : <AttemptsTable rows={attempts} perOp={locallyMeasured} />}

      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Each row is one attempt. Click <b>N runs</b> to see the per-run
        runtime/memory chart across the dispatch repeats and the full run
        table · <b>Error log</b> surfaces LeetCode's traceback / failing
        testcase when a run failed · <b>Provenance</b> shows the exact prompt,
        the model's full response, and its reasoning trace · <b>Log entry</b>
        is that attempt's append-only transparency-log row (sequence, hashes,
        signature). The entries are hash-chained to the previous one, so any
        tampering is detectable.
      </p>
    </div>
  );
}

function Stat({ label, value, tip }: { label: string; value: string; tip?: string }) {
  return (
    <div className="card" style={{ minWidth: 140 }} title={tip}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}


/* Per-(level) breakdown for ONE submission, computed client-side from the
 * attempts array. Returns three rows (Easy/Medium/Hard) shaped exactly like
 * CompareByLevelRow so the same chart component the /compare page uses can
 * render them directly — the only difference is models.length === 1.
 *
 * Reduction rules mirror the backend's compare-by-level handler so the
 * numbers match anything the user might cross-check on /compare:
 *   * n_attempted / n_accepted: counted PER PROBLEM (a problem with 5
 *     retries still counts as 1 attempted; accepted if ANY attempt for it
 *     was Accepted)
 *   * perf metrics (runtime, memory, percentile): mean over each problem's
 *     "best" accepted attempt's reported value — failed attempts excluded
 *     so the average isn't dragged by errors
 *   * generator-side ($, tokens, time): per-problem totals (sum of every
 *     attempt's spend for the problem), then mean across problems —
 *     a model that retried 3 times for one problem pays 3× the per-call
 *     cost on that problem and the chart should reflect it
 */
function buildByLevelFromAttempts(
  attempts: AttemptResultPayload[], modelName: string,
): CompareByLevelRow[] {
  const LEVELS: ("Easy" | "Medium" | "Hard")[] = ["Easy", "Medium", "Hard"];
  // Group attempts by problem first, then bucket each problem by its level.
  const byProblem: Map<string, AttemptResultPayload[]> = new Map();
  const slugLevel: Map<string, string> = new Map();
  for (const a of attempts) {
    if (!byProblem.has(a.problem_slug)) byProblem.set(a.problem_slug, []);
    byProblem.get(a.problem_slug)!.push(a);
    slugLevel.set(a.problem_slug, a.level);
  }

  return LEVELS.map(level => {
    let n_attempted = 0, n_accepted = 0;
    const avgRT: number[] = [], avgMB: number[] = [];
    const avgRTP: number[] = [], avgMP: number[] = [];
    const avgEN: number[] = [];
    const costPerProblem: number[] = [];
    const tokensPerProblem: number[] = [];
    const wallPerProblem: number[] = [];

    for (const [slug, atts] of byProblem) {
      if (slugLevel.get(slug) !== level) continue;
      n_attempted++;
      const accepted = atts.filter(a => a.accepted);
      if (accepted.length) {
        n_accepted++;
        // Pick the "best" accepted attempt — highest accepted-runs share
        // and, tied on that, fastest avg runtime. Same picker the backend
        // uses in /api/compare so the numbers line up.
        const best = accepted.reduce((a, b) => {
          const sa = a.n_runs ? a.n_accepted_runs / a.n_runs : 0;
          const sb = b.n_runs ? b.n_accepted_runs / b.n_runs : 0;
          if (sa !== sb) return sa > sb ? a : b;
          const ra = a.avg_runtime_ms ?? Infinity;
          const rb = b.avg_runtime_ms ?? Infinity;
          return ra < rb ? a : b;
        });
        if (best.avg_runtime_ms != null) avgRT.push(best.avg_runtime_ms);
        if (best.avg_memory_mb != null) avgMB.push(best.avg_memory_mb);
        if (best.avg_runtime_percentile != null) avgRTP.push(best.avg_runtime_percentile);
        if (best.avg_memory_percentile != null) avgMP.push(best.avg_memory_percentile);
        if (best.avg_runtime_energy_j != null) avgEN.push(best.avg_runtime_energy_j);
      }
      // Generator-side spend — sum across ALL attempts for this problem
      // (whether accepted or not; LLM bills per call regardless).
      const costSum = atts.reduce((s, a) => s + (a.cost_usd ?? 0), 0);
      const tokSum  = atts.reduce((s, a) => s + (a.response_tokens ?? 0), 0);
      const wcSum   = atts.reduce((s, a) => s + (a.wall_clock_ms ?? 0), 0);
      // Only push when at least one attempt reported the metric — a
      // problem with zero instrumented attempts shouldn't drag the
      // average toward zero.
      if (atts.some(a => a.cost_usd != null))        costPerProblem.push(costSum);
      if (atts.some(a => a.response_tokens != null)) tokensPerProblem.push(tokSum);
      if (atts.some(a => a.wall_clock_ms != null))   wallPerProblem.push(wcSum);
    }

    const mean = (xs: number[]): number | null =>
      xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

    return {
      model_name: modelName,
      level,
      n_attempted,
      n_accepted,
      pass_rate: n_attempted ? n_accepted / n_attempted : 0,
      avg_runtime_ms: mean(avgRT),
      avg_memory_mb: mean(avgMB),
      avg_runtime_percentile: mean(avgRTP),
      avg_memory_percentile: mean(avgMP),
      avg_runtime_energy_j: mean(avgEN),
      avg_cost_usd: mean(costPerProblem),
      avg_response_tokens: mean(tokensPerProblem),
      avg_wall_clock_ms: mean(wallPerProblem),
    };
  });
}


/* Per-(tag) breakdown for ONE submission, computed client-side from
 * the attempts array. Returns one row per LeetCode tag that ANY problem
 * in the submission carries — the CompareByTag component then keeps
 * the top-N by attempt count and drops the long tail so the radar
 * stays legible.
 *
 * Reduction rules mirror the backend's `/api/compare` by_tag handler so
 * the numbers line up with what /compare?models=<this_model> shows:
 *   * a problem tagged "Array, Hash Table" contributes to BOTH spokes
 *   * n_attempted: bumped once per problem per tag, regardless of
 *     accept-status (so failed problems still show up as a spoke)
 *   * n_accepted: only when the problem's best attempt was Accepted
 *   * runtime / memory means: best accepted attempt's value, pushed
 *     onto each tag the problem carries
 */
function buildByTagFromAttempts(
  attempts: AttemptResultPayload[], modelName: string,
): CompareByTagRow[] {
  // First group attempts by problem so we can pick the "best" one per
  // problem before fanning out across its tags (mirrors the backend).
  const byProblem: Map<string, AttemptResultPayload[]> = new Map();
  const slugTags: Map<string, string[]> = new Map();
  for (const a of attempts) {
    if (!byProblem.has(a.problem_slug)) byProblem.set(a.problem_slug, []);
    byProblem.get(a.problem_slug)!.push(a);
    if (!slugTags.has(a.problem_slug)) {
      const tags = (a.tags || "")
        .split(",")
        .map(t => t.trim())
        .filter(Boolean);
      slugTags.set(a.problem_slug, tags);
    }
  }

  // Accumulator: tag -> running tallies.
  type Cell = {
    n_attempted: number; n_accepted: number;
    rts: number[]; mems: number[]; ens: number[];
  };
  const byTag: Map<string, Cell> = new Map();
  const ensure = (tag: string): Cell => {
    let c = byTag.get(tag);
    if (!c) {
      c = { n_attempted: 0, n_accepted: 0, rts: [], mems: [], ens: [] };
      byTag.set(tag, c);
    }
    return c;
  };

  for (const [slug, atts] of byProblem) {
    const tags = slugTags.get(slug) || [];
    if (tags.length === 0) continue;          // untagged problem — skipped
    for (const tag of tags) ensure(tag).n_attempted++;

    const accepted = atts.filter(a => a.accepted);
    if (accepted.length === 0) continue;
    // Pick the "best" accepted attempt — same picker as buildByLevel.
    const best = accepted.reduce((a, b) => {
      const sa = a.n_runs ? a.n_accepted_runs / a.n_runs : 0;
      const sb = b.n_runs ? b.n_accepted_runs / b.n_runs : 0;
      if (sa !== sb) return sa > sb ? a : b;
      const ra = a.avg_runtime_ms ?? Infinity;
      const rb = b.avg_runtime_ms ?? Infinity;
      return ra < rb ? a : b;
    });
    for (const tag of tags) {
      const cell = byTag.get(tag)!;
      cell.n_accepted++;
      if (best.avg_runtime_ms != null) cell.rts.push(best.avg_runtime_ms);
      if (best.avg_memory_mb != null) cell.mems.push(best.avg_memory_mb);
      if (best.avg_runtime_energy_j != null) cell.ens.push(best.avg_runtime_energy_j);
    }
  }

  const mean = (xs: number[]): number | null =>
    xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

  // Sorted by tag for stable axis order on the radar.
  return Array.from(byTag.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([tag, cell]) => ({
      model_name: modelName,
      tag,
      n_attempted: cell.n_attempted,
      n_accepted: cell.n_accepted,
      pass_rate: cell.n_attempted ? cell.n_accepted / cell.n_attempted : 0,
      avg_runtime_ms: mean(cell.rts),
      avg_memory_mb: mean(cell.mems),
      avg_runtime_energy_j: mean(cell.ens),
    }));
}
