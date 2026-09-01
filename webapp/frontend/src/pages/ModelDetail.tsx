import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ModelDetail, ModelDetailLanguageStat } from "../lib/api";
import LanguageLink from "../components/LanguageLink";
import SubmissionsTable from "../components/SubmissionsTable";
import { formatMemory, formatPassAt1, formatPercentile, formatRuntime } from "../lib/format";

export default function ModelDetailPage() {
  const { name = "" } = useParams();
  const [m, setM] = useState<ModelDetail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.modelDetail(name).then(setM).catch((e: unknown) => setErr(String(e)));
  }, [name]);

  if (err) return <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>;
  if (!m) return <div className="empty">Loading…</div>;

  return (
    <div className="col">
      <div className="hero">
        <h1>{m.name}</h1>
        <p>
          versions: {m.versions.join(", ") || "–"} · languages: {m.languages.join(", ") || "–"} ·
          submitters: {m.submitters.join(", ") || "–"}
        </p>
      </div>

      <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
        <Stat label="submissions" value={m.n_submissions.toString()} />
        <Stat label="best pass@1" value={formatPassAt1(m.best_pass_at_1)} />
        <Stat label="best avg runtime %ile" value={formatPercentile(m.best_avg_runtime_percentile)} />
        <Stat label="best avg memory %ile" value={formatPercentile(m.best_avg_memory_percentile)} />
      </div>

      <ByLanguage rows={m.by_language} />

      <h2>All submissions for this model</h2>
      <SubmissionsTable rows={m.submissions} />
    </div>
  );
}

function ByLanguage({ rows }: { rows: ModelDetailLanguageStat[] }) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h2>By language</h2>
      <p className="muted" style={{ marginTop: 4 }}>
        How this model does in each language it was submitted in. "Best" takes
        the top value across that model's submissions in the language.
      </p>
      <div className="card tbl-scroll" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr>
            <th>Lang</th><th>Submissions</th>
            <th>Best pass@1</th>
            <th title="Lowest avg runtime across this model's submissions in the language">Best avg runtime</th>
            <th title="Lowest mean peak memory across this model's submissions in the language. Memory is peak (max resident) on both oracles, never per function call.">Best peak mem</th>
            <th title="Highest avg runtime percentile reached">RT %ile</th>
            <th title="Highest avg memory percentile reached">Mem %ile</th>
            <th>Fully accepted</th>
            <th title="Any of this model's submissions in the language is currently on the leaderboard">On leaderboard</th>
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.language}>
                <td><LanguageLink name={r.language} /></td>
                <td className="metric">{r.n_submissions}</td>
                <td className="metric">{formatPassAt1(r.best_pass_at_1)}</td>
                <td className="metric">{formatRuntime(r.best_avg_runtime_ms)}</td>
                <td className="metric">{formatMemory(r.best_avg_memory_mb)}</td>
                <td className="metric">{formatPercentile(r.best_avg_runtime_percentile)}</td>
                <td className="metric">{formatPercentile(r.best_avg_memory_percentile)}</td>
                <td>{r.fully_accepted_any
                  ? <span className="badge badge-easy">yes</span>
                  : <span className="badge badge-medium">no</span>}</td>
                <td>{r.leaderboard_eligible_any
                  ? <span className="badge badge-easy">yes</span>
                  : <span className="badge badge-medium">no</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
