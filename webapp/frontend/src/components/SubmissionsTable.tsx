import { Link } from "react-router-dom";
import { SubmissionPublic } from "../lib/api";
import { formatDuration, formatEnergy, formatMemory, formatPassAt1, formatPercentile, formatRuntime } from "../lib/format";
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
          <th title="Timestamp of the most recent attempt on this submission (falls back to creation time when nothing has been submitted yet). The table sorts by this so an active sweep stays at the top while it's still landing attempts.">When</th><th>Model</th><th>Submitter</th><th>Lang</th>
          <th title="Registered prompt template used (clickable). 'ad-hoc' if the submitter passed inline prompt text instead of a registry id.">Prompt</th>
          <th title="Competition this submission targeted. pass@1 and Solved are computed against THIS competition's problem set as the denominator. — = legacy row created before the per-submission scope existed.">Competition</th>
          <th title="Perf oracle (harness) that scored this submission's runs. Resolved per submission (submission override → competition default). Every legacy row resolves to `leetcode` via the startup backfill.">Harness</th>
          <th>Status</th><th>pass@1</th>
          <th title="Mean acceptance rate across submitted attempts: total accepted / total submitted. Divides only by attempts actually shipped, so partial submissions aren't penalised for unattempted problems. Differs from pass@1 when there are multiple samples per problem.">pass@k</th>
          <th>Solved</th>
          <th title="Total attempts submitted (one Attempt = one code POST). Re-submissions for the same problem count.">Attempts</th>
          <th title="Mean LeetCode-reported runtime">Avg runtime</th>
          <th title="Mean LeetCode-reported memory">Avg memory</th>
          <th title="Avg LeetCode runtime percentile">Runtime %ile</th>
          <th title="Avg LeetCode memory percentile">Memory %ile</th>
          {showRuntimeEnergy && <th title="Avg runtime energy (Joules) — mean of per-problem mean execution energy across accepted attempts. Reported only when the harness that scored this submission measures execution energy (an in-house runner with RAPL / NVML). Lower = better.">Avg energy</th>}
          {showRuntimeEnergy && <th title="Best runtime energy (Joules) — mean of per-problem MIN execution energy across accepted attempts. Same source as Avg energy.">Best energy</th>}
          <th title="Total $ cost of all LLM calls in this submission. Live-recomputed from /models pricing × stored tokens when the model has rates configured; falls back to the snapshot stamped at submit otherwise. 0 for Ollama / local.">Cost</th>
          <th title="Per-attempt median cost in USD. Apples-to-apples 'typical $ per problem solved' — doesn't scale with how many problems the submission covered.">Median $/att</th>
          <th title="Total code-only response tokens across all attempts.">Tokens</th>
          <th title="Per-attempt median response_tokens — typical 'how many tokens does one solution from this model cost?' regardless of submission size.">Median tok/att</th>
          <th title="Total generation wall-clock across all attempts (sum of each solution's generation time)">Gen time</th>
          <th title="Per-attempt median generation time. Apples-to-apples 'how long does ONE solution take this model to generate' regardless of submission size.">Median gen/att</th>
          {showEnergy && <th title="Total generation energy across all attempts (Joules). Reported by submit clients that have a power probe; LeetCode never reports it.">Gen energy</th>}
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
              <td><span className="badge badge-status">{s.status}</span></td>
              <td className="metric">{formatPassAt1(s.pass_at_1)}</td>
              <td className="metric">{formatPassAt1(s.pass_at_k)}</td>
              <td className="metric">{s.total_solved}/{s.total_problems}</td>
              <td className="metric">{s.n_attempts}</td>
              <td className="metric">{formatRuntime(s.avg_runtime_ms)}</td>
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
