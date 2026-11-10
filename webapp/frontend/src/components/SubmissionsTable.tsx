import { Link } from "react-router-dom";
import { SubmissionPublic } from "../lib/api";
import { formatDuration, formatEnergy, formatMemory, formatPassAt1, formatPercentile, formatRuntime } from "../lib/format";
import { SHOW_RELATIVE_METRICS } from "../lib/flags";
import LanguageLink from "./LanguageLink";
import PromptName from "./PromptName";

export default function SubmissionsTable({ rows }: { rows: SubmissionPublic[] }) {
  if (rows.length === 0) return <div className="empty">No submissions to show.</div>;
  // Energy is only meaningful when some submission in scope reported it.
  // LeetCode never does, so for pure-LC submission sets we hide the column
  // entirely rather than show a column of dashes.
  const showEnergy = rows.some(s => s.total_generation_energy_j != null);
  // Runtime energy (execution-side, distinct from the LLM-side "Gen energy"
  // column above) — surfaced only when at least one row was scored by a
  // harness that measured it. Hidden entirely for LC-only sets, same
  // pattern the generation-energy column uses. Two columns: Avg (per-
  // problem mean across accepted attempts) + Best (per-problem minimum).
  const showRuntimeEnergy = rows.some(s =>
    s.avg_runtime_energy_j != null || s.best_runtime_energy_j != null,
  );
  return (
    <div className="card tbl-scroll" style={{ padding: 0 }}>
      <table className="tbl">
        <thead><tr>
          <th title="Time of the most recent attempt on this submission. If the submission has no attempts yet, this is its creation time. The table sorts by this column, so an active sweep stays at the top while it still adds attempts.">When</th><th>Model</th><th>Submitter</th><th>Lang</th>
          <th title="The registered prompt template that this submission used (click to open it). 'ad-hoc' means that the submitter sent inline prompt text instead of a registry ID.">Prompt</th>
          <th title="The competition that this submission targeted. pass@1 and Solved use the problem set of this competition as the denominator. A dash marks a legacy row that was created before the per-submission scope existed.">Competition</th>
          <th title="The performance oracle (harness) that scored the runs of this submission. It is resolved per submission: the submission override first, then the competition default. The startup backfill resolves every legacy row to `leetcode`.">Harness</th>
          <th title="Correctness oracle: the verdict source (Accepted / Wrong Answer / RE / TLE) for pass@1. It is separate from the performance oracle, so one oracle can score a submission and another can measure it.">Correct</th>
          <th>Status</th><th>pass@1</th>
          <th title="Mean acceptance rate across submitted attempts: total accepted / total submitted. Only submitted attempts count in the denominator, so a partial submission is not penalized for problems it did not attempt. It differs from pass@1 when there are multiple samples per problem.">pass@k</th>
          <th>Solved</th>
          <th title="Total attempts submitted (one attempt = one code POST). Resubmissions for the same problem count.">Attempts</th>
          <th title="Mean LeetCode-reported runtime">Avg RT</th>
          <th title="Mean peak memory (MB) across runs. This is the peak (max resident) memory, not a per-function-call value, so it is valid on both oracles.">Peak mem</th>
          {SHOW_RELATIVE_METRICS && <th title="Avg LeetCode runtime percentile (relative to the reference population)">RT %ile</th>}
          {SHOW_RELATIVE_METRICS && <th title="Avg LeetCode memory percentile (relative to the reference population)">Mem %ile</th>}
          {showRuntimeEnergy && <th title="Avg runtime energy (Joules): mean of per-problem mean execution energy across accepted attempts. Reported only when the harness that scored this submission measures execution energy (an in-house runner with RAPL / NVML). Lower is better.">Avg E</th>}
          {showRuntimeEnergy && <th title="Best runtime energy (Joules): mean of the per-problem minimum execution energy across accepted attempts. Same source as Avg E.">Best E</th>}
          <th title="Total $ cost of all LLM calls in this submission. If the model has rates configured, the cost is recomputed live from /models pricing × stored tokens. Otherwise, it is the snapshot stored at submit time. 0 for Ollama and other local models.">Cost</th>
          <th title="Per-attempt median cost in USD: the typical $ per problem solved. It does not scale with the number of problems in the submission, so you can compare it across submissions.">$/att</th>
          <th title="Total code-only response tokens across all attempts.">Tokens</th>
          <th title="Per-attempt median response_tokens: the typical number of tokens for one solution from this model, independent of submission size.">tok/att</th>
          <th title="Total generation wall-clock across all attempts (sum of each solution's generation time)">Gen time</th>
          <th title="Per-attempt median generation time: the typical time that this model takes to generate one solution, independent of submission size.">gen/att</th>
          {showEnergy && <th title="Total generation energy across all attempts (Joules). Submit clients that have a power probe report it. LeetCode never reports it.">Gen E</th>}
          <th></th>
        </tr></thead>
        <tbody>
          {rows.map(s => {
            // Prefer the last-activity timestamp so a still-running sweep
            // keeps its row at the top. Falls back to creation time when
            // no attempts have been logged (just-created submission, or
            // legacy row from before this field was populated).
            const when = s.last_attempt_at ?? s.created_at;
            const isLive = s.last_attempt_at != null
              && s.last_attempt_at !== s.created_at;
            return (
            <tr key={s.id}>
              <td className="muted"
                  title={isLive
                    ? `Created ${new Date(s.created_at).toLocaleString()}\n` +
                      `Last attempt ${new Date(s.last_attempt_at!).toLocaleString()}`
                    : `Created ${new Date(s.created_at).toLocaleString()}`}>
                {new Date(when).toLocaleString()}
              </td>
              <td>
                <Link to={`/models/${encodeURIComponent(s.model_name)}`}>
                  <b>{s.model_name}</b>
                </Link>
                <span className="muted"> {s.model_version}</span>
              </td>
              <td>
                <Link to={`/submitters/${s.submitter_slug}`}>
                  {s.user_org ?? s.user_display_name}
                </Link>
              </td>
              <td><LanguageLink name={s.language} /></td>
              <td><PromptName id={s.prompt_id} name={s.prompt_name} /></td>
              <td>
                {s.competition_slug
                  ? <Link to={`/competitions/${s.competition_slug}`}
                          style={{
                            padding: "2px 8px", borderRadius: 999,
                            background: "var(--panel-2)",
                            border: "1px solid var(--border)",
                            fontSize: "0.8rem", whiteSpace: "nowrap",
                            textDecoration: "none",
                          }}>{s.competition_name ?? s.competition_slug}</Link>
                  : <span className="muted">—</span>}
              </td>
              <td>
                {s.harness_slug
                  ? <Link to={`/harnesses/${s.harness_slug}`}
                          style={{
                            padding: "2px 8px", borderRadius: 999,
                            background: "var(--panel-2)",
                            border: "1px solid var(--border)",
                            fontSize: "0.8rem", whiteSpace: "nowrap",
                            textDecoration: "none",
                          }}>{s.harness_name ?? s.harness_slug}</Link>
                  : <span className="muted">—</span>}
              </td>
              <td>
                {s.correctness_oracle_slug
                  ? <Link to={`/correctness-oracles/${s.correctness_oracle_slug}`}
                          style={{
                            padding: "2px 8px", borderRadius: 999,
                            background: "var(--panel-2)",
                            border: "1px solid var(--border)",
                            fontSize: "0.8rem", whiteSpace: "nowrap",
                            textDecoration: "none",
                          }}>{s.correctness_oracle_name ?? s.correctness_oracle_slug}</Link>
                  : <span className="muted">—</span>}
              </td>
              <td><span className="badge badge-status">{s.status}</span></td>
              <td className="metric">{formatPassAt1(s.pass_at_1)}</td>
              <td className="metric">{formatPassAt1(s.pass_at_k)}</td>
              <td className="metric">{s.total_solved}/{s.total_problems}</td>
              <td className="metric">{s.n_attempts}</td>
              <td className="metric">
                {formatRuntime(s.avg_runtime_ms)}
                {(s.machine_name != null || s.energy_tool != null) && s.avg_runtime_ms != null && (
                  <sup className="muted" style={{ fontSize: "0.68em", marginLeft: 1 }}>/call</sup>
                )}
              </td>
              <td className="metric">{formatMemory(s.avg_memory_mb)}</td>
              {SHOW_RELATIVE_METRICS && (
                <td className="metric">{formatPercentile(s.avg_runtime_percentile)}</td>
              )}
              {SHOW_RELATIVE_METRICS && (
                <td className="metric">{formatPercentile(s.avg_memory_percentile)}</td>
              )}
              {showRuntimeEnergy && (
                <td className="metric">{formatEnergy(s.avg_runtime_energy_j)}</td>
              )}
              {showRuntimeEnergy && (
                <td className="metric">{formatEnergy(s.best_runtime_energy_j)}</td>
              )}
              <td className="metric">{s.total_cost_usd == null ? "–" : `$${s.total_cost_usd.toFixed(4)}`}</td>
              <td className="metric">{
                s.median_cost_usd_per_attempt == null
                  ? "–"
                  : s.median_cost_usd_per_attempt < 0.01
                    ? `$${s.median_cost_usd_per_attempt.toFixed(4)}`
                    : `$${s.median_cost_usd_per_attempt.toFixed(3)}`
              }</td>
              <td className="metric">{s.total_response_tokens == null ? "–" : s.total_response_tokens.toLocaleString()}</td>
              <td className="metric">{
                s.median_response_tokens_per_attempt == null
                  ? "–"
                  : s.median_response_tokens_per_attempt.toLocaleString(undefined, { maximumFractionDigits: 0 })
              }</td>
              <td className="metric">{formatDuration(s.total_generation_ms)}</td>
              <td className="metric">{formatDuration(s.median_generation_ms_per_attempt)}</td>
              {showEnergy && (
                <td className="metric">{formatEnergy(s.total_generation_energy_j)}</td>
              )}
              <td><Link to={`/submissions/${s.id}`}>details →</Link></td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
