import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, SnapshotDetail } from "../lib/api";
import { formatPassAt1, formatPercentile } from "../lib/format";
import LanguageLink from "../components/LanguageLink";
import PromptName from "../components/PromptName";

export default function SnapshotDetailPage() {
  const { id = "" } = useParams();
  const [s, setS] = useState<SnapshotDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.snapshotDetail(id).then(setS).catch((e: unknown) => setErr(String(e)));
  }, [id]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!s) return <div className="empty">Loading…</div>;

  const t = s.report.totals;
  return (
    <div className="col">
      <div className="hero">
        <h1>{s.label}</h1>
        <p>Captured {new Date(s.taken_at).toLocaleString()} · generated at {new Date(s.report.generated_at).toLocaleString()}</p>
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="submissions" value={String(t.n_submissions ?? 0)} />
        <Stat label="distinct submitters" value={String(t.n_distinct_submitters ?? 0)} />
        <Stat label="distinct models" value={String(t.n_distinct_models ?? 0)} />
        <Stat label="languages" value={String(t.n_languages ?? 0)} />
      </div>

      <div>
        <h2>Top models</h2>
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>#</th><th>Model</th><th>Submitter</th><th>Lang</th>
              <th title="Registered prompt template used at snapshot time. Pre-2026-05-28 snapshots may show 'ad-hoc' for all rows (field wasn't recorded yet).">Prompt</th>
              <th>pass@1</th><th>Solved</th><th>Runtime %ile</th><th>Memory %ile</th><th></th>
            </tr></thead>
            <tbody>
              {s.report.top_models.map(m => (
                <tr key={m.submission_id}>
                  <td>{m.rank}</td>
                  <td>
                    <Link to={`/models/${encodeURIComponent(m.model_name)}`}>
                      <b>{m.model_name}</b>
                    </Link>
                    <span className="muted"> {m.model_version}</span>
                  </td>
                  <td>{m.submitter}</td>
                  <td><LanguageLink name={m.language} /></td>
                  <td><PromptName id={m.prompt_id ?? null} name={m.prompt_name ?? null} /></td>
                  <td className="metric">{formatPassAt1(m.pass_at_1)}</td>
                  <td className="metric">{m.total_solved}/{m.total_problems}</td>
                  <td className="metric">{formatPercentile(m.avg_runtime_percentile)}</td>
                  <td className="metric">{formatPercentile(m.avg_memory_percentile)}</td>
                  <td><Link to={`/submissions/${m.submission_id}`}>details →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="row" style={{ alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px" }}>
          <h2>Top submitters</h2>
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr><th>Submitter</th><th>Submissions</th></tr></thead>
              <tbody>
                {s.report.top_submitters.map(r => (
                  <tr key={r.submitter}>
                    <td>{r.submitter}</td>
                    <td className="metric">{r.n_submissions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div style={{ flex: "1 1 320px" }}>
          <h2>Language breakdown</h2>
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr><th>Language</th><th>Submissions</th><th>Best pass@1</th></tr></thead>
              <tbody>
                {s.report.language_breakdown.map(r => (
                  <tr key={r.language}>
                    <td><LanguageLink name={r.language} /></td>
                    <td className="metric">{r.n_submissions}</td>
                    <td className="metric">{formatPassAt1(r.best_pass_at_1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ minWidth: 140 }}>
      <div className="muted" style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700 }} className="metric">{value}</div>
    </div>
  );
}
