import { Link } from "react-router-dom";
import { SubmissionPublic } from "../lib/api";
import { formatMemory, formatPassAt1, formatPercentile, formatRuntime } from "../lib/format";
import LanguageLink from "./LanguageLink";

export default function SubmissionsTable({ rows }: { rows: SubmissionPublic[] }) {
  if (rows.length === 0) return <div className="empty">No submissions to show.</div>;
  return (
    <div className="card tbl-scroll" style={{ padding: 0 }}>
      <table className="tbl">
        <thead><tr>
          <th>When</th><th>Model</th><th>Submitter</th><th>Lang</th>
          <th>Status</th><th>pass@1</th><th>Solved</th>
          <th title="Total attempts submitted (one Attempt = one code POST). Re-submissions for the same problem count.">Attempts</th>
          <th title="Mean LeetCode-reported runtime">Avg runtime</th>
          <th title="Mean LeetCode-reported memory">Avg memory</th>
          <th title="Avg LeetCode runtime percentile">Runtime %ile</th>
          <th title="Avg LeetCode memory percentile">Memory %ile</th><th></th>
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
              <td><span className="badge badge-status">{s.status}</span></td>
              <td className="metric">{formatPassAt1(s.pass_at_1)}</td>
              <td className="metric">{s.total_solved}/{s.total_problems}</td>
              <td className="metric">{s.n_attempts}</td>
              <td className="metric">{formatRuntime(s.avg_runtime_ms)}</td>
              <td className="metric">{formatMemory(s.avg_memory_mb)}</td>
              <td className="metric">{formatPercentile(s.avg_runtime_percentile)}</td>
              <td className="metric">{formatPercentile(s.avg_memory_percentile)}</td>
              <td><Link to={`/submissions/${s.id}`}>details →</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
