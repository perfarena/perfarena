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
  return (
    <div className="card tbl-scroll" style={{ padding: 0 }}>
      <table className="tbl">
        <thead><tr>
          <th>When</th><th>Model</th><th>Submitter</th><th>Lang</th>
          <th title="Registered prompt template used (clickable). 'ad-hoc' if the submitter passed inline prompt text instead of a registry id.">Prompt</th>
          <th>Status</th><th>pass@1</th><th>Solved</th>
          <th title="Total attempts submitted (one Attempt = one code POST). Re-submissions for the same problem count.">Attempts</th>
          <th title="Mean LeetCode-reported runtime">Avg runtime</th>
          <th title="Mean LeetCode-reported memory">Avg memory</th>
          <th title="Avg LeetCode runtime percentile">Runtime %ile</th>
          <th title="Avg LeetCode memory percentile">Memory %ile</th>
          <th title="Total $ cost of all LLM calls in this submission (0 for Ollama / local; blank if not reported)">Cost</th>
          <th title="Total code-only response tokens across all attempts">Tokens</th>
          <th title="Total generation wall-clock across all attempts (sum of each solution's generation time)">Gen time</th>
          {showEnergy && <th title="Total generation energy across all attempts (Joules). Reported by submit clients that have a power probe; LeetCode never reports it.">Gen energy</th>}
          <th></th>
        </tr></thead>
        <tbody>
          {rows.map(s => (
            <tr key={s.id}>
              <td className="muted">{new Date(s.created_at).toLocaleString()}</td>
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
              <td><span className="badge badge-status">{s.status}</span></td>
              <td className="metric">{formatPassAt1(s.pass_at_1)}</td>
              <td className="metric">{s.total_solved}/{s.total_problems}</td>
              <td className="metric">{s.n_attempts}</td>
              <td className="metric">{formatRuntime(s.avg_runtime_ms)}</td>
              <td className="metric">{formatMemory(s.avg_memory_mb)}</td>
              <td className="metric">{formatPercentile(s.avg_runtime_percentile)}</td>
              <td className="metric">{formatPercentile(s.avg_memory_percentile)}</td>
              <td className="metric">{s.total_cost_usd == null ? "–" : `$${s.total_cost_usd.toFixed(4)}`}</td>
              <td className="metric">{s.total_response_tokens == null ? "–" : s.total_response_tokens.toLocaleString()}</td>
              <td className="metric">{formatDuration(s.total_generation_ms)}</td>
              {showEnergy && (
                <td className="metric">{formatEnergy(s.total_generation_energy_j)}</td>
              )}
              <td><Link to={`/submissions/${s.id}`}>details →</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
