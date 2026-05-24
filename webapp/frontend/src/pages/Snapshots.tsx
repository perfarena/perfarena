import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, SnapshotSummary } from "../lib/api";

export default function Snapshots() {
  const [rows, setRows] = useState<SnapshotSummary[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.snapshots().then(setRows).catch((e: unknown) => setErr(String(e)));
  }, []);

  return (
    <div className="col">
      <div className="hero">
        <h1>Snapshots</h1>
        <p>Weekly frozen reports of the leaderboard — top models, top submitters, and overall benchmark health.</p>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)" }}>{err}</div>}

      {rows.length === 0 ? (
        <div className="empty">No snapshots yet. Snapshots are taken weekly by the admin.</div>
      ) : (
        <div className="card tbl-scroll" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr>
              <th>Label</th><th>Taken at</th>
              <th>Submissions</th><th>Distinct submitters</th><th>Distinct models</th><th>Languages</th>
              <th></th>
            </tr></thead>
            <tbody>
              {rows.map(s => (
                <tr key={s.id}>
                  <td><b>{s.label}</b></td>
                  <td className="muted">{new Date(s.taken_at).toLocaleString()}</td>
                  <td className="metric">{s.totals?.n_submissions ?? "–"}</td>
                  <td className="metric">{s.totals?.n_distinct_submitters ?? "–"}</td>
                  <td className="metric">{s.totals?.n_distinct_models ?? "–"}</td>
                  <td className="metric">{s.totals?.n_languages ?? "–"}</td>
                  <td><Link to={`/snapshots/${s.id}`}>report →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
