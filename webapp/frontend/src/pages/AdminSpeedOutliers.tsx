import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, MePayload, SpeedOutlierRow } from "../lib/api";

/**
 * Admin-only — measurement-integrity review. Lists accepted attempts that ran
 * far faster than the median of the other accepted solutions for the same
 * (problem, language) pair. Such an attempt usually skipped the benchmark's
 * intended work (e.g. a closed-form CLBG checksum instead of allocating), so
 * its runtime/energy are meaningless even though the output is correct. This is
 * a review signal, not an auto-reject — a genuine optimization can also land
 * here, so each row is for a human to judge.
 */
export default function AdminSpeedOutliers() {
  const [me, setMe] = useState<MePayload | null>(null);
  const [rows, setRows] = useState<SpeedOutlierRow[]>([]);
  const [factor, setFactor] = useState(8);
  const [minPeers, setMinPeers] = useState(3);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true); setErr("");
    try {
      const r = await api.adminSpeedOutliers(factor, minPeers);
      setRows(r.flagged);
    } catch (e: unknown) { setErr(String(e)); }
    finally { setLoading(false); }
  }, [factor, minPeers]);

  useEffect(() => { api.me().then(setMe).catch(() => setMe(null)); }, []);
  useEffect(() => { refresh(); }, [refresh]);

  if (me && me.role !== "admin") {
    return <div className="card" style={{ borderColor: "var(--bad)" }}>Admins only.</div>;
  }

  return (
    <div className="col">
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>Too-fast outliers</h1>
        <p>
          Accepted attempts that ran at least <b>{factor}×</b> faster than the
          median of the other accepted solutions for the same problem +
          language. A solution this much faster has usually skipped the work the
          benchmark measures (for example, computing a CLBG checksum in closed
          form instead of actually allocating) — its runtime and energy are then
          meaningless even though the output passed. This is a signal to review,
          not proof: a genuinely clever optimization can land here too.
        </p>
      </div>

      <div className="row" style={{ gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label className="col" style={{ gap: 2 }}>
          <span className="muted">Min speed-up (×)</span>
          <input type="number" min={2} step={1} value={factor}
                 onChange={e => setFactor(Math.max(2, Number(e.target.value) || 2))}
                 style={{ width: 110 }} />
        </label>
        <label className="col" style={{ gap: 2 }}>
          <span className="muted">Min peers</span>
          <input type="number" min={1} step={1} value={minPeers}
                 onChange={e => setMinPeers(Math.max(1, Number(e.target.value) || 1))}
                 style={{ width: 110 }} />
        </label>
        <button onClick={refresh} disabled={loading}>{loading ? "…" : "Refresh"}</button>
        <span className="muted">{rows.length} flagged</span>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--bad)", whiteSpace: "pre-wrap" }}>{err}</div>}

      {!loading && rows.length === 0 && !err
        ? <div className="empty">No attempts exceed {factor}× the peer median (with ≥ {minPeers} peers).</div>
        : (
          <div className="card tbl-scroll" style={{ padding: 0 }}>
            <table className="tbl">
              <thead><tr>
                <th>Speed-up</th><th>Problem</th><th>Lang</th><th>Model</th>
                <th>Best runtime</th><th>Peer median</th><th>Peers</th><th>Submission</th>
              </tr></thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.attempt_id}>
                    <td><b style={{ color: "var(--bad)" }}>{r.ratio}×</b></td>
                    <td><Link to={`/problems/${r.problem_slug}`}>{r.problem_slug}</Link></td>
                    <td><code>{r.language}</code></td>
                    <td><Link to={`/models/${encodeURIComponent(r.model_name)}`}>{r.model_name}</Link></td>
                    <td>{r.best_runtime_ms.toLocaleString()} ms</td>
                    <td className="muted">{r.peer_median_ms.toLocaleString()} ms</td>
                    <td className="muted">{r.n_peers}</td>
                    <td><Link to={`/submissions/${r.submission_id}`}>#{r.submission_id}</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}
