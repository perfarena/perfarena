import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, AttemptResultPayload, BaselineSummary, getApiKey, MePayload, SubmissionDetail, SubmissionPublic } from "../lib/api";
import {
  DASH, formatCoefficient, formatDuration, formatEnergy, formatMemory, formatMicros, formatPassAt1, formatPercentile, formatRuntime,
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
          A coefficient that includes a synthetic baseline is an estimate. A
          new submission auto-seeds a baseline for its (language, problem) pair.
          The auto-refresher upgrades a placeholder to real LeetCode data when
          an accepted submission and credentials are available.
          {isAdmin && (
            <div className="row" style={{ gap: 8, marginTop: 6, alignItems: "center" }}>
              <button className="secondary" disabled={busy} onClick={refresh}>
                {busy ? "Please wait." : "Refresh baselines now"}
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
        <b>On the leaderboard.</b> This submission is complete. It passes more than 25% of the supported problems.
      </div>
    );
  }
  const r = s.leaderboard_reason;
  let msg: string;
  if (r === "incomplete") {
    msg = `This submission needs at least one attempt for every supported problem. The model solved ${s.total_solved}/${s.total_problems}. The total number of attempts is ${s.n_attempts}. Add the missing slugs with /api/submissions/${s.id}/attempts.`;
  } else if (r === "pass-rate-too-low") {
    const pct = s.total_problems > 0 ? Math.round((s.total_solved / s.total_problems) * 100) : 0;
    msg = `The pass-rate ${pct}% does not exceed the 25% threshold. Correct the failed problems. Submit them again to increase the pass-rate.`;
  } else if (r === "not-judged") {
    msg = `The team still scores this submission. When dispatch finishes, the team evaluates it for the leaderboard.`;
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
    if (!confirm(`Delete submission #${id} and all its attempts and runs permanently? You can not undo this action.`)) return;
    try {
      await api.deleteSubmission(id);
      nav("/me");
    } catch (e: unknown) {
      setErr(String(e));
    }
  }

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!d) return <div className="empty">Please wait.</div>;
  const { submission: s } = d;
  // Locally-measured (arena-local / per-op) iff a machine or energy tool is
  // attached. Only then is runtime measured per single solve (per-op); on
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
        <Stat label="pass@1" tip="The Chen unbiased estimator at k=1. The team calculates the mean across all the supported problems. An unattempted problem contributes 0. This is the accuracy for one sample across the full problem set." value={formatPassAt1(s.pass_at_1)} />
        <Stat label="pass@k" tip="The mean acceptance rate across the submitted attempts: total accepted / total submitted. The calculation divides only by the attempts that the submitter sent. A partial submission does not get a penalty for an unattempted problem." value={formatPassAt1(s.pass_at_k)} />
        <Stat label="pass@full" tip="The best of k for each problem. The team calculates the mean across all the supported problems. This is the fraction of the full set that the submitter solved one time or more across the k samples." value={formatPassAt1(s.pass_at_full)} />
        <Stat label="solved" value={`${s.total_solved} / ${s.total_problems}`} />
        <Stat label="attempts" value={String(s.n_attempts)} />
        <Stat label="total cost"
              tip="The sum of the cost in dollars of every language model call in this submission (k samples × problems × rounds). The value is 0 for Ollama and local models. The value is blank when the client does not report it."
              value={s.total_cost_usd == null ? "–" : `$${s.total_cost_usd.toFixed(4)}`} />
        <Stat label="code tokens"
              tip="The sum of the code response tokens across all the attempts. This total excludes the reasoning tokens. The submit client gives a best estimate."
              value={s.total_response_tokens == null ? "–" : s.total_response_tokens.toLocaleString()} />
        <Stat label="total gen time"
              tip="The sum of the generation wall-clock time of every solution in this submission (k samples × problems × rounds). The value is blank when the client does not report it."
              value={formatDuration(s.total_generation_ms)} />
        <Stat label="avg gen time"
              tip="The mean generation wall-clock time for each attempt: total_gen_time / n_attempts. This is the cost for each attempt to produce one solution from this model, prompt, and language. It is independent of the number of samples or problems that the submitter sent."
              value={
                s.total_generation_ms == null || s.n_attempts === 0
                  ? "–"
                  : formatDuration(s.total_generation_ms / s.n_attempts)
              } />
        {s.total_generation_energy_j != null && (
          <Stat label="total gen energy"
                tip="The sum of the generation energy across all the attempts (Joules). The submit client reports this value only when it has a power probe. The value is absent when there is no data."
                value={formatEnergy(s.total_generation_energy_j)} />
        )}
        <Stat label={locallyMeasured ? "avg runtime / op" : "avg runtime"}
              tip={locallyMeasured
                ? "The time for one solve (per-op) under the looped curated methodology. The team measured this submission locally."
                : undefined}
              value={formatRuntime(s.avg_runtime_ms)} />
        <Stat label={locallyMeasured ? "best runtime / op" : "best runtime"}
              tip={locallyMeasured
                ? "The fastest per-op time (for one solve). The team measured this submission locally."
                : undefined}
              value={formatRuntime(s.best_runtime_ms)} />
        {s.avg_memory_mb != null && (
          <Stat label="peak mem"
                tip="The peak memory (maximum resident). It is the peak on both oracles. It is never per-op."
                value={formatMemory(s.avg_memory_mb)} />
        )}
        {s.best_memory_mb != null && (
          <Stat label="best peak mem" value={formatMemory(s.best_memory_mb)} />
        )}
        {s.avg_runtime_energy_j != null && (
          <Stat label="avg runtime energy"
                tip="The mean execution energy (Joules) across the accepted attempts. A harness with execution instruments reports this value, for example arena-local. A lower value is better."
                value={formatEnergy(s.avg_runtime_energy_j)} />
        )}
        {s.best_runtime_energy_j != null && (
          <Stat label="best runtime energy"
                tip="The least execution energy that any accepted attempt in this submission used. The source is the same as the avg runtime energy."
                value={formatEnergy(s.best_runtime_energy_j)} />
        )}
        {s.avg_energy_j_per_op != null && (
          <Stat label="energy / op"
                tip="The per-op runtime energy: Joules for one solve under the looped curated methodology. You can compare this energy figure across languages. A lower value is better."
                value={formatEnergy(s.avg_energy_j_per_op)} />
        )}
        {s.avg_time_us_per_op != null && (
          <Stat label="time / op"
                tip="The per-op runtime: the time for one solve under the looped curated methodology. It is the fair per-op time. The team normalizes it across languages."
                value={formatMicros(s.avg_time_us_per_op)} />
        )}
        {s.avg_iterations != null && (
          <Stat label="iters / measurement"
                tip="The mean number of solves for each measurement, the confidence count. More iterations give a more accurate per-op estimate."
                value={Math.round(s.avg_iterations).toLocaleString()} />
        )}
        <Stat label="avg runtime %ile" value={formatPercentile(s.avg_runtime_percentile)} />
        {s.avg_memory_percentile != null && (
          <Stat label="avg memory %ile" value={formatPercentile(s.avg_memory_percentile)} />
        )}
      </div>

      {/* Measurement provenance — which machine + energy tool these
          energy/time numbers came from. Only rendered when at least one
          provenance field is present; LeetCode-scored submissions leave all
          null (they ran on LeetCode's judges, so the machine is "unknown"). */}
      {(s.machine_name || s.energy_tool || s.run_uid) && (
        <div className="card" style={{ padding: 10, fontSize: "0.85rem" }}>
          <b>Measured on</b> machine <b>{s.machine_name ?? "unknown"}</b>
          {s.energy_tool && <> · energy tool <b>{s.energy_tool}</b></>}
          {s.avg_iterations != null && (
            <> · {Math.round(s.avg_iterations).toLocaleString()} solves / measurement</>
          )}
          {s.run_uid && (
            <> · run{" "}
              <code className="muted" title={s.run_uid}
                    style={{ fontSize: "0.78rem", maxWidth: 220,
                             display: "inline-block", overflow: "hidden",
                             textOverflow: "ellipsis", whiteSpace: "nowrap",
                             verticalAlign: "bottom" }}>
                {s.run_uid}
              </code>
            </>
          )}
          <div className="muted" style={{ marginTop: 4 }}>
            You can compare the energy and time figures only within one
            (energy&nbsp;tool, machine) pair.
          </div>
        </div>
      )}

      <p className="muted" style={{ marginTop: -4, fontSize: "0.85rem" }}>
        The team dispatches every problem to LeetCode 5 times and stores all the runs.
        "avg" is the mean of the runs of each problem. "best" is the fastest run
        or the run with the least memory of each problem. A submission-level number
        is the mean of these per-problem values across the accepted problems.
      </p>

      <h2 style={{ marginBottom: 0 }}>vs. LeetCode baselines</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        This is the ratio of the LeetCode cohort baseline to the model metric. <b>&gt; 1.0×</b> means
        the model does better than the cohort. <b>&lt; 1.0×</b> means the model does worse. The cohort
        is every person who submitted on LeetCode for that problem in the same language.
        It includes humans <em>and</em> AI-assisted submissions, because LeetCode does not separate them.
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
              not from the registry
            </span>
          )}
        </div>
        <p className="muted" style={{ marginTop: 4, fontSize: "0.85rem" }}>
          The team stored the exact text snapshot at the time of the submission.
          If someone edits the template in the registry later, this snapshot stays
          the version that the model saw.
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

      {/* Per-op measurement breakdown (one row per attempt's final run).
          Only rendered when the scoring harness reported per-op metrics
          (looped curated methodology) — LeetCode-scored submissions leave
          every field null and the whole section stays hidden. */}
      {d.runs.some(r => r.iterations != null || r.energy_j_per_op != null || r.time_us_per_op != null) && (
        <div className="col" style={{ gap: 6 }}>
          <h2 style={{ marginBottom: 0 }}>Per-op measurements</h2>
          <p className="muted" style={{ marginTop: 4, fontSize: "0.85rem" }}>
            The team normalizes energy and time to one solve under the looped curated
            methodology. You can compare these numbers across languages. <b>iters</b> is
            the solve count for each measurement (confidence).
          </p>
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>Problem</th><th>Status</th>
                <th title="the solves for the measurement (confidence)">iters</th>
                <th title="Joules for one solve">energy / op</th>
                <th title="the time for one solve (µs source)">time / op</th>
              </tr></thead>
              <tbody>
                {d.runs.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <Link to={`/problems/${r.problem_slug}`}><code>{r.problem_slug}</code></Link>
                    </td>
                    <td>
                      <span className={`badge ${r.status_msg === "Accepted" ? "badge-easy" : "badge-hard"}`}>
                        {r.status_msg}
                      </span>
                    </td>
                    <td className="metric">
                      {r.iterations == null ? DASH : r.iterations.toLocaleString()}
                    </td>
                    <td className="metric">{formatEnergy(r.energy_j_per_op)}</td>
                    <td className="metric">{formatMicros(r.time_us_per_op)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {attempts === null
        ? <div className="empty">Please wait.</div>
        : <AttemptsTable rows={attempts} perOp={locallyMeasured} />}

      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Each row is one attempt. Click <b>N runs</b> to see the per-run
        runtime and memory chart across the dispatch repeats and the full run
        table · <b>Error log</b> shows the LeetCode traceback and the failed
        testcase when a run fails · <b>Provenance</b> shows the exact prompt,
        the full response of the model, and its reasoning trace · <b>Log entry</b>
        is the append-only transparency-log row of that attempt (sequence, hashes,
        signature). Each entry has a hash chain to the previous entry, so anyone
        can detect a change.
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
