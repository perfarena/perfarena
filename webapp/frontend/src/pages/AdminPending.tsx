import { useEffect, useState, useCallback } from "react";
import { api, type PendingAttemptRow } from "../lib/api";

/* Admin-only "Pending" tab.
 *
 * Lists every attempt that has not finalized yet, across all submissions: the
 * backend queue. Each row shows its state (queued / Pending / Dispatch failed),
 * age, and any dispatch warning, and can be deleted without touching the DB. */
export default function AdminPending() {
  const [rows, setRows] = useState<PendingAttemptRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr("");
    api.adminPendingAttempts()
      .then(r => setRows(r.pending))
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function del(id: number) {
    if (!window.confirm(`Delete attempt ${id}? This removes it and its runs.`)) return;
    setBusy(id);
    setErr("");
    try {
      await api.deleteAttempt(id);
      setRows(rs => rs.filter(r => r.attempt_id !== id));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  function age(iso: string | null): string {
    if (!iso) return "-";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return `${Math.round(s)}s`;
    if (s < 5400) return `${Math.round(s / 60)}m`;
    return `${Math.round(s / 3600)}h`;
  }

  const byState: Record<string, number> = {};
  for (const r of rows) byState[r.state] = (byState[r.state] || 0) + 1;
  const summary = Object.entries(byState).map(([k, v]) => `${v} ${k}`).join(", ");

  return (
    <div>
      <h1>Pending attempts</h1>
      <p>
        Attempts that are not final yet (the backend queue). {rows.length} total
        {summary ? ` (${summary})` : ""}.{" "}
        <button onClick={load} disabled={loading}>
          {loading ? "Refreshing" : "Refresh"}
        </button>
      </p>
      {err && <p style={{ color: "crimson" }}>{err}</p>}
      {rows.length === 0 && !loading && <p>The queue is empty.</p>}
      {rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Attempt</th><th>Sub</th><th>Model</th><th>Lang</th>
              <th>Competition</th><th>Problem</th><th>State</th><th>Runs</th>
              <th>Age</th><th>Warning</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.attempt_id}>
                <td>{r.attempt_id}</td>
                <td>{r.submission_id}</td>
                <td>{r.model}</td>
                <td>{r.language}</td>
                <td>{r.competition_slug ?? "-"}</td>
                <td>{r.problem_slug}</td>
                <td>{r.state}</td>
                <td>{r.n_runs}</td>
                <td>{age(r.logged_at)}</td>
                <td title={r.dispatch_warning ?? ""}>
                  {r.dispatch_warning ? r.dispatch_warning.slice(0, 40) : ""}
                </td>
                <td>
                  <button
                    onClick={() => del(r.attempt_id)}
                    disabled={busy === r.attempt_id}
                    title="Admin: permanently delete this attempt and its runs"
                  >
                    {busy === r.attempt_id ? "..." : "Delete"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
