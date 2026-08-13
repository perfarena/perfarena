import { Link } from "react-router-dom";
import { SubmissionPublic } from "../lib/api";
import { formatDuration, formatEnergy, formatMemory, formatPassAt1, formatPercentile, formatRuntime } from "../lib/format";
import LanguageLink from "./LanguageLink";
import PromptName from "./PromptName";

export default function SubmissionsTable({ rows }: { rows: SubmissionPublic[] }) {
  if (rows.length === 0) return <div className="empty">There are no submissions to show.</div>;
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
          <th title="The time of the most recent attempt on this submission. It falls back to the creation time when no attempt exists yet. The table sorts by this time, so an active sweep stays at the top while it still adds attempts.">When</th><th>Model</th><th>Submitter</th><th>Lang</th>
          <th title="The registered prompt template that this submission used. Select it to open the template. The value is 'ad-hoc' when the submitter passed inline prompt text instead of a registry id.">Prompt</th>
          <th title="The competition that this submission targeted. The system computes pass@1 and Solved against the problem set of this competition as the denominator. A dash marks a legacy row from before the per-submission scope existed.">Competition</th>
          <th title="The performance oracle (the harness) that scored the runs of this submission. The system resolves it for each submission. A submission override wins over the competition default. Every legacy row resolves to `leetcode` through the startup backfill.">Harness</th>
          <th title="The correctness oracle. This is the source of the verdict (Accepted, Wrong Answer, RE, or TLE) that drives pass@1. It is separate from the performance oracle. One oracle can score a submission, and another oracle can measure it.">Correctness</th>
          <th>Status</th><th>pass@1</th>
          <th title="The mean acceptance rate across the submitted attempts. It equals total accepted divided by total submitted. It divides only by the attempts that the submission sent, so unattempted problems do not lower the score. This differs from pass@1 when there are several samples for each problem.">pass@k</th>
          <th>Solved</th>
          <th title="The total attempts that the submission sent. One attempt is one code POST. A re-submission for the same problem also counts.">Attempts</th>
          <th title="The mean runtime that LeetCode reports.">Avg runtime</th>
          <th title="The mean peak memory in MB across the runs. This is peak memory (maximum resident). It is never per-op. It is correct on both oracles.">Peak mem</th>
          <th title="The average runtime percentile from LeetCode.">Runtime %ile</th>
          <th title="The average memory percentile from LeetCode.">Memory %ile</th>
          {showRuntimeEnergy && <th title="The average runtime energy in Joules. It is the mean of the per-problem mean execution energy across the accepted attempts. A harness reports this value only when it measures execution energy, for example an in-house runner with RAPL or NVML. A lower value is better.">Avg energy</th>}
          {showRuntimeEnergy && <th title="The best runtime energy in Joules. It is the mean of the per-problem minimum execution energy across the accepted attempts. It has the same source as the Avg energy column.">Best energy</th>}
          <th title="The total cost of all LLM calls in this submission, in dollars. The system recomputes it live from the /models pricing and the stored tokens when the model has rates. If the model has no rates, the system uses the snapshot from submit time. The cost is 0 for Ollama or local models.">Cost</th>
          <th title="The median cost for each attempt in USD. This is a fair measure of the typical cost for each problem solved. It does not change with the number of problems in the submission.">Median $/att</th>
          <th title="The total response tokens for code only across all attempts.">Tokens</th>
          <th title="The median response_tokens for each attempt. This shows the typical number of tokens for one solution from this model. It does not depend on the size of the submission.">Median tok/att</th>
          <th title="The total generation wall-clock time across all attempts. It is the sum of the generation time of each solution.">Gen time</th>
          <th title="The median generation time for each attempt. This is a fair measure of the time for this model to generate one solution. It does not depend on the size of the submission.">Median gen/att</th>
          {showEnergy && <th title="The total generation energy across all attempts, in Joules. A submit client reports this value only when it has a power probe. LeetCode never reports it.">Gen energy</th>}
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
                  <sup className="muted" style={{ fontSize: "0.68em", marginLeft: 1 }}>/op</sup>
                )}
              </td>
              <td className="metric">{formatMemory(s.avg_memory_mb)}</td>
              <td className="metric">{formatPercentile(s.avg_runtime_percentile)}</td>
              <td className="metric">{formatPercentile(s.avg_memory_percentile)}</td>
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
